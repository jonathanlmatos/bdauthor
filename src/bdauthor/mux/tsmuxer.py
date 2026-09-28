"""Muxer backend that drives the tsMuxeR command-line tool."""

import logging
import re
import shlex
import subprocess
import tempfile
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bdauthor.model import AudioStream, Chapter, MediaInfo
from bdauthor.mux.base import MuxError, MuxRequest, ProgressCallback

log = logging.getLogger(__name__)

_PROGRESS = re.compile(r"^\s*(\d+(?:\.\d+)?)% complete")
_SUCCESS = "Mux successful complete"
_MUXOPT = "--no-pcr-on-video-pid --new-audio-pes --blu-ray --vbr"
_H264 = "V_MPEG4/ISO/AVC"
_AC3 = "A_AC3"
_PGS = "S_HDMV/PGS"
_KINDS = {"V": "video", "A": "audio", "S": "subtitle"}
# tsMuxeR stream id that reads each probe audio codec; codecs missing here (flac, opus...) are not listed.
_AUDIO_FAMILY = {"aac": "A_AAC", "mp3": "A_MP3", "ac3": _AC3, "eac3": _AC3, "truehd": _AC3, "dts": "A_DTS"}


@dataclass(frozen=True)
class Track:
    """A track as listed by tsMuxeR's detection mode."""

    id: int | None  # None for a raw elementary stream (a .sup file), which has no track number
    stream_id: str  # e.g. "V_MPEG4/ISO/AVC", "A_AC3", "S_HDMV/PGS"
    language: str | None

    @property
    def kind(self) -> str | None:
        return _KINDS.get(self.stream_id[:1])


@dataclass(frozen=True)
class Entry:
    """One line of the meta file: a track of a file, optionally shifted in time."""

    path: Path
    track: Track
    timeshift_ms: int = 0


def parse_tracks(output: str) -> list[Track]:
    """Parse the output of `tsMuxeR <media file>` into tracks."""
    tracks = []
    for block in output.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key.strip()] = value.strip()
        if "Track ID" in fields and "Stream ID" in fields:
            lang = fields.get("Stream lang") or None
            tracks.append(Track(int(fields["Track ID"]), fields["Stream ID"], lang))
    return tracks


def _timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _chapter_option(chapters: tuple[Chapter, ...]) -> str:
    starts = sorted({round(chapter.start * 1000) for chapter in chapters})
    if starts and starts[0] != 0:
        starts.insert(0, 0)
    if len(starts) < 2:
        return ""
    return " --custom-chapters=" + ";".join(_timestamp(ms / 1000) for ms in starts)


def build_meta(entries: list[Entry], chapters: tuple[Chapter, ...] = ()) -> str:
    """Render the tsMuxeR meta file: one line per entry, in order."""
    lines = [f"MUXOPT {_MUXOPT}{_chapter_option(chapters)}"]
    for entry in entries:
        track = entry.track
        if '"' in str(entry.path):
            raise MuxError(f"file names containing double quotes are not supported: {entry.path}")
        params = [] if track.id is None else [f"track={track.id}"]
        if track.stream_id == _H264:
            params = ["insertSEI", "contSPS", *params]
        if track.language and len(track.language) == 3:
            params.append(f"lang={track.language}")
        if entry.timeshift_ms:
            params.append(f"timeshift={entry.timeshift_ms}ms")
        lines.append(f'{track.stream_id}, "{entry.path}", {", ".join(params)}')
    return "\n".join(lines) + "\n"


def _audio_family(codec: str) -> str | None:
    return "A_LPCM" if codec.startswith("pcm_") else _AUDIO_FAMILY.get(codec)


def _mismatch(tracks: list[Track], info: MediaInfo) -> MuxError:
    found = {kind: sum(1 for t in tracks if t.kind == kind) for kind in _KINDS.values()}
    expected = {"video": len(info.video), "audio": len(info.audio), "subtitle": len(info.subtitles)}
    return MuxError(
        "tsMuxeR detected different tracks than the probe "
        f"(tsMuxeR: {found}, probe: {expected}); refusing to guess which to mux"
    )


def _check_tracks(tracks: list[Track], info: MediaInfo) -> None:
    """Video and subtitle tracks must match the probe one to one."""
    for kind, count in (("video", len(info.video)), ("subtitle", len(info.subtitles))):
        if sum(1 for t in tracks if t.kind == kind) != count:
            raise _mismatch(tracks, info)


