"""Process exit codes. Code 2 is reserved for usage errors (Click/Typer default)."""

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    CHECK_FAILED = 1
    MISSING_DEPENDENCY = 3
    BUILD_FAILED = 4
