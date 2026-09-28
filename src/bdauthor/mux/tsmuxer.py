"""Muxer backend that drives the tsMuxeR command-line tool."""

import logging
import re
import shlex
import subprocess
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from bdauthor.model import Chapter, MediaInfo
from bdauthor.mux.base import MuxError, MuxRequest, ProgressCallback

log = logging.getLogger(__name__)

_PROGRESS = re.compile(r"^\s*(\d+(?:\.\d+)?)% complete")
_SUCCESS = "Mux successful complete"
_MUXOPT = "--no-pcr-on-video-pid --new-audio-pes --blu-ray --vbr"
_H264 = "V_MPEG4/ISO/AVC"
_KINDS = {"V": "video", "A": "audio", "S": "subtitle"}


@dataclass(frozen=True)
class Track:
    """A track as listed by tsMuxeR's detection mode."""

    id: int
    stream_id: str  # e.g. "V_MPEG4/ISO/AVC", "A_AC3", "S_HDMV/PGS"
    language: str | None

    @property
    def kind(self) -> str | None:
        return _KINDS.get(self.stream_id[:1])


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


def build_meta(source: Path, tracks: list[Track], chapters: tuple[Chapter, ...] = ()) -> str:
    """Render the tsMuxeR meta file that remuxes every track of `source` to a BD folder."""
    if '"' in str(source):
        raise MuxError(f"file names containing double quotes are not supported: {source}")

    lines = [f"MUXOPT {_MUXOPT}{_chapter_option(chapters)}"]
    for track in tracks:
        params = [f"track={track.id}"]
        if track.stream_id == _H264:
            params = ["insertSEI", "contSPS", *params]
        if track.language and len(track.language) == 3:
            params.append(f"lang={track.language}")
        lines.append(f'{track.stream_id}, "{source}", {", ".join(params)}')
    return "\n".join(lines) + "\n"


def _check_tracks(tracks: list[Track], info: MediaInfo) -> None:
    expected = {"video": len(info.video), "audio": len(info.audio), "subtitle": len(info.subtitles)}
    found = {kind: sum(1 for t in tracks if t.kind == kind) for kind in expected}
    if found != expected:
        raise MuxError(
            "tsMuxeR detected different tracks than the probe "
            f"(tsMuxeR: {found}, probe: {expected}); refusing to guess which to mux"
        )


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

    def mux(self, request: MuxRequest, on_progress: ProgressCallback | None = None) -> None:
        info = request.info
        source = info.path.resolve()
        tracks = self.detect(source)
        _check_tracks(tracks, info)
        meta = build_meta(source, tracks, info.chapters)
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
