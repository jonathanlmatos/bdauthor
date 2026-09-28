"""Factories for model objects; defaults describe a BD-compatible 1080p24 file."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from bdauthor.model import AudioStream, MediaInfo, SubtitleStream, VideoStream

_VIDEO = VideoStream(
    index=0,
    codec="h264",
    profile="High",
    level=41,
    width=1920,
    height=1080,
    pix_fmt="yuv420p",
    bit_depth=8,
    chroma="4:2:0",
    fps=Fraction(24000, 1001),
    interlaced=False,
    ref_frames=4,
    bitrate=None,
)
_AUDIO = AudioStream(
    index=1,
    codec="ac3",
    profile=None,
    channels=6,
    sample_rate=48000,
    bit_depth=None,
    bitrate=640_000,
    language="eng",
    title=None,
)
_SUBTITLE = SubtitleStream(index=2, codec="hdmv_pgs_subtitle", language="eng", title=None)


def video(**overrides) -> VideoStream:
    return replace(_VIDEO, **overrides)


def audio(**overrides) -> AudioStream:
    return replace(_AUDIO, **overrides)


def subtitle(**overrides) -> SubtitleStream:
    return replace(_SUBTITLE, **overrides)


def media_info(
    *,
    videos: tuple[VideoStream, ...] | None = None,
    audios: tuple[AudioStream, ...] | None = None,
    subtitles: tuple[SubtitleStream, ...] = (),
    size_bytes: int = 4 * 2**30,
    duration: float | None = 7200.0,
    container: str = "matroska,webm",
) -> MediaInfo:
    return MediaInfo(
        path=Path("movie.mkv"),
        size_bytes=size_bytes,
        duration=duration,
        container=container,
        video=(video(),) if videos is None else videos,
        audio=(audio(),) if audios is None else audios,
        subtitles=subtitles,
        others=(),
        chapters=(),
    )
