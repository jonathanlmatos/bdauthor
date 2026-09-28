import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.text import Text

from bdauthor.cli._report import print_report
from bdauthor.exitcodes import ExitCode
from bdauthor.model import Media, to_jsonable
from bdauthor.probe import ProbeError, probe
from bdauthor.validate import validate

out = Console()
err = Console(stderr=True)


def check(
    file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Input .mkv file.")
    ],
    media: Annotated[
        Media, typer.Option("--media", "-m", help="Target disc; sets the capacity budget.")
    ] = Media.BD25,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON instead of a report.")
    ] = False,
) -> None:
    """Check whether a media file can be remuxed to a Blu-ray disc as it is."""
    try:
        info = probe(file)
    except ProbeError as exc:
        err.print(Text(str(exc)), soft_wrap=True)
        raise typer.Exit(ExitCode.CHECK_FAILED) from exc

    report = validate(info, media)
    if json_output:
        data = {"file": str(file), "passed": report.passed, **to_jsonable(report)}
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print_report(report, out, name=file.name)

    if not report.passed:
        raise typer.Exit(ExitCode.CHECK_FAILED)
