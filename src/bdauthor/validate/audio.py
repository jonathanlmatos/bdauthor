"""Audio rules: Blu-ray codecs, sample rates and channel counts."""

from bdauthor.model import AudioStream, Finding, Media, MediaInfo, Severity

RULE = "audio"

_LOSSY_RATES = (48000,)
_LOSSLESS_RATES = (48000, 96000, 192000)
_LPCM_CODECS = ("pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_s24be", "pcm_bluray")
_ALLOWED = "AC3, E-AC3, DTS, DTS-HD, TrueHD, LPCM"


def check(info: MediaInfo, media: Media) -> list[Finding]:
    return [finding for stream in info.audio for finding in _check_stream(stream)]


def _limits(a: AudioStream) -> tuple[tuple[int, ...], int] | None:
    """(allowed sample rates, max channels) for a supported codec, else None."""
    if a.codec == "ac3":
        return _LOSSY_RATES, 6
    if a.codec == "eac3":
        return _LOSSY_RATES, 8
    if a.codec == "dts":
        lossless = "HD" in (a.profile or "") and "Express" not in (a.profile or "")
        return (_LOSSLESS_RATES if lossless else _LOSSY_RATES), 8
    if a.codec == "truehd":
        return _LOSSLESS_RATES, 8
    if a.codec in _LPCM_CODECS:
        return _LOSSLESS_RATES, 8
    return None


def _check_stream(a: AudioStream) -> list[Finding]:
    def finding(severity: Severity, message: str) -> Finding:
        return Finding(RULE, severity, message, a.index)

    limits = _limits(a)
    if limits is None:
        return [finding(Severity.ERROR, f"codec {a.codec} is not allowed (allowed: {_ALLOWED})")]

    rates, max_channels = limits
    findings: list[Finding] = []
    if a.sample_rate is None:
        findings.append(finding(Severity.WARNING, "sample rate unknown"))
    elif a.sample_rate not in rates:
        allowed = ", ".join(f"{rate / 1000:g}" for rate in rates)
        findings.append(
            finding(
                Severity.ERROR,
                f"{a.sample_rate / 1000:g} kHz is not allowed for {a.codec} (allowed: {allowed} kHz)",
            )
        )
    if a.channels is not None and a.channels > max_channels:
        findings.append(
            finding(Severity.ERROR, f"{a.channels} channels exceed the {a.codec} limit of {max_channels}")
        )

    if not any(f.severity is Severity.ERROR for f in findings):
        details = " ".join(
            part
            for part in (
                a.codec,
                a.profile,
                f"{a.channels}ch" if a.channels else None,
                f"{a.sample_rate / 1000:g} kHz" if a.sample_rate else None,
                a.language,
            )
            if part
        )
        findings.append(finding(Severity.OK, details))
    return findings
