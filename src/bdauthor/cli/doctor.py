import typer
from rich.console import Console
from rich.text import Text

from bdauthor.deps import DepStatus, check_all
from bdauthor.exitcodes import ExitCode

out = Console()
err = Console(stderr=True)


def _format(status: DepStatus) -> Text:
    line = Text()
    line.append("✓ " if status.ok else "✗ ", style="green" if status.ok else "red")
    line.append(status.name, style="bold")
    if status.version:
        line.append(f" {status.version}")
    if status.path:
        line.append(f"  ({status.path})", style="dim")
    return line


def doctor() -> None:
    """Check that the tools and libraries BDAuthor needs are available."""
    statuses = check_all()
    for status in statuses:
        out.print(_format(status), soft_wrap=True)

    missing = [s for s in statuses if not s.ok]
    for status in missing:
        err.print(Text(f"{status.name}: {status.hint}"), soft_wrap=True)
    if missing:
        raise typer.Exit(ExitCode.MISSING_DEPENDENCY)
