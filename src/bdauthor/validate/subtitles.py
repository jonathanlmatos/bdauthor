"""Subtitle rules: only PGS (bitmap) subtitles are allowed on Blu-ray."""

from bdauthor.model import Finding, Media, MediaInfo, Severity

RULE = "subtitles"
PGS = "hdmv_pgs_subtitle"


def check(info: MediaInfo, media: Media) -> list[Finding]:
    findings: list[Finding] = []
    for s in info.subtitles:
        if s.codec == PGS:
            message = " ".join(part for part in ("PGS", s.language) if part)
            findings.append(Finding(RULE, Severity.OK, message, s.index))
        else:
            message = f"codec {s.codec} is not allowed (PGS only; text subtitles must be converted to PGS)"
            findings.append(Finding(RULE, Severity.ERROR, message, s.index))
    return findings
