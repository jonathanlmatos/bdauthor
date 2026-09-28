import av
import pytest

from bdauthor.h264 import Sps, parse_extradata, parse_sps


class BitWriter:
    """Builds RBSP bit strings so SPS fields can be controlled exactly."""

    def __init__(self) -> None:
        self.bits: list[int] = []

    def u(self, count: int, value: int) -> None:
        self.bits.extend((value >> (count - 1 - i)) & 1 for i in range(count))

    def ue(self, value: int) -> None:
        value += 1
        length = value.bit_length()
        self.bits.extend([0] * (length - 1))
        self.u(length, value)

    def se(self, value: int) -> None:
        self.ue(2 * value - 1 if value > 0 else -2 * value)

    def to_bytes(self) -> bytes:
        bits = self.bits + [1]  # rbsp_stop_one_bit
        bits += [0] * (-len(bits) % 8)
        return bytes(int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8))


def build_sps(
    *,
    profile_idc: int = 100,
    level_idc: int = 41,
    chroma_format_idc: int = 1,
    bit_depth: int = 8,
    num_ref_frames: int = 4,
    frame_mbs_only: bool = True,
    width_mbs: int = 120,
    frame_height_mbs: int = 68,
    poc_type: int = 0,
) -> bytes:
    w = BitWriter()
    w.u(8, profile_idc)
    w.u(8, 0)
    w.u(8, level_idc)
    w.ue(0)  # sps id
    if profile_idc in (100, 110, 122, 244):
        w.ue(chroma_format_idc)
        if chroma_format_idc == 3:
            w.u(1, 0)
        w.ue(bit_depth - 8)
        w.ue(bit_depth - 8)
        w.u(1, 0)
        w.u(1, 0)  # no scaling matrix
    w.ue(4)  # log2_max_frame_num_minus4
    w.ue(poc_type)
    if poc_type == 0:
        w.ue(4)
    elif poc_type == 1:
        w.u(1, 0)
        w.se(-1)
        w.se(2)
        w.ue(2)
        w.se(3)
        w.se(-4)
    w.ue(num_ref_frames)
    w.u(1, 0)  # gaps_in_frame_num_value_allowed_flag
    w.ue(width_mbs - 1)
    w.ue(frame_height_mbs - 1 if frame_mbs_only else frame_height_mbs // 2 - 1)
    w.u(1, int(frame_mbs_only))
    return b"\x67" + w.to_bytes()


def test_parse_progressive_1080p_high_profile():
    assert parse_sps(build_sps()) == Sps(
        profile_idc=100,
        level_idc=41,
        chroma_format_idc=1,
        bit_depth=8,
        num_ref_frames=4,
        frame_mbs_only=True,
        width_mbs=120,
        height_mbs=68,
    )


def test_parse_interlaced_reports_frame_height_in_macroblocks():
    sps = parse_sps(build_sps(frame_mbs_only=False))
    assert not sps.frame_mbs_only
    assert (sps.width_mbs, sps.height_mbs) == (120, 68)


def test_parse_high10_bit_depth_and_chroma():
    sps = parse_sps(build_sps(profile_idc=110, bit_depth=10, chroma_format_idc=2))
    assert (sps.bit_depth, sps.chroma_format_idc) == (10, 2)


def test_parse_poc_type_1_still_reaches_later_fields():
    sps = parse_sps(build_sps(poc_type=1, num_ref_frames=5))
    assert sps.num_ref_frames == 5


def test_parse_main_profile_has_no_chroma_or_depth_fields():
    sps = parse_sps(build_sps(profile_idc=77, num_ref_frames=2))
    assert (sps.profile_idc, sps.chroma_format_idc, sps.bit_depth, sps.num_ref_frames) == (
        77,
        1,
        8,
        2,
    )


def test_parse_sps_rejects_other_nal_types():
    with pytest.raises(ValueError):
        parse_sps(b"\x68\xee\x3c\x80")


def test_parse_extradata_avcc_and_annexb():
    nal = build_sps()
    avcc = bytes([1, 100, 0, 41, 0xFF, 0xE1]) + len(nal).to_bytes(2, "big") + nal + b"\x01\x00\x02\x68\xee"
    annexb = b"\x00\x00\x00\x01" + nal + b"\x00\x00\x01\x68\xee\x3c\x80"
    assert parse_extradata(avcc) == parse_sps(nal)
    assert parse_extradata(annexb) == parse_sps(nal)


def test_parse_extradata_returns_none_for_garbage():
    assert parse_extradata(b"") is None
    assert parse_extradata(b"\x01\x64\x00\x29\xff\xe1\x00\x01") is None


def test_parse_real_x264_extradata(good_mkv):
    with av.open(str(good_mkv)) as container:
        context = container.streams.video[0].codec_context
        sps = parse_extradata(bytes(context.extradata))
    assert sps is not None
    assert (sps.profile_idc, sps.chroma_format_idc, sps.bit_depth) == (100, 1, 8)
    assert sps.level_idc == context.level
    assert sps.frame_mbs_only
    assert (sps.width_mbs, sps.height_mbs) == (120, 68)
