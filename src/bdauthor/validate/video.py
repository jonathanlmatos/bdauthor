"""Video rules: H.264 Main/High, 8-bit 4:2:0, 1080/720 Blu-ray formats, level <= 4.1."""

from fractions import Fraction
from math import ceil

from bdauthor.model import Finding, Media, MediaInfo, Severity, VideoStream

RULE = "video"
MAX_LEVEL = 41
ALLOWED_PROFILES = ("Main", "High")

_FILM = Fraction(24000, 1001)
_PROGRESSIVE = {
    (1920, 1080): (_FILM, Fraction(24)),
    (1280, 720): (_FILM, Fraction(24), Fraction(50), Fraction(60000, 1001)),
}
_INTERLACED = {
    (1920, 1080): (Fraction(25), Fraction(30000, 1001)),
}
# H.264 Table A-1: MaxDpbMbs per level_idc.
_MAX_DPB_MBS = {
    10: 396, 11: 900, 12: 2376, 13: 2376, 20: 2376, 21: 4752, 22: 8100,
    30: 8100, 31: 18000, 32: 20480, 40: 32768, 41: 32768, 42: 34816,
    50: 110400, 51: 184320, 52: 184320,
}  # fmt: skip


def check(info: MediaInfo, media: Media) -> list[Finding]:
    return [finding for stream in info.video for finding in _check_stream(stream)]


def _rate(rate: Fraction) -> str:
    return f"{float(rate):.3f}".rstrip("0").rstrip(".")


def _max_ref_frames(v: VideoStream) -> int:
    level = min(v.level or MAX_LEVEL, MAX_LEVEL)
    dpb_mbs = _MAX_DPB_MBS.get(level, _MAX_DPB_MBS[MAX_LEVEL])
    frame_mbs = ceil(v.width / 16) * ceil(v.height / 16)
    return min(16, dpb_mbs // frame_mbs)


def _check_stream(v: VideoStream) -> list[Finding]:
    def finding(severity: Severity, message: str) -> Finding:
        return Finding(RULE, severity, message, v.index)

    def error(message: str) -> Finding:
        return finding(Severity.ERROR, message)

    def warning(message: str) -> Finding:
        return finding(Severity.WARNING, message)

    if v.codec != "h264":
        return [error(f"codec {v.codec} is not allowed (H.264 only)")]

    findings: list[Finding] = []

    if v.profile is None:
        findings.append(warning("H.264 profile unknown"))
    elif v.profile not in ALLOWED_PROFILES:
        findings.append(
            error(f"profile {v.profile} is not allowed ({' or '.join(ALLOWED_PROFILES)})")
        )

    if v.level is None:
        findings.append(warning("H.264 level unknown"))
    elif v.level > MAX_LEVEL:
        findings.append(
            error(f"level {v.level_label} exceeds the Blu-ray maximum of {MAX_LEVEL // 10}.{MAX_LEVEL % 10}")
        )

    if v.bit_depth is not None and v.bit_depth != 8:
        findings.append(error(f"{v.bit_depth}-bit video is not allowed (8-bit only)"))
    if v.chroma is not None and v.chroma != "4:2:0":
        findings.append(error(f"chroma {v.chroma} is not allowed (4:2:0 only)"))

    size = (v.width, v.height)
    if size not in _PROGRESSIVE:
        hint = ""
        if v.width == 1920 and v.height < 1080:
            hint = "; a cropped frame like this needs re-encoding with black bars up to 1920x1080"
        elif v.width > 1920 or v.height > 1080:
            hint = "; UHD/4K is not supported"
        findings.append(error(f"resolution {v.width}x{v.height} is not a Blu-ray format (1920x1080 or 1280x720){hint}"))
    elif v.fps is None:
        findings.append(warning("frame rate unknown"))
    else:
        allowed = (_INTERLACED if v.interlaced else _PROGRESSIVE).get(size, ())
        scan = "i" if v.interlaced else "p"
        if not allowed:
            findings.append(error(f"interlaced video is not allowed at {v.width}x{v.height}"))
        elif not any(abs(float(v.fps) - float(rate)) < 0.01 for rate in allowed):
            options = ", ".join(f"{_rate(rate)}{scan}" for rate in allowed)
            findings.append(
                error(f"{v.rate_label} at {v.width}x{v.height} is not a Blu-ray format (allowed: {options})")
            )

    if v.ref_frames is None:
        findings.append(warning("could not read the H.264 SPS; reference frames and interlacing were not checked"))
    else:
        limit = _max_ref_frames(v)
        if v.ref_frames > limit:
            findings.append(
                error(
                    f"{v.ref_frames} reference frames exceed the limit of {limit} "
                    f"for level {v.level_label or '4.1'} at {v.width}x{v.height}"
                )
            )

    if not any(f.severity is Severity.ERROR for f in findings):
        codec = " ".join(part for part in ("H.264", v.profile) if part)
        if v.level_label:
            codec += f"@{v.level_label}"
        details = " ".join(part for part in (codec, f"{v.width}x{v.height}", v.rate_label) if part)
        findings.append(finding(Severity.OK, details))
    return findings
