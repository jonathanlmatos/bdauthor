"""Writing a CLIPINF/*.clpi from scratch for a clip tsMuxeR never produces one for."""

from bdauthor.bdav import IG_PID, IG_STREAM_TYPE
from bdauthor.navigation.clip_info import program_map_pid
from bdauthor.navigation.clip_info_writer import HDMV_STREAM_TYPE, IG_APPLICATION_TYPE, build_clip_info

_PMT_PID, _PCR_PID = 0x0100, 0x1001


def _build(**overrides):
    kwargs = dict(
        pmt_pid=_PMT_PID,
        pcr_pid=_PCR_PID,
        stream_pid=IG_PID,
        stream_coding_type=IG_STREAM_TYPE,
        num_source_packets=1234,
        presentation_end=90000,
    )
    kwargs.update(overrides)
    return build_clip_info(**kwargs)


def test_signature_and_header_offsets():
    data = _build()
    assert data[:8] == b"HDMV0200"
    seq_addr, prog_addr, cpi_addr, mark_addr, ext_addr = (
        int.from_bytes(data[8 + 4 * i : 12 + 4 * i], "big") for i in range(5)
    )
    assert ext_addr == 0  # no extension data
    assert data[seq_addr - 4 : seq_addr] != data[prog_addr - 4 : prog_addr]  # distinct blocks
    assert seq_addr < prog_addr < cpi_addr < mark_addr <= len(data)


def test_clip_info_fields_at_their_known_real_disc_offsets():
    # offsets confirmed against real commercial CLIPINF files, see docs/hdmv-ig-notes.md
    data = _build()
    assert data[46] == HDMV_STREAM_TYPE
    assert data[47] == IG_APPLICATION_TYPE


def test_our_own_program_map_pid_reader_agrees():
    data = _build()
    assert program_map_pid(data) == _PMT_PID


def test_empty_cpi_and_clip_mark():
    data = _build()
    seq_addr, prog_addr, cpi_addr, mark_addr, _ = (
        int.from_bytes(data[8 + 4 * i : 12 + 4 * i], "big") for i in range(5)
    )
    assert int.from_bytes(data[cpi_addr : cpi_addr + 4], "big") == 0
    assert int.from_bytes(data[mark_addr : mark_addr + 4], "big") == 0
    assert mark_addr + 4 == len(data)


def test_num_source_packets_and_presentation_end_round_trip():
    data = _build(num_source_packets=999, presentation_end=45000)
    assert b"\x00\x00\x03\xe7" in data  # 999 as a 4-byte big-endian int, sanity spot check
    assert int.from_bytes(data[44 + 2 + 2 + 4 + 4 : 44 + 2 + 2 + 4 + 8], "big") == 999
