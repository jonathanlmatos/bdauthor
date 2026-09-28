"""Turning tsMuxeR's presentation graphics stream into an interactive graphics one."""

import shutil

import pytest

from bdauthor.bdav import (
    IG_PID,
    IG_STREAM_TYPE,
    PGS_PID,
    BdavError,
    convert_graphics_to_interactive,
    crc32_mpeg,
)
from bdauthor.navigation.clip_info import program_map_pid
from tests.ts_helpers import crc_is_valid, pes_payloads, pid_counts, pmt_sections

@pytest.fixture
def converted(muxed_menu, tmp_path):
    bdmv, segments = muxed_menu
    m2ts = tmp_path / "00001.m2ts"
    shutil.copyfile(bdmv / "STREAM" / "00000.m2ts", m2ts)
    pmt_pid = program_map_pid((bdmv / "CLIPINF" / "00000.clpi").read_bytes())
    result = convert_graphics_to_interactive(m2ts, pmt_pid)
    return m2ts, pmt_pid, segments, result


def test_crc32_mpeg_check_value():
    assert crc32_mpeg(b"123456789") == 0x0376E6E7  # the standard check value of CRC-32/MPEG-2


def test_tsmuxer_labels_the_graphics_as_pgs(muxed_menu):
    bdmv, _ = muxed_menu
    counts = pid_counts(bdmv / "STREAM" / "00000.m2ts")
    assert PGS_PID in counts and IG_PID not in counts


def test_the_graphics_packets_move_to_the_ig_pid(converted):
    m2ts, _, _, result = converted
    counts = pid_counts(m2ts)
    assert PGS_PID not in counts
    assert counts[IG_PID] == result.packets > 0


def test_every_program_map_table_lists_an_ig_stream_and_keeps_a_valid_crc(converted):
    m2ts, pmt_pid, _, result = converted
    sections = pmt_sections(m2ts, pmt_pid)
    assert len(sections) == result.tables > 0
    for section in sections:
        assert crc_is_valid(section)
        assert bytes([IG_STREAM_TYPE, 0xF0 | IG_PID >> 8, IG_PID & 0xFF]) in section


def test_the_segments_come_out_exactly_as_they_went_in(converted):
    m2ts, _, segments, result = converted
    assert result.compositions == 1
    assert pes_payloads(m2ts, IG_PID) == segments  # the interactive composition has its real type back


def test_converting_a_stream_without_graphics_is_refused(tsmuxer_disc, tmp_path):
    m2ts = tmp_path / "movie.m2ts"
    shutil.copyfile(tsmuxer_disc / "BDMV" / "STREAM" / "00000.m2ts", m2ts)
    with pytest.raises(BdavError, match="no graphics stream"):
        convert_graphics_to_interactive(m2ts, 0x100)


def test_a_file_that_is_not_made_of_source_packets_is_refused(tmp_path):
    path = tmp_path / "bad.m2ts"
    path.write_bytes(bytes(100))
    with pytest.raises(BdavError, match="192-byte"):
        convert_graphics_to_interactive(path, 0x100)


def test_a_lost_sync_byte_is_refused(tmp_path):
    path = tmp_path / "bad.m2ts"
    path.write_bytes(bytes(192))
    with pytest.raises(BdavError, match="synchronisation"):
        convert_graphics_to_interactive(path, 0x100)
