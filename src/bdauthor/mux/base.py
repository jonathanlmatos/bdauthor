from collections.abc import Callable
from dataclasses import dataclass
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


class Muxer(Protocol):
    def mux(self, request: MuxRequest, on_progress: ProgressCallback | None = None) -> None: ...