def _timeshift_ms(stream: AudioStream, info: MediaInfo) -> int:
    """Audio delay relative to the video, which tsMuxeR ignores when reading an mkv."""
    if not info.video or info.video[0].start_time is None or stream.start_time is None:
        return 0
    return round((stream.start_time - info.video[0].start_time) * 1000)


def plan_tracks(
    tracks: list[Track], info: MediaInfo, replacements: Mapping[int, tuple[Path, Track]]
) -> list[Entry]:
    """Pair every detected track with its file, swapping in the replaced audio streams.

    Audio tracks are matched to probe audio streams in order; a replaced stream's detected
    track (if tsMuxeR can read that codec at all) is dropped in favour of the replacement.
    Audio entries carry the delay the source had relative to the video.
    """
    source = info.path.resolve()
    detected_audio = iter([t for t in tracks if t.kind == "audio"])
    audio_entries: list[Entry] = []
    for stream in info.audio:
        family = _audio_family(stream.codec)
        detected = next(detected_audio, None) if family else None
        shift = _timeshift_ms(stream, info)
        if stream.index in replacements:
            if family and (detected is None or detected.stream_id != family):
                raise _mismatch(tracks, info)
            path, track = replacements[stream.index]
            audio_entries.append(Entry(path, track, shift))
        elif detected is None:
            raise _mismatch(tracks, info)
        else:
            audio_entries.append(Entry(source, detected, shift))
    if next(detected_audio, None) is not None:
        raise _mismatch(tracks, info)

    entries: list[Entry] = []
    audio_placed = False
    for track in tracks:
        if track.kind != "audio":
            entries.append(Entry(source, track))
        elif not audio_placed:
            entries.extend(audio_entries)
            audio_placed = True
    if not audio_placed:
        entries.extend(audio_entries)
    return entries


class TsMuxer:
    def __init__(self, executable: Path) -> None:
        self.executable = executable

    def detect(self, source: Path) -> list[Track]:
        cmd = [str(self.executable), str(source)]
        log.debug("running: %s", shlex.join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MuxError(f"could not run tsMuxeR: {exc}") from exc
        if result.returncode != 0:
            raise MuxError(f"tsMuxeR failed to read {source}:\n{result.stdout}{result.stderr}".strip())
        return parse_tracks(result.stdout)

    def _detect_replacement(self, path: Path) -> tuple[Path, Track]:
        audio = [t for t in self.detect(path) if t.kind == "audio"]
        if len(audio) != 1 or audio[0].stream_id != _AC3:
            raise MuxError(f"expected exactly one AC3 track in {path}, found {audio}")
        return path.resolve(), audio[0]

    def mux(self, request: MuxRequest, on_progress: ProgressCallback | None = None) -> None:
        info = request.info
        tracks = self.detect(info.path.resolve())
        _check_tracks(tracks, info)
        replacements = {
            index: self._detect_replacement(path)
            for index, path in request.audio_replacements.items()
        }
        entries = plan_tracks(tracks, info, replacements)
        for graphics in request.graphics:  # raw .sup files: tsMuxeR lists no tracks for them
            entries.append(Entry(graphics.resolve(), Track(None, _PGS, None)))
        meta = build_meta(entries, info.chapters)
        log.debug("meta file:\n%s", meta.rstrip())

        with tempfile.TemporaryDirectory(prefix="bdauthor-") as tmp:
            meta_path = Path(tmp) / "mux.meta"
            meta_path.write_text(meta, encoding="utf-8")
            self._run(meta_path, request.output_dir, on_progress)

    def _run(self, meta_path: Path, output_dir: Path, on_progress: ProgressCallback | None) -> None:
        cmd = [str(self.executable), str(meta_path), str(output_dir)]
        log.debug("running: %s", shlex.join(cmd))
        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
            )
        except OSError as exc:
            raise MuxError(f"could not run tsMuxeR: {exc}") from exc

        tail: deque[str] = deque(maxlen=15)
        succeeded = False
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            log.debug("tsMuxeR: %s", line)
            tail.append(line)
            if _SUCCESS in line:
                succeeded = True
            match = _PROGRESS.match(line)
            if match and on_progress:
                on_progress(float(match.group(1)))
        returncode = process.wait()

        if returncode != 0 or not succeeded:
            raise MuxError(f"tsMuxeR failed (exit code {returncode}):\n" + "\n".join(tail))
