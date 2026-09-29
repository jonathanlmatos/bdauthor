from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
from rich.text import Text

from bdauthor.build import BuildError, CheckFailed, build_disc
from bdauthor.cli._options import MediaOption, TranscodeAudioOption
from bdauthor.cli._report import print_report
from bdauthor.deps import TSMUXER_HINT, find_tsmuxer
from bdauthor.exitcodes import ExitCode
from bdauthor.menu import MenuError
from bdauthor.model import Media
from bdauthor.mux.base import MuxError
from bdauthor.mux.tsmuxer import TsMuxer
from bdauthor.probe import ProbeError
from bdauthor.transcode import TranscodeError

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
    media: MediaOption = Media.BD25,
    transcode_audio: TranscodeAudioOption = False,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing BDMV/ in the output directory.")
    ] = False,
    menu: Annotated[
        bool,
        typer.Option("--menu", help="Add a menu with a Play button that the disc opens with (experimental)."),
    ] = False,
    menu_title: Annotated[
        Optional[str],
        typer.Option("--menu-title", help="Movie name shown above the Play button; requires --menu."),
    ] = None,
) -> None:
    """Remux a compatible media file into a BDMV directory (video is never re-encoded)."""
    tsmuxer = find_tsmuxer()
    if tsmuxer is None:
        raise _fail(f"tsMuxeR not found. {TSMUXER_HINT}", ExitCode.MISSING_DEPENDENCY)

    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=err,
        transient=True,
    )
    transcode_task = progress.add_task("Re-encoding audio", total=100, visible=False)
    mux_task = progress.add_task("Muxing", total=100, visible=False)
    try:
        with progress:
            result = build_disc(
                file,
                output,
                media,
                TsMuxer(tsmuxer),
                force=force,
                transcode_audio=transcode_audio,
                menu=menu,
                menu_title=menu_title,
                on_progress=lambda percent: progress.update(mux_task, completed=percent, visible=True),
                on_transcode_progress=lambda percent: progress.update(
                    transcode_task, completed=percent, visible=True
                ),
            )
    except ProbeError as exc:
        raise _fail(str(exc), ExitCode.CHECK_FAILED) from exc
    except CheckFailed as exc:
        print_report(exc.report, out, name=file.name)
        raise typer.Exit(ExitCode.CHECK_FAILED) from exc
    except MenuError as exc:
        raise _fail(f"menu: {exc}", ExitCode.BUILD_FAILED) from exc
    except (BuildError, MuxError, TranscodeError) as exc:
        raise _fail(str(exc), ExitCode.BUILD_FAILED) from exc

    print_report(result.report, out, name=file.name)
    out.print(Text(f"Built {result.bdmv_dir} ({result.size_bytes / 2**30:.2f} GiB)"), soft_wrap=True)
