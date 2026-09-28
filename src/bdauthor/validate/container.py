"""Container-level rules: Matroska input, one video stream, at least one audio stream."""

from bdauthor.model import Finding, Media, MediaInfo, Severity

RULE = "container"


def check(info: MediaInfo, media: Media) -> list[Finding]:
    findings: list[Finding] = []

    if "matroska" not in info.container:
        findings.append(
            Finding(
                RULE,
                Severity.ERROR,
                f"only Matroska (.mkv) input is supported for now (found {info.container})",
            )
        )
    if not info.video:
        findings.append(Finding(RULE, Severity.ERROR, "no video stream found"))
    elif len(info.video) > 1:
        findings.append(
            Finding(
                RULE,
                Severity.ERROR,
                f"{len(info.video)} video streams found; exactly one is supported",
            )
        )
    if not info.audio:
        findings.append(Finding(RULE, Severity.ERROR, "no audio stream found"))

    if not findings:
        findings.append(
            Finding(
                RULE,
                Severity.OK,
                f"Matroska, {len(info.video)} video / {len(info.audio)} audio / "
                f"{len(info.subtitles)} subtitle streams",
            )
        )
    return findings
