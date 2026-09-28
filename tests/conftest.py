from fractions import Fraction
from pathlib import Path

import av
import pytest

from bdauthor.deps import find_libbluray_tool, find_tsmuxer


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
    """Write a tiny synthetic mkv (all-zero H.264 video + silent audio)."""
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
            for plane in frame.planes:  # a new frame is uninitialised memory: zero it so files are deterministic
                plane.update(bytes(plane.buffer_size))
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


@pytest.fixture
def make_disc(tmp_path):
    """Create a small fake disc tree: BDMV + BACKUP, CERTIFICATE, plus files that must be ignored."""

    def factory(name: str = "MY DISC") -> Path:
        root = tmp_path / name
        contents = {
            "BDMV/index.bdmv": b"INDX0200",
            "BDMV/MovieObject.bdmv": b"MOBJ0200" * 8,
            "BDMV/PLAYLIST/00000.mpls": b"MPLS0200" * 16,
            "BDMV/CLIPINF/00000.clpi": b"HDMV0200" * 32,
            "BDMV/STREAM/00000.m2ts": bytes(range(256)) * 4096,  # 1 MiB
            "BDMV/BACKUP/index.bdmv": b"INDX0200",
            "BDMV/BACKUP/PLAYLIST/00000.mpls": b"MPLS0200" * 16,
            "CERTIFICATE/id.bdmv": b"CERT",
            "notes.txt": b"not part of the disc",
            "extra/readme.md": b"not part of the disc",
        }
        empty_dirs = (  # what tsMuxeR creates
            "BDMV/AUXDATA",
            "BDMV/BDJO",
            "BDMV/JAR",
            "BDMV/META",
            "BDMV/BACKUP/BDJO",
            "BDMV/BACKUP/JAR",
            "CERTIFICATE/BACKUP",
        )
        for relative in empty_dirs:
            (root / relative).mkdir(parents=True, exist_ok=True)
        for relative, data in contents.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root

    return factory


@pytest.fixture(scope="session")
def tsmuxer() -> Path:
    path = find_tsmuxer()
    if path is None:
        pytest.skip("tsMuxeR is not installed (see `bdauthor doctor`)")
    return path


@pytest.fixture(scope="session")
def libbluray_tool():
    """`libbluray_tool("mobj_dump")` -> path of the tool; the test is skipped when it is missing."""

    def find(name: str) -> Path:
        path = find_libbluray_tool(name)
        if path is None:
            pytest.skip(f"libbluray tool {name} is not installed (run scripts/bootstrap.py)")
        return path

    return find


@pytest.fixture(scope="session")
def tsmuxer_disc(good_mkv, tmp_path_factory, tsmuxer) -> Path:
    """A disc directory written by the real tsMuxeR from `good_mkv`. Read-only: copy before editing."""
    from bdauthor.build import build_disc
    from bdauthor.model import Media
    from bdauthor.mux.tsmuxer import TsMuxer

    out = tmp_path_factory.mktemp("tsmuxer_disc")
    build_disc(good_mkv, out, Media.BD25, TsMuxer(tsmuxer))
    return out


@pytest.fixture(scope="session")
def good_mkv(make_mkv) -> Path:
    """A BD-compatible 1080p23.976 H.264 + AC3 file."""
    return make_mkv("good.mkv")
