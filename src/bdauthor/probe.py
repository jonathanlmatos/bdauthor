"""Read a media file with PyAV and describe it as a MediaInfo."""

import re
from fractions import Fraction
from pathlib import Path

import av

from bdauthor.h264 import Sps, parse_extradata
from bdauthor.model import (
    AudioStream,
    Chapter,
    MediaInfo,
    OtherStream,
    SubtitleStream,
    VideoStream,
)

_CHROMA = {0: "4:0:0", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}
_PIX_FMT_CHROMA = {"420": "4:2:0", "422": "4:2:2", "444": "4:4:4", "440": "4:4:0"}


class ProbeError(Exception):
    """The file could not be opened or read."""


def probe(path: Path) -> MediaInfo:
    try:
        container = av.open(str(path))
    except (av.error.FFmpegError, OSError) as exc:
        raise ProbeError(f"cannot open {path}: {exc}") from exc

    with container:
        video: list[VideoStream] = []
        audio: list[AudioStream] = []
        subtitles: list[SubtitleStream] = []
        others: list[OtherStream] = []
        for stream in container.streams:
            codec = _codec_name(stream)
            if stream.type == "video" and not _is_cover_art(stream):
                video.append(_video(stream))
            elif stream.type == "audio":
                audio.append(_audio(stream))
            elif stream.type == "subtitle":
                subtitles.append(
                    SubtitleStream(
                        stream.index, codec or "unknown", stream.language, _title(stream)
                    )
                )
            else:
                kind = "cover art" if stream.type == "video" else stream.type
                others.append(OtherStream(stream.index, kind, codec))

        return MediaInfo(
            path=path,
            size_bytes=path.stat().st_size,
            duration=None if container.duration is None else container.duration / av.time_base,
            container=container.format.name,
            video=tuple(video),
            audio=tuple(audio),
            subtitles=tuple(subtitles),
            others=tuple(others),
            chapters=_chapters(container),
        )


def _codec_name(stream) -> str | None:
    context = stream.codec_context
    return context.name if context is not None else None


def _is_cover_art(stream) -> bool:
    return bool(stream.disposition & av.stream.Disposition.attached_pic)


def _title(stream) -> str | None:
    return stream.metadata.get("title")


def _bitrate(stream) -> int | None:
    if stream.codec_context.bit_rate:
        return int(stream.codec_context.bit_rate)
    tag = stream.metadata.get("BPS")  # written by mkvmerge statistics tags
    return int(tag) if tag and tag.isdigit() else None


def _video(stream) -> VideoStream:
    context = stream.codec_context
    sps: Sps | None = None
    if context.name == "h264" and context.extradata:
        sps = parse_extradata(bytes(context.extradata))

    level = sps.level_idc if sps else (context.level if context.level and context.level > 0 else None)
    rate = stream.average_rate or stream.guessed_rate
    return VideoStream(
        index=stream.index,
        codec=context.name,
        profile=context.profile,
        level=level,
        width=context.width,
        height=context.height,
        pix_fmt=context.pix_fmt,
        bit_depth=sps.bit_depth if sps else _pix_fmt_bit_depth(context.pix_fmt),
        chroma=_CHROMA.get(sps.chroma_format_idc) if sps else _pix_fmt_chroma(context.pix_fmt),
        fps=Fraction(rate) if rate else None,
        interlaced=None if sps is None else not sps.frame_mbs_only,
        ref_frames=sps.num_ref_frames if sps else None,
        bitrate=_bitrate(stream),
    )


def _pix_fmt_bit_depth(pix_fmt: str | None) -> int | None:
    if not pix_fmt:
        return None
    match = re.search(r"p(\d+)(?:le|be)$", pix_fmt)
    return int(match.group(1)) if match else 8


def _pix_fmt_chroma(pix_fmt: str | None) -> str | None:
    match = re.match(r"yuvj?(\d{3})", pix_fmt or "")
    return _PIX_FMT_CHROMA.get(match.group(1)) if match else None


def _audio(stream) -> AudioStream:
    context = stream.codec_context
    pcm = re.fullmatch(r"pcm_[su](\d+)(?:le|be)", context.name)
    return AudioStream(
        index=stream.index,
        codec=context.name,
        profile=context.profile,
        channels=context.channels or None,
        sample_rate=context.sample_rate or None,
        bit_depth=int(pcm.group(1)) if pcm else None,
        bitrate=_bitrate(stream),
        language=stream.language,
        title=_title(stream),
    )


def _chapters(container) -> tuple[Chapter, ...]:
    return tuple(
        Chapter(
            start=float(chapter["start"] * chapter["time_base"]),
            end=float(chapter["end"] * chapter["time_base"]),
            title=chapter["metadata"].get("title"),
        )
        for chapter in container.chapters()
    )
