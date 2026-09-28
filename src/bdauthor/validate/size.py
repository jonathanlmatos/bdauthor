"""Size and bitrate rules: the disc must fit the chosen media and the BD bitrate limits."""

from bdauthor.model import Finding, Media, MediaInfo, Severity

RULE = "size"
MAX_TOTAL_BITRATE = 48_000_000
MAX_VIDEO_BITRATE = 40_000_000
# M2TS packetization (4-byte extra header + TS/PES headers) adds about 4-5%.
MUXING_OVERHEAD = 1.05


def _gib(size: float) -> str:
    return f"{size / 2**30:.2f} GiB"


def _mbps(bitrate: float) -> str:
    return f"{bitrate / 1_000_000:.1f} Mbps"


def check(info: MediaInfo, media: Media) -> list[Finding]:
    findings: list[Finding] = []

    estimated = int(info.size_bytes * MUXING_OVERHEAD)
    if estimated > media.capacity_bytes:
        findings.append(
            Finding(
                RULE,
                Severity.ERROR,
                f"estimated disc size {_gib(estimated)} exceeds {media.label} capacity "
                f"{_gib(media.capacity_bytes)} (over by {_gib(estimated - media.capacity_bytes)})",
            )
        )

    if info.duration:
        average = estimated * 8 / info.duration
        if average > MAX_TOTAL_BITRATE:
            findings.append(
                Finding(
                    RULE,
                    Severity.ERROR,
                    f"average bitrate {_mbps(average)} exceeds the Blu-ray limit of {_mbps(MAX_TOTAL_BITRATE)}",
                )
            )
    for v in info.video:
        if v.bitrate is not None and v.bitrate > MAX_VIDEO_BITRATE:
            findings.append(
                Finding(
                    "video",
                    Severity.ERROR,
                    f"bitrate {_mbps(v.bitrate)} exceeds the Blu-ray limit of {_mbps(MAX_VIDEO_BITRATE)}",
                    v.index,
                )
            )

    if not findings:
        findings.append(
            Finding(
                RULE,
                Severity.OK,
                f"estimated disc size {_gib(estimated)} fits {media.label} ({_gib(media.capacity_bytes)})",
            )
        )
    return findings
