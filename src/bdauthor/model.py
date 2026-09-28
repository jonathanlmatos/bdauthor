"""Data shared by probe, validate, mux and the CLI. No I/O, no printing."""

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum, StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any


class Media(StrEnum):
    DVD5 = "dvd5"
    DVD9 = "dvd9"
    BD25 = "bd25"
    BD50 = "bd50"

    @property
    def capacity_bytes(self) -> int:
        return _CAPACITY_BYTES[self]

    @property
    def label(self) -> str:
        return _LABELS[self]


# Sector-exact usable capacities (2048-byte sectors).
_CAPACITY_BYTES = {
    Media.DVD5: 4_700_372_992,
    Media.DVD9: 8_543_666_176,
    Media.BD25: 25_025_314_816,
    Media.BD50: 50_050_629_632,
}
_LABELS = {
    Media.DVD5: "DVD-5",
    Media.DVD9: "DVD-9",
    Media.BD25: "BD-25",
    Media.BD50: "BD-50",
}


@dataclass(frozen=True)
class VideoStream:
    index: int
    codec: str
    profile: str | None
    level: int | None  # H.264 level_idc, e.g. 41 for level 4.1
    width: int
    height: int
    pix_fmt: str | None
    bit_depth: int | None
    chroma: str | None  # e.g. "4:2:0"
    fps: Fraction | None
    interlaced: bool | None
    ref_frames: int | None
    bitrate: int | None  # bits per second
    start_time: float | None = None  # seconds, first timestamp in the container

    @property
    def level_label(self) -> str | None:
        return None if self.level is None else f"{self.level // 10}.{self.level % 10}"

    @property
    def rate_label(self) -> str | None:
        """Frame rate with scan type, e.g. "23.976p" or "25i"."""
        if self.fps is None:
            return None
        rate = f"{float(self.fps):.3f}".rstrip("0").rstrip(".")
        return rate + ("i" if self.interlaced else "p")


@dataclass(frozen=True)
class AudioStream:
    index: int
    codec: str
    profile: str | None
    channels: int | None
    sample_rate: int | None
    bit_depth: int | None
    bitrate: int | None  # bits per second
    language: str | None
    title: str | None
    start_time: float | None = None  # seconds, first timestamp in the container


@dataclass(frozen=True)
class SubtitleStream:
    index: int
    codec: str
    language: str | None
    title: str | None


@dataclass(frozen=True)
class OtherStream:
    """Attachments, cover art, data streams: ignored by validation and mux."""

    index: int
    kind: str
    codec: str | None


@dataclass(frozen=True)
class Chapter:
    start: float  # seconds
    end: float
    title: str | None


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    size_bytes: int
    duration: float | None  # seconds
    container: str
    video: tuple[VideoStream, ...]
    audio: tuple[AudioStream, ...]
    subtitles: tuple[SubtitleStream, ...]
    others: tuple[OtherStream, ...]
    chapters: tuple[Chapter, ...]


class Severity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    message: str
    stream_index: int | None = None

    @property
    def label(self) -> str:
        """Rule name plus stream index, e.g. "video #0"."""
        return self.rule if self.stream_index is None else f"{self.rule} #{self.stream_index}"


@dataclass(frozen=True)
class Report:
    media: Media
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    @property
    def passed(self) -> bool:
        return not self.errors


def to_jsonable(obj: Any) -> Any:
    """Convert model objects to plain JSON types (Fraction/Path/Enum aware)."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Fraction):
        return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(item) for item in obj]
    return obj
