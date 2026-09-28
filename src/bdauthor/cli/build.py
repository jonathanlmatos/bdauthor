from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
from rich.text import Text

from bdauthor.build import BuildError, CheckFailed, build_disc
from bdauthor.cli._report import print_report
from bdauthor.deps import TSMUXER_HINT, find_tsmuxer
from bdauthor.exitcodes import ExitCode
from bdauthor.model import Media
from bdauthor.mux.base import MuxError
from bdauthor.mux.tsmuxer import TsMuxer
from bdauthor.probe import ProbeError

out = Console()
err = Console(stderr=True)


def _fail(message: str, code: ExitCode) -> typer.Exit:
    err.print(Text(message), soft_wrap=True)
    return typer.Exit(code)


def build(
    file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Input .mkv file.")
    ],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Directory where BDMV/ is created.")
    ],
    media: Annotated[
        Media, typer.Option("--media", "-m", help="Target disc; sets the capacity budget.")
    ] = Media.BD25,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing BDMV/ in the output directory.")
    ] = False,
) -> None:
    """Remux a compatible media file into a BDMV directory (no re-encoding)."""
    tsmuxer = find_tsmuxer()
    if tsmuxer is None:
        raise _fail(f"tsMuxeR not found. {TSMUXER_HINT}", ExitCode.MISSING_DEPENDENCY)

    progress = Progress(
        TextColumn("Muxing"), BarColumn(), TaskProgressColumn(), console=err, transient=True
    )
    task = progress.add_task("mux", total=100)
    try:
        with progress:
            result = build_disc(
                file,
                output,
                media,
                TsMuxer(tsmuxer),
                force=force,
                on_progress=lambda percent: progress.update(task, completed=percent),
            )
    except ProbeError as exc:
        raise _fail(str(exc), ExitCode.CHECK_FAILED) from exc
    except CheckFailed as exc:
        print_report(exc.report, out, name=file.name)
        raise typer.Exit(ExitCode.CHECK_FAILED) from exc
    except (BuildError, MuxError) as exc:
        raise _fail(str(exc), ExitCode.BUILD_FAILED) from exc

    print_report(result.report, out, name=file.name)
    out.print(Text(f"Built {result.bdmv_dir} ({result.size_bytes / 2**30:.2f} GiB)"), soft_wrap=True)
