"""Locate external tools and verify the runtime environment."""

import logging
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
TSMUXER_NAME = "tsMuxeR.exe" if sys.platform == "win32" else "tsMuxeR"
TSMUXER_VENDOR_PATH = REPO_ROOT / "vendor" / "tsmuxer" / TSMUXER_NAME
TSMUXER_HINT = (
    "Run `python scripts/bootstrap.py tsmuxer` to download it into vendor/tsmuxer/ "
    "(or put the tsMuxeR CLI, not the GUI, on PATH)"
)


@dataclass(frozen=True)
class DepStatus:
    name: str
    ok: bool
    version: str | None = None
    path: Path | None = None
    hint: str | None = None


def find_tsmuxer() -> Path | None:
    """Look in vendor/ first, then on PATH."""
    if TSMUXER_VENDOR_PATH.is_file():
        return TSMUXER_VENDOR_PATH
    found = shutil.which(TSMUXER_NAME)
    return Path(found) if found else None


def tsmuxer_version(path: Path) -> str | None:
    # tsMuxeR prints its banner (and usage) when run without arguments.
    cmd = [str(path)]
    log.debug("running: %s", shlex.join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("tsMuxeR failed to run: %s", exc)
        return None
    match = re.search(r"tsMuxeR version (\d+(?:\.\d+)*)", result.stdout)
    return match.group(1) if match else None


def check_tsmuxer() -> DepStatus:
    path = find_tsmuxer()
    if path is None:
        return DepStatus("tsMuxeR", ok=False, hint=TSMUXER_HINT)
    version = tsmuxer_version(path)
    if version is None:
        return DepStatus(
            "tsMuxeR",
            ok=False,
            path=path,
            hint=f"{path} was found but could not be executed",
        )
    return DepStatus("tsMuxeR", ok=True, version=version, path=path)


def check_pyav() -> DepStatus:
    try:
        import av
    except ImportError:
        return DepStatus("PyAV", ok=False, hint="Run `uv sync` to install dependencies")
    libavcodec = ".".join(str(n) for n in av.library_versions["libavcodec"])
    return DepStatus("PyAV", ok=True, version=f"{av.__version__} (libavcodec {libavcodec})")


def check_all() -> list[DepStatus]:
    return [check_pyav(), check_tsmuxer()]
