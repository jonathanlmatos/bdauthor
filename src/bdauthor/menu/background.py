"""The menu's video: a short black clip in the movie's format, muxed into its own clip."""

from fractions import Fraction
from pathlib import Path

import av

from bdauthor.menu.ig import pgs_carrier
from bdauthor.model import VideoStream
from bdauthor.mux.base import Muxer, MuxRequest
from bdauthor.probe import probe

MENU_SECONDS = 10
_BLACK = (16, 128, 128)  # limited-range Y, Cb, Cr


class MenuError(Exception):
    """The menu could not be added to the disc."""


def write_black_video(dest: Path, video: VideoStream, seconds: int = MENU_SECONDS) -> None:
    """An H.264 mkv of black frames with the resolution and frame rate of `video`."""
    if video.interlaced:
        raise MenuError("menus for interlaced video are not supported yet")
    if video.fps is None:
        raise MenuError("the frame rate of the video is unknown")
    fps = Fraction(video.fps)
    with av.open(str(dest), "w") as out:
        stream = out.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = video.width, video.height, "yuv420p"
        stream.options = {"keyint": str(max(1, round(fps)))}  # a keyframe every second
        for i in range(round(float(fps) * seconds)):
            frame = av.VideoFrame(video.width, video.height, "yuv420p")
            for plane, value in zip(frame.planes, _BLACK):
                plane.update(bytes([value]) * plane.buffer_size)
            frame.pts = i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


def mux_menu_clip(muxer: Muxer, video: VideoStream, segments: list[bytes], work_dir: Path) -> Path:
    """Mux the black video and the IG `segments` into `work_dir`; returns the BDMV directory written.

    The graphics come out as a presentation graphics stream (see `pgs_carrier`) and still have to
    be converted with `convert_graphics_to_interactive`.
    """
    source = work_dir / "menu-background.mkv"
    write_black_video(source, video)
    graphics = work_dir / "menu-graphics.sup"
    graphics.write_bytes(pgs_carrier(segments))
    output = work_dir / "menu-disc"
    output.mkdir()
    muxer.mux(MuxRequest(info=probe(source), output_dir=output, graphics=(graphics,)))
    return output / "BDMV"
