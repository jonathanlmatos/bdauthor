import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from bdauthor.exitcodes import ExitCode
from bdauthor.model import AudioStream, MediaInfo, VideoStream, to_jsonable
from bdauthor.probe import ProbeError
from bdauthor.probe import probe as probe_media

out = Console()
err = Console(stderr=True)

_CHANNEL_LAYOUTS = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _kbps(bitrate: int | None) -> str | None:
    return None if bitrate is None else f"{bitrate / 1000:.0f} kb/s"


def _join(*parts: str | None) -> str:
    return " ".join(part for part in parts if part)


def _video_details(v: VideoStream) -> str:
    profile = _join(v.profile) + (f"@{v.level_label}" if v.level_label else "")
    return _join(
        profile,
        f"{v.width}x{v.height}",
        v.rate_label,
        v.pix_fmt,
        f"{v.bit_depth}-bit" if v.bit_depth else None,
        f"refs={v.ref_frames}" if v.ref_frames is not None else None,
        _kbps(v.bitrate),
    )


def _audio_details(a: AudioStream) -> str:
    return _join(
        a.profile,
        _CHANNEL_LAYOUTS.get(a.channels or 0, f"{a.channels}ch" if a.channels else None),
        f"{a.sample_rate / 1000:g} kHz" if a.sample_rate else None,
        f"{a.bit_depth}-bit" if a.bit_depth else None,
        _kbps(a.bitrate),
    )


def _print_summary(info: MediaInfo) -> None:
    out.print(Text(str(info.path), style="bold"), soft_wrap=True)
    out.print(
        Text(
            f"{info.container}  {info.size_bytes / 2**30:.2f} GiB  "
            f"{_clock(info.duration)}  {len(info.chapters)} chapters"
        ),
        soft_wrap=True,
    )

    table = Table(show_header=True, header_style="bold")
    for column in ("#", "Type", "Codec", "Details", "Lang", "Title"):
        table.add_column(column)
    for v in info.video:
        table.add_row(*map(Text, (str(v.index), "video", v.codec, _video_details(v), "", "")))
    for a in info.audio:
        table.add_row(
            *map(Text, (str(a.index), "audio", a.codec, _audio_details(a), a.language or "", a.title or ""))
        )
    for s in info.subtitles:
        table.add_row(*map(Text, (str(s.index), "subtitle", s.codec, "", s.language or "", s.title or "")))
    for o in info.others:
        table.add_row(*map(Text, (str(o.index), o.kind, o.codec or "", "", "", "")))
    out.print(table)


def probe(
    file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Input .mkv file.")
    ],
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON instead of a table.")
    ] = False,
) -> None:
    """Show the streams, chapters and size of a media file."""
    try:
        info = probe_media(file)
    except ProbeError as exc:
        err.print(Text(str(exc)), soft_wrap=True)
        raise typer.Exit(ExitCode.CHECK_FAILED) from exc

    if json_output:
        typer.echo(json.dumps(to_jsonable(info), indent=2, ensure_ascii=False))
    else:
        _print_summary(info)
