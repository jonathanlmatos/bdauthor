from typing import Annotated

import typer

from bdauthor.model import Media

MediaOption = Annotated[
    Media, typer.Option("--media", "-m", help="Target disc; sets the capacity budget.")
]
TranscodeAudioOption = Annotated[
    bool,
    typer.Option(
        "--transcode-audio",
        help="Re-encode audio that Blu-ray does not support to AC3. Video is never re-encoded.",
    ),
]
