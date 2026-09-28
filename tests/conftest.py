from fractions import Fraction
from pathlib import Path

import av
import pytest

from bdauthor.deps import find_tsmuxer


def write_mkv(
    path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: Fraction = Fraction(24000, 1001),
    seconds: int = 1,
    pix_fmt: str = "yuv420p",
    acodec: str | None = "ac3",
    sample_rate: int = 48000,
    audio_layout: str = "stereo",
    audio_offset: float = 0.0,
    audio_language: str | None = None,
    audio_title: str | None = None,
    chapters: list[tuple[float, float, str]] | None = None,
) -> None:
    """Write a tiny synthetic mkv (gray H.264 video + silent audio)."""
    with av.open(str(path), "w") as out:
        video = out.add_stream("libx264", rate=fps)
        video.width, video.height, video.pix_fmt = width, height, pix_fmt
        audio = out.add_stream(acodec, rate=sample_rate) if acodec else None
        if audio is not None:
            audio.layout = audio_layout
            if audio_language:
                audio.metadata["language"] = audio_language
            if audio_title:
                audio.metadata["title"] = audio_title
        if chapters:
            out.set_chapters(
                [
                    {
                        "id": n,
                        "start": round(start * 1000),
                        "end": round(end * 1000),
                        "time_base": Fraction(1, 1000),
                        "metadata": {"title": title},
                    }
                    for n, (start, end, title) in enumerate(chapters, start=1)
                ]
            )

        for i in range(round(float(fps) * seconds)):
            frame = av.VideoFrame(width, height, pix_fmt)
            frame.pts = i
            for packet in video.encode(frame):
                out.mux(packet)
        for packet in video.encode():
            out.mux(packet)

        if audio is None:
            return
        frame_size = audio.codec_context.frame_size or 1024
        for i in range(sample_rate * seconds // frame_size):
            frame = av.AudioFrame(
                format=audio.codec_context.format.name, layout=audio_layout, samples=frame_size
            )
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            frame.sample_rate = sample_rate
            frame.pts = round(audio_offset * sample_rate) + i * frame_size
            for packet in audio.encode(frame):
                out.mux(packet)
        for packet in audio.encode():
            out.mux(packet)


@pytest.fixture(scope="session")
def make_mkv(tmp_path_factory):
    def factory(name: str = "movie.mkv", **kwargs) -> Path:
        path = tmp_path_factory.mktemp("mkv") / name
        write_mkv(path, **kwargs)
        return path

    return factory


@pytest.fixture(scope="session")
def tsmuxer() -> Path:
    path = find_tsmuxer()
    if path is None:
        pytest.skip("tsMuxeR is not installed (see `bdauthor doctor`)")
    return path


@pytest.fixture(scope="session")
def good_mkv(make_mkv) -> Path:
    """A BD-compatible 1080p23.976 H.264 + AC3 file."""
    return make_mkv("good.mkv")
