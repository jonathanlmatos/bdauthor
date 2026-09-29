"""The menu's video: a short black clip in the movie's format, muxed into its own clip."""

from fractions import Fraction
from pathlib import Path

import av
from PIL import Image as PilImage

from bdauthor.menu.ig import pgs_carrier
from bdauthor.menu.render import rgb_to_ycrcb
from bdauthor.model import VideoStream
from bdauthor.mux.base import Muxer, MuxRequest
from bdauthor.probe import probe

MENU_SECONDS = 10
_BLACK = (16, 128, 128)  # limited-range Y, Cb, Cr
TITLE_TOP_MARGIN = 0.08  # fraction of the frame height, from the top to the title's box


def _render_frame_planes(width: int, height: int, overlay: PilImage.Image | None, origin: tuple[int, int]) -> tuple[bytes, bytes, bytes]:
    """(Y, Cb, Cr) plane bytes (tightly packed, no stride) for a black frame with `overlay` composited at `origin`.

    Chroma is subsampled 2x2 by averaging, as yuv420p expects. Rendered once and reused for every
    frame of the clip, since the whole thing is static.
    """
    y_plane = bytearray([_BLACK[0]]) * (width * height)
    cb_plane = bytearray([_BLACK[1]]) * (width // 2 * (height // 2))
    cr_plane = bytearray([_BLACK[2]]) * (width // 2 * (height // 2))
    if overlay is None:
        return bytes(y_plane), bytes(cb_plane), bytes(cr_plane)

    ox, oy = origin
    rgba = overlay.convert("RGBA")
    pixels = rgba.load()
    for row in range(overlay.height):
        for col in range(overlay.width):
            r, g, b, a = pixels[col, row]
            if not a:
                continue
            # alpha blend against black (0, 0, 0) in linear RGB, then convert the *result*, so the
            # limited-range black offset (Y=16) is not double counted at partially transparent edges
            y, cr, cb = rgb_to_ycrcb(r * a // 255, g * a // 255, b * a // 255)
            y_plane[(oy + row) * width + ox + col] = y
            cx, cy = (ox + col) // 2, (oy + row) // 2
            cb_plane[cy * (width // 2) + cx] = cb
            cr_plane[cy * (width // 2) + cx] = cr
    return bytes(y_plane), bytes(cb_plane), bytes(cr_plane)


def _write_plane(plane, data: bytes, width: int, height: int) -> None:
    stride = plane.line_size
    if stride == width:
        plane.update(data)
        return
    buf = bytearray(plane.buffer_size)
    for row in range(height):
        buf[row * stride : row * stride + width] = data[row * width : (row + 1) * width]
    plane.update(bytes(buf))


class MenuError(Exception):
    """The menu could not be added to the disc."""


def check_video_supported(video: VideoStream) -> None:
    """Raise MenuError if a black clip in the format of `video` cannot be made."""
    if video.interlaced:
        raise MenuError("menus for interlaced video are not supported yet")
    if video.fps is None:
        raise MenuError("the frame rate of the video is unknown")


def title_position(video: VideoStream, overlay_width: int, overlay_height: int) -> tuple[int, int]:
    """Where the title box sits: centred horizontally, `TITLE_TOP_MARGIN` from the top."""
    x = (video.width - overlay_width) // 2 // 2 * 2  # even, so chroma subsampling lines up
    y = round(video.height * TITLE_TOP_MARGIN) // 2 * 2
    return x, y


def write_black_video(
    dest: Path, video: VideoStream, seconds: int = MENU_SECONDS, title: PilImage.Image | None = None
) -> None:
    """An H.264 mkv of black frames with the resolution and frame rate of `video`.

    `title`, if given, is composited once (it is static for the whole clip) near the top.
    """
    check_video_supported(video)
    fps = Fraction(video.fps)
    origin = title_position(video, title.width, title.height) if title else (0, 0)
    y_bytes, cb_bytes, cr_bytes = _render_frame_planes(video.width, video.height, title, origin)
    with av.open(str(dest), "w") as out:
        stream = out.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = video.width, video.height, "yuv420p"
        stream.options = {"keyint": str(max(1, round(fps)))}  # a keyframe every second
        for i in range(round(float(fps) * seconds)):
            frame = av.VideoFrame(video.width, video.height, "yuv420p")
            _write_plane(frame.planes[0], y_bytes, video.width, video.height)
            _write_plane(frame.planes[1], cb_bytes, video.width // 2, video.height // 2)
            _write_plane(frame.planes[2], cr_bytes, video.width // 2, video.height // 2)
            frame.pts = i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


def mux_menu_clip(
    muxer: Muxer,
    video: VideoStream,
    segments: list[bytes],
    work_dir: Path,
    title: PilImage.Image | None = None,
    seconds: int = MENU_SECONDS,
) -> Path:
    """Mux the black video and the IG `segments` into `work_dir`; returns the BDMV directory written.

    The graphics come out as a presentation graphics stream (see `pgs_carrier`) and still have to
    be converted with `convert_graphics_to_interactive`.
    """
    source = work_dir / "menu-background.mkv"
    write_black_video(source, video, seconds=seconds, title=title)
    graphics = work_dir / "menu-graphics.sup"
    graphics.write_bytes(pgs_carrier(segments))
    output = work_dir / "menu-disc"
    output.mkdir()
    muxer.mux(MuxRequest(info=probe(source), output_dir=output, graphics=(graphics,)))
    return output / "BDMV"
