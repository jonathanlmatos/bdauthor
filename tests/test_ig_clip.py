"""A standalone, graphics-only BDAV clip (no video/audio, since tsMuxeR refuses that)."""

from pathlib import Path

from bdauthor.bdav import IG_PID, IG_STREAM_TYPE
from bdauthor.menu.ig import Image, Segment, decode_gap, end_segment, segment
from bdauthor.menu.ig_clip import PAT_PID, PCR_PID, PMT_PID, build_ig_clip
from tests.ts_helpers import crc_is_valid, payload_of, pes_payloads, pid_counts, pmt_sections, transport_packets


def test_clip_is_made_of_source_packets(tmp_path):
    data = build_ig_clip([end_segment()], seconds=1)
    assert len(data) % 192 == 0
    (tmp_path / "clip.m2ts").write_bytes(data)


def test_pat_and_pmt_are_present_with_valid_crc(tmp_path):
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=1))
    counts = pid_counts(p)
    assert counts[PAT_PID] == 1 and counts[PMT_PID] == 1
    sections = pmt_sections(p, PMT_PID)
    assert len(sections) == 1
    assert crc_is_valid(sections[0])


def test_pat_lists_the_network_pid_like_every_real_clip_inspected_does(tmp_path):
    # a real disc's own clip (and tsMuxeR's own output) always has two program entries: the
    # network PID (program_number 0, PID 0x1f) and the actual program/PMT one -- see
    # docs/hdmv-ig-notes.md
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=1))
    (section,) = pmt_sections(p, PAT_PID)
    assert crc_is_valid(section)

    def entry(i):
        pos = 8 + 4 * i
        program_number = (section[pos] << 8) | section[pos + 1]
        pid = ((section[pos + 2] & 0x1F) << 8) | section[pos + 3]
        return program_number, pid

    assert entry(0) == (0, 0x1F)
    assert entry(1) == (1, PMT_PID)


def test_pmt_lists_the_ig_stream_and_pcr_pid(tmp_path):
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=1))
    (section,) = pmt_sections(p, PMT_PID)
    assert bytes([0xE0 | PCR_PID >> 8, PCR_PID & 0xFF]) in section
    assert bytes([IG_STREAM_TYPE, 0xE0 | IG_PID >> 8, IG_PID & 0xFF]) in section


def test_pmt_has_the_hdmv_registration_descriptor_at_program_level(tmp_path):
    # a real commercial disc's own PMT (and tsMuxeR's own output) always carries this at the
    # program level; a from-scratch PMT with no descriptors at all is a real, if minor,
    # divergence from what any BD-ROM stream actually looks like -- see docs/hdmv-ig-notes.md
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=1))
    (section,) = pmt_sections(p, PMT_PID)
    program_info_length = ((section[10] & 0x0F) << 8) | section[11]
    descriptors = section[12 : 12 + program_info_length]
    assert bytes([0x05, 0x04]) + b"HDMV" in descriptors  # registration_descriptor, format "HDMV"
    assert descriptors[6] == 0x88  # the vendor-private descriptor every real clip inspected also has
    stream_type, pid_hi = section[12 + program_info_length : 12 + program_info_length + 2]
    assert stream_type == IG_STREAM_TYPE  # descriptors didn't shift the stream loop


def test_segments_come_out_exactly_as_they_went_in(tmp_path):
    segments = [end_segment(), end_segment()]
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip(segments, seconds=1))
    assert pes_payloads(p, IG_PID) == segments


def test_pcr_repeats_for_the_whole_duration(tmp_path):
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=2))
    counts = pid_counts(p)
    # 2s at a 90ms cadence (90kHz clock, 8100 ticks): 23 points from 0 up to and including 2s
    assert counts[PCR_PID] == 23


def test_pcr_packets_carry_no_payload_and_a_valid_pcr_flag(tmp_path):
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=1))
    pcr_packets = [pkt for pkt in transport_packets(p) if (pkt[1] & 0x1F) << 8 | pkt[2] == PCR_PID]
    for pkt in pcr_packets:
        afc = (pkt[3] >> 4) & 0x3
        assert afc == 0b10  # adaptation field only, no payload
        assert pkt[5] & 0x10  # PCR_flag


def _decode_timestamp(b):
    return (b[0] >> 1 & 0x7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1


def test_dts_precedes_pts_by_the_object_decode_rate(tmp_path):
    # an OBJECT (ODS) segment: on the reference disc these carry PTS+DTS, unlike PALETTE/END.
    # 100x800 = 80,000 pixels: ceil(80_000 * 90_000 / 8_000_000) = 900 ticks, exactly, at BD-ROM's
    # own HDMV object decode rate (see _OBJECT_DECODE_RATE).
    image = Image(id=0, width=100, height=800, pixels=bytes(100 * 800))
    segments = [image.pack()]
    gap = decode_gap(sum(len(s) for s in segments))
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip(segments, seconds=1, pts=gap + 1000))
    packets = [pkt for pkt in transport_packets(p) if (pkt[1] & 0x1F) << 8 | pkt[2] == IG_PID]
    pes = payload_of(packets[0])
    assert pes[:3] == b"\x00\x00\x01"
    assert pes[6] == 0x84  # data_alignment_indicator=1: matches the reference disc's own IG PES
    flags, header_len = pes[7], pes[8]
    assert flags == 0xC0  # PTS_DTS_flags = 11: both present
    pts_bytes, dts_bytes = pes[9:14], pes[14:19]

    assert _decode_timestamp(dts_bytes) == 1000  # the clock starts at dts (no ICS ahead of it here)
    assert _decode_timestamp(pts_bytes) == 1000 + 900
    assert header_len == 10


def test_palette_and_end_segments_carry_pts_only(tmp_path):
    # matches the reference disc's own IG clip: PALETTE and END are the only segment types that
    # never carry a DTS, even when the composition/images do (see build_ig_clip's docstring)
    segments = [segment(Segment.PALETTE, b"\x00"), end_segment()]
    gap = decode_gap(sum(len(s) for s in segments))
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip(segments, seconds=1, pts=gap + 1000))
    packets = [pkt for pkt in transport_packets(p) if (pkt[1] & 0x1F) << 8 | pkt[2] == IG_PID]
    starts = [pkt for pkt in packets if pkt[1] & 0x40]
    assert len(starts) == 2
    for pkt in starts:
        pes = payload_of(pkt)
        flags, header_len = pes[7], pes[8]
        assert flags == 0x80  # PTS only, no DTS
        assert header_len == 5
        assert _decode_timestamp(pes[9:14]) == 1000
