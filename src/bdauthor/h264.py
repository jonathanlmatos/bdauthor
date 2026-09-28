"""Minimal H.264 SPS parser: only the fields the validator needs."""

from dataclasses import dataclass

_HIGH_PROFILES = {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}
_SPS_NAL_TYPE = 7


@dataclass(frozen=True)
class Sps:
    profile_idc: int
    level_idc: int
    chroma_format_idc: int
    bit_depth: int
    num_ref_frames: int
    frame_mbs_only: bool
    width_mbs: int
    height_mbs: int  # frame height in macroblocks (not map units)


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def bit(self) -> int:
        byte = self._data[self._pos >> 3]  # IndexError when reading past the end
        value = (byte >> (7 - (self._pos & 7))) & 1
        self._pos += 1
        return value

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def ue(self) -> int:
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("invalid exp-Golomb code")
        return (1 << zeros) - 1 + self.bits(zeros)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def _skip_scaling_list(reader: _BitReader, size: int) -> None:
    last = next_ = 8
    for _ in range(size):
        if next_ != 0:
            next_ = (last + reader.se() + 256) % 256
        if next_ != 0:
            last = next_


def parse_sps(nal: bytes) -> Sps:
    """Parse an SPS NAL unit (including its 1-byte NAL header)."""
    if not nal or nal[0] & 0x1F != _SPS_NAL_TYPE:
        raise ValueError("not an SPS NAL unit")
    reader = _BitReader(nal[1:].replace(b"\x00\x00\x03", b"\x00\x00"))  # drop emulation bytes

    profile_idc = reader.bits(8)
    reader.bits(8)  # constraint flags + reserved bits
    level_idc = reader.bits(8)
    reader.ue()  # seq_parameter_set_id

    chroma_format_idc = 1
    bit_depth = 8
    if profile_idc in _HIGH_PROFILES:
        chroma_format_idc = reader.ue()
        if chroma_format_idc == 3:
            reader.bit()  # separate_colour_plane_flag
        bit_depth = reader.ue() + 8  # luma; chroma depth is not checked separately
        reader.ue()  # bit_depth_chroma_minus8
        reader.bit()  # qpprime_y_zero_transform_bypass_flag
        if reader.bit():  # seq_scaling_matrix_present_flag
            for i in range(8 if chroma_format_idc != 3 else 12):
                if reader.bit():
                    _skip_scaling_list(reader, 16 if i < 6 else 64)

    reader.ue()  # log2_max_frame_num_minus4
    poc_type = reader.ue()
    if poc_type == 0:
        reader.ue()  # log2_max_pic_order_cnt_lsb_minus4
    elif poc_type == 1:
        reader.bit()  # delta_pic_order_always_zero_flag
        reader.se()  # offset_for_non_ref_pic
        reader.se()  # offset_for_top_to_bottom_field
        for _ in range(reader.ue()):
            reader.se()  # offset_for_ref_frame

    num_ref_frames = reader.ue()
    reader.bit()  # gaps_in_frame_num_value_allowed_flag
    width_mbs = reader.ue() + 1
    height_map_units = reader.ue() + 1
    frame_mbs_only = bool(reader.bit())
    height_mbs = height_map_units * (1 if frame_mbs_only else 2)

    return Sps(
        profile_idc=profile_idc,
        level_idc=level_idc,
        chroma_format_idc=chroma_format_idc,
        bit_depth=bit_depth,
        num_ref_frames=num_ref_frames,
        frame_mbs_only=frame_mbs_only,
        width_mbs=width_mbs,
        height_mbs=height_mbs,
    )


def _find_sps_nal(extradata: bytes) -> bytes | None:
    if extradata[:1] == b"\x01" and len(extradata) >= 8:  # avcC (Matroska/MP4)
        count = extradata[5] & 0x1F
        offset = 6
        for _ in range(count):
            length = int.from_bytes(extradata[offset : offset + 2], "big")
            nal = extradata[offset + 2 : offset + 2 + length]
            if nal and nal[0] & 0x1F == _SPS_NAL_TYPE:
                return nal
            offset += 2 + length
        return None
    for chunk in extradata.split(b"\x00\x00\x01")[1:]:  # Annex B
        if chunk and chunk[0] & 0x1F == _SPS_NAL_TYPE:
            return chunk
    return None


def parse_extradata(extradata: bytes) -> Sps | None:
    """Parse the first SPS in codec extradata (avcC or Annex B); None if unreadable."""
    nal = _find_sps_nal(extradata)
    if nal is None:
        return None
    try:
        return parse_sps(nal)
    except (ValueError, IndexError):
        return None
