from rich.console import Console
from rich.text import Text

from bdauthor.model import Report, Severity

_MARKS = {
    Severity.OK: ("✓", "green"),
    Severity.WARNING: ("!", "yellow"),
    Severity.ERROR: ("✗", "red"),
}


def print_report(report: Report, console: Console, *, name: str) -> None:
    """Print a validation report as one line per finding plus a verdict."""
    console.print(Text(f"{name} → {report.media.label}", style="bold"), soft_wrap=True)
    for finding in report.findings:
        mark, style = _MARKS[finding.severity]
        line = Text()
        line.append(f"{mark} ", style=style)
        line.append(f"{finding.label}: ", style="bold")
        line.append(finding.message)
        console.print(line, soft_wrap=True)

    errors, warnings = len(report.errors), len(report.warnings)
    summary = f"{errors} error(s), {warnings} warning(s)"
    if report.passed:
        console.print(Text(f"PASSED: {summary}", style="bold green"))
    else:
        console.print(Text(f"FAILED: {summary}", style="bold red"))
