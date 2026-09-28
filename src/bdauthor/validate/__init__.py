"""Blu-ray compatibility rules. Each rule module exposes check(info, media) -> findings."""

from collections.abc import Callable

from bdauthor.model import Finding, Media, MediaInfo, Report
from bdauthor.validate import audio, container, size, subtitles, video

Rule = Callable[[MediaInfo, Media], list[Finding]]

RULES: tuple[Rule, ...] = (
    container.check,
    video.check,
    audio.check,
    subtitles.check,
    size.check,
)


def validate(info: MediaInfo, media: Media) -> Report:
    findings = [finding for rule in RULES for finding in rule(info, media)]
    return Report(media=media, findings=tuple(findings))
