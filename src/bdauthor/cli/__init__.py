import logging
import sys
from typing import Annotated

import typer

from bdauthor.cli.doctor import doctor

app = typer.Typer(
    help="Automate Blu-ray (BDMV) authoring from .mkv files.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show the external commands that are run.")
    ] = False,
) -> None:
    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(message)s")


app.command()(doctor)
