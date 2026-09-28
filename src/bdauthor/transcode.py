"""Re-encode Blu-ray-incompatible audio to AC3. Audio is the only thing ever re-encoded."""

from dataclasses import dataclass, replace
from pathlib import Path

import av

from bdauthor.model import Finding, Media, MediaInfo, Report, Severity
from bdauthor.mux.base import ProgressCallback
from bdauthor.validate import validate

SAMPLE_RATE = 48_000
AC3_MAX_CHANNELS = 6
_LAYOUTS = {1: "mono", 2: "stereo", 3: "3.0", 4: "quad", 5: "5.0", 6: "5.1"}
_BITRATES = {1: 128_000, 2: 192_000, 3: 256_000, 4: 320_000, 5: 384_000, 6: 448_000}


class TranscodeError(Exception):
    """An audio stream could not be re-encoded."""


@dataclass(frozen=True)
class AudioPlan:
    """Re-encode one audio stream of the source to AC3."""

    source_index: int
    source_label: str  # e.g. "aac (HE-AAC)"
    source_channels: int
    channels: int
    layout: str
    bitrate: int  # bits per second
    language: str | None

    @property
    def note(self) -> str:
        message = (
            f"will be re-encoded from {self.source_label} {self.source_channels}ch "
            f"to AC3 {self.layout} at {self.bitrate // 1000} kb/s"
        )
        if self.source_channels > self.channels:
            message += f", downmixed from {self.source_channels} channels"
        return message


def plan_audio(info: MediaInfo, report: Report) -> list[AudioPlan]:
    """One plan per audio stream the validator rejected (any audio error is fixed by AC3)."""
    failing = {f.stream_index for f in report.errors if f.rule == "audio"}
    plans = []
    for stream in info.audio:
        if stream.index not in failing:
            continue
        source_channels = stream.channels or 2
        channels = min(source_channels, AC3_MAX_CHANNELS)
        plans.append(
            AudioPlan(
                source_index=stream.index,
                source_label=f"{stream.codec} ({stream.profile})" if stream.profile else stream.codec,
                source_channels=source_channels,
                channels=channels,
                layout=_LAYOUTS[channels],
                bitrate=_BITRATES[channels],
                language=stream.language,
            )
        )
    return plans


def converted_info(info: MediaInfo, plans: list[AudioPlan]) -> MediaInfo:
    """What the file will look like after the audio is re-encoded (for validation)."""
    by_index = {plan.source_index: plan for plan in plans}
    audio = []
    size_delta = 0.0
    for stream in info.audio:
        plan = by_index.get(stream.index)
        if plan is None:
            audio.append(stream)
            continue
        audio.append(
            replace(
                stream,
                codec="ac3",
                profile=None,
                channels=plan.channels,
                sample_rate=SAMPLE_RATE,
                bit_depth=None,
                bitrate=plan.bitrate,
            )
        )
        if info.duration:
            size_delta += (plan.bitrate - (stream.bitrate or 0)) * info.duration / 8
    return replace(info, audio=tuple(audio), size_bytes=max(0, int(info.size_bytes + size_delta)))


def validate_with_audio_transcode(info: MediaInfo, media: Media) -> tuple[Report, list[AudioPlan]]:
    """Validate as if incompatible audio were already AC3; returns the report and the plans."""
    plans = plan_audio(info, validate(info, media))
    if not plans:
        return validate(info, media), []

    notes = {
        plan.source_index: Finding("audio", Severity.WARNING, plan.note, plan.source_index)
        for plan in plans
    }
    findings: list[Finding] = []
    for finding in validate(converted_info(info, plans), media).findings:
        note = notes.pop(finding.stream_index, None) if finding.rule == "audio" else None
        if note:
            findings.append(note)
        findings.append(finding)
    findings.extend(notes.values())
    return Report(media=media, findings=tuple(findings)), plans


def encode_ac3(
    source: Path, plan: AudioPlan, dest: Path, on_progress: ProgressCallback | None = None
) -> None:
    """Write the re-encoded stream to an audio-only mkv, keeping the original timestamps."""
    try:
        with av.open(str(source)) as inp, av.open(str(dest), "w", format="matroska") as out:
            in_stream = inp.streams[plan.source_index]
            out_stream = out.add_stream("ac3", rate=SAMPLE_RATE)
            out_stream.layout = plan.layout
            out_stream.bit_rate = plan.bitrate
            if plan.language:
                out_stream.metadata["language"] = plan.language

            if in_stream.duration:
                duration = float(in_stream.duration * in_stream.time_base)
            else:
                duration = inp.duration / av.time_base if inp.duration else None

            last_percent = -1
            for packet in inp.demux(in_stream):
                for frame in packet.decode():
                    for encoded in out_stream.encode(frame):
                        out.mux(encoded)
                if on_progress and duration and packet.pts is not None:
                    percent = min(99, int(float(packet.pts * packet.time_base) / duration * 100))
                    if percent > last_percent:
                        last_percent = percent
                        on_progress(float(percent))
            for encoded in out_stream.encode(None):
                out.mux(encoded)
        if on_progress:
            on_progress(100.0)
    except (av.error.FFmpegError, OSError, IndexError) as exc:
        raise TranscodeError(
            f"could not re-encode audio stream #{plan.source_index} of {source}: {exc}"
        ) from exc
