"""The menu's video: a short black clip in the movie's format, muxed into its own clip."""

from fractions import Fraction
from pathlib import Path

import av
from PIL import Image as PilImage

from bdauthor.menu.render import rgb_to_ycrcb
from bdauthor.model import VideoStream
from bdauthor.mux.base import Muxer, MuxRequest
from bdauthor.probe import probe

MENU_SECONDS = 10
_BLACK = (16, 128, 128)  # limited-range Y, Cb, Cr
TITLE_TOP_MARGIN = 0.08  # fraction of the frame height, from the top to the title's box


_BAR_LUMA = 235  # bright white, limited range
_BAR_WIDTH_FRACTION = 1 / 40


def _render_frame_planes(
    width: int,
    height: int,
    frame_index: int,
    total_frames: int,
    overlay: PilImage.Image | None,
    origin: tuple[int, int],
) -> tuple[bytes, bytes, bytes]:
    """(Y, Cb, Cr) plane bytes (tightly packed, no stride) for one frame of the menu's background:
    a white vertical bar swept left-to-right across the clip's duration over black, with `overlay`
    composited at `origin` if given.

    The bar keeps the background moving for the clip's whole duration instead of a single static
    frame: on a real player, a stall or freeze during playback then shows up as the bar visibly
    stopping too (the underlying video pipeline itself is stuck) or continuing to move smoothly
    while something else stops responding (the problem is elsewhere, e.g. the menu's interactive
    graphics) -- see docs/hdmv-ig-notes.md. Chroma is subsampled 2x2 by averaging, as yuv420p
    expects.
    """
    y_plane = bytearray([_BLACK[0]]) * (width * height)
    cb_plane = bytearray([_BLACK[1]]) * (width // 2 * (height // 2))
    cr_plane = bytearray([_BLACK[2]]) * (width // 2 * (height // 2))

    bar_width = max(2, round(width * _BAR_WIDTH_FRACTION)) // 2 * 2
    x = int(frame_index / max(1, total_frames - 1) * (width - bar_width)) // 2 * 2
    for row in range(height):
        y_plane[row * width + x : row * width + x + bar_width] = bytes([_BAR_LUMA]) * bar_width

    if overlay is not None:
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


_MENU_BITRATE = 8_000_000  # 8 Mbps: conservative for near-static, low-motion content


def write_black_video(
    dest: Path, video: VideoStream, seconds: int = MENU_SECONDS, title: PilImage.Image | None = None
) -> None:
    """An MPEG-2 mkv of the menu's background, with the resolution and frame rate of `video`.

    MPEG-2, not H.264: a real commercial disc's own menu background was found to be MPEG-2 (see
    docs/hdmv-ig-notes.md), and menu video is one of the few places BD-ROM still allows it. `title`,
    if given, is composited once (it is static for the whole clip) near the top; the background
    itself is a moving bar over black for the whole clip, see `_render_frame_planes`.
    """
    check_video_supported(video)
    fps = Fraction(video.fps)
    origin = title_position(video, title.width, title.height) if title else (0, 0)
    with av.open(str(dest), "w") as out:
        stream = out.add_stream("mpeg2video", rate=fps)
        stream.width, stream.height, stream.pix_fmt = video.width, video.height, "yuv420p"
        stream.codec_context.gop_size = max(1, round(fps))  # a keyframe every second
        stream.codec_context.bit_rate = _MENU_BITRATE
        total_frames = round(float(fps) * seconds)
        for i in range(total_frames):
            y_bytes, cb_bytes, cr_bytes = _render_frame_planes(video.width, video.height, i, total_frames, title, origin)
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
    work_dir: Path,
    title: PilImage.Image | None = None,
    seconds: int = MENU_SECONDS,
) -> Path:
    """Mux the background video (no graphics) into `work_dir`; returns the BDMV directory written.

    The IG buttons are no longer carried by this clip: they are a separate, out-of-mux SubPath
    clip (`menu.ig_clip.build_ig_clip`), matching how a real commercial disc delivers a menu.
    """
    source = work_dir / "menu-background.mkv"
    write_black_video(source, video, seconds=seconds, title=title)
    output = work_dir / "menu-disc"
    output.mkdir()
    muxer.mux(MuxRequest(info=probe(source), output_dir=output))
    return output / "BDMV"
