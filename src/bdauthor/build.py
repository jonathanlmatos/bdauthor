"""Orchestration: probe -> validate -> mux -> verify the resulting BDMV directory."""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bdauthor.model import Media, MediaInfo, Report
from bdauthor.mux.base import Muxer, MuxRequest, ProgressCallback
from bdauthor.probe import probe
from bdauthor.transcode import encode_ac3, validate_with_audio_transcode
from bdauthor.validate import validate

REQUIRED_FILES = (
    "index.bdmv",
    "MovieObject.bdmv",
    "PLAYLIST/00000.mpls",
    "CLIPINF/00000.clpi",
    "STREAM/00000.m2ts",
)
_DURATION_TOLERANCE = 0.02  # muxed duration may differ slightly from the container's


class BuildError(Exception):
    """The disc directory could not be created or failed verification."""


class CheckFailed(Exception):
    """The source is not Blu-ray compatible as it is."""

    def __init__(self, report: Report) -> None:
        super().__init__(f"{len(report.errors)} compatibility error(s)")
        self.report = report


@dataclass(frozen=True)
class BuildResult:
    bdmv_dir: Path
    size_bytes: int
    report: Report


def _gib(size: int) -> str:
    return f"{size / 2**30:.2f} GiB"


def prepare_output(output_dir: Path, *, force: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise BuildError(f"{output_dir} exists and is not a directory")
    generated = [output_dir / "BDMV", output_dir / "CERTIFICATE"]
    existing = [path for path in generated if path.exists()]
    if existing and not force:
        raise BuildError(f"{existing[0]} already exists; use --force to replace it")
    for path in existing:  # only what a build generates, never the rest of output_dir
        shutil.rmtree(path)
    output_dir.mkdir(parents=True, exist_ok=True)


def _directory_size(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def verify_output(bdmv_dir: Path, info: MediaInfo, media: Media) -> int:
    """Check the generated directory; returns its size in bytes."""
    missing = [name for name in REQUIRED_FILES if not (bdmv_dir / name).is_file()]
    if missing:
        raise BuildError(f"the muxer did not produce: {', '.join(missing)}")

    size = _directory_size(bdmv_dir)
    if size > media.capacity_bytes:
        raise BuildError(
            f"BDMV is {_gib(size)}, which exceeds {media.label} capacity "
            f"({_gib(media.capacity_bytes)})"
        )

    muxed = probe(bdmv_dir / "STREAM" / "00000.m2ts")
    if (len(muxed.video), len(muxed.audio), len(muxed.subtitles)) != (
        len(info.video),
        len(info.audio),
        len(info.subtitles),
    ):
        raise BuildError(
            "the muxed stream does not have the same tracks as the source "
            f"(video/audio/subtitle: {len(muxed.video)}/{len(muxed.audio)}/{len(muxed.subtitles)} "
            f"vs {len(info.video)}/{len(info.audio)}/{len(info.subtitles)})"
        )
    if info.duration and muxed.duration:
        if abs(muxed.duration - info.duration) > max(2.0, info.duration * _DURATION_TOLERANCE):
            raise BuildError(
                f"the muxed duration ({muxed.duration:.1f}s) differs from the source ({info.duration:.1f}s)"
            )
    return size


def build_disc(
    source: Path,
    output_dir: Path,
    media: Media,
    muxer: Muxer,
    *,
    force: bool = False,
    transcode_audio: bool = False,
    on_progress: ProgressCallback | None = None,
    on_transcode_progress: ProgressCallback | None = None,
) -> BuildResult:
    """Validate `source` and, if it is compatible, write `output_dir/BDMV`.

    With `transcode_audio`, incompatible audio streams are re-encoded to AC3 first; video and
    subtitles are never re-encoded.

    Raises ProbeError, CheckFailed, TranscodeError, BuildError or MuxError.
    """
    info = probe(source)
    if transcode_audio:
        report, plans = validate_with_audio_transcode(info, media)
    else:
        report, plans = validate(info, media), []
    if not report.passed:
        raise CheckFailed(report)

    prepare_output(output_dir, force=force)
    with tempfile.TemporaryDirectory(prefix="bdauthor-audio-") as tmp:
        replacements: dict[int, Path] = {}
        for plan in plans:
            dest = Path(tmp) / f"audio-{plan.source_index}.mkv"
            encode_ac3(info.path, plan, dest, on_transcode_progress)
            replacements[plan.source_index] = dest
        muxer.mux(
            MuxRequest(info=info, output_dir=output_dir, audio_replacements=replacements),
            on_progress,
        )

    bdmv_dir = output_dir / "BDMV"
    size = verify_output(bdmv_dir, info, media)
    return BuildResult(bdmv_dir=bdmv_dir, size_bytes=size, report=report)
