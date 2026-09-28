from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
from rich.text import Text

from bdauthor.cli._options import MediaOption
from bdauthor.exitcodes import ExitCode
from bdauthor.iso import IsoError, build_iso
from bdauthor.model import Media

out = Console()
err = Console(stderr=True)


def iso(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True, file_okay=False, readable=True, help="Directory containing BDMV/ (or BDMV/ itself)."
        ),
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="ISO file to write.")],
    media: MediaOption = Media.BD25,
    label: Annotated[
        str | None, typer.Option("--label", help="Volume label (default: the directory name).")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing ISO file.")] = False,
) -> None:
    """Pack a BDMV directory into an ISO (UDF 2.60 + ISO9660 bridge image).

    Strict hardware players or burners may reject this image; burning the BDMV directory
    directly (e.g. with ImgBurn) is the safest way to get a disc.
    """
    progress = Progress(
        TextColumn("Writing ISO"), BarColumn(), TaskProgressColumn(), console=err, transient=True
    )
    task = progress.add_task("iso", total=100)
    try:
        with progress:
            result = build_iso(
                source,
                output,
                media,
                label=label,
                force=force,
                on_progress=lambda percent: progress.update(task, completed=percent),
            )
    except IsoError as exc:
        err.print(Text(str(exc)), soft_wrap=True)
        raise typer.Exit(ExitCode.BUILD_FAILED) from exc

    out.print(
        Text(
            f"Wrote {result.path} ({result.size_bytes / 2**30:.2f} GiB, "
            f"{result.file_count} files, label {result.label})"
        ),
        soft_wrap=True,
    )
