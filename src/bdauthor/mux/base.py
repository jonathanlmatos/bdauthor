from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from bdauthor.model import MediaInfo

ProgressCallback = Callable[[float], None]  # percent, 0-100


class MuxError(Exception):
    """The muxer could not produce the BDMV directory."""


@dataclass(frozen=True)
class MuxRequest:
    info: MediaInfo  # already validated
    output_dir: Path  # BDMV/ is created inside it
    # Probe audio-stream index -> audio-only file that replaces that stream.
    audio_replacements: Mapping[int, Path] = field(default_factory=dict)
    # .sup files (presentation graphics) to mux as extra tracks, after the source's own tracks.
    graphics: tuple[Path, ...] = ()


class Muxer(Protocol):
    def mux(self, request: MuxRequest, on_progress: ProgressCallback | None = None) -> None: ...
