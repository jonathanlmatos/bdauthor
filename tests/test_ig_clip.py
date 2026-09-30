"""A standalone, graphics-only BDAV clip (no video/audio, since tsMuxeR refuses that)."""

from pathlib import Path

from bdauthor.bdav import IG_PID, IG_STREAM_TYPE
from bdauthor.menu.ig import decode_gap, end_segment
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


def test_pmt_lists_the_ig_stream_and_pcr_pid(tmp_path):
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=1))
    (section,) = pmt_sections(p, PMT_PID)
    assert bytes([0xE0 | PCR_PID >> 8, PCR_PID & 0xFF]) in section
    assert bytes([IG_STREAM_TYPE, 0xE0 | IG_PID >> 8, IG_PID & 0xFF]) in section


def test_segments_come_out_exactly_as_they_went_in(tmp_path):
    segments = [end_segment(), end_segment()]
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip(segments, seconds=1))
    assert pes_payloads(p, IG_PID) == segments


def test_pcr_repeats_for_the_whole_duration(tmp_path):
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=2))
    counts = pid_counts(p)
    # 2s at a 40ms cadence (90kHz clock): 51 points from 0 up to and including 2s
    assert counts[PCR_PID] == 51


def test_pcr_packets_carry_no_payload_and_a_valid_pcr_flag(tmp_path):
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip([end_segment()], seconds=1))
    pcr_packets = [pkt for pkt in transport_packets(p) if (pkt[1] & 0x1F) << 8 | pkt[2] == PCR_PID]
    for pkt in pcr_packets:
        afc = (pkt[3] >> 4) & 0x3
        assert afc == 0b10  # adaptation field only, no payload
        assert pkt[5] & 0x10  # PCR_flag


def test_dts_precedes_pts_by_the_decode_gap(tmp_path):
    segments = [end_segment()]
    gap = decode_gap(sum(len(s) for s in segments))
    p = tmp_path / "clip.m2ts"
    p.write_bytes(build_ig_clip(segments, seconds=1, pts=gap + 1000))
    packets = [pkt for pkt in transport_packets(p) if (pkt[1] & 0x1F) << 8 | pkt[2] == IG_PID]
    pes = payload_of(packets[0])
    assert pes[:3] == b"\x00\x00\x01"
    flags, header_len = pes[7], pes[8]
    assert flags == 0xC0  # PTS_DTS_flags = 11: both present
    pts_bytes, dts_bytes = pes[9:14], pes[14:19]

    def decode(b):
        return (b[0] >> 1 & 0x7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1

    assert decode(pts_bytes) == gap + 1000
    assert decode(dts_bytes) == 1000
    assert header_len == 10
