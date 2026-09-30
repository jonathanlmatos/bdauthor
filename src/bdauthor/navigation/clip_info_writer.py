"""Writing a CLIPINF/*.clpi from scratch, for a clip tsMuxeR never produced one for.

Field layout and offsets are taken directly from libbluray's own parser
(`src/libbluray/bdnav/clpi_parse.c`), not guessed: see docs/hdmv-ig-notes.md. The header's
own start-address fields are what a reader actually follows to find each block, so as long
as those are consistent, each block's own internal `length` field is informational only
(libbluray skips over it without checking) -- still written correctly here for any other
reader that might check it.
"""

import struct

_SIGNATURE1 = b"HDMV"
_SIGNATURE2 = b"0200"
_HEADER_LEN = 40  # 7 header fields (4 bytes each) + 12 reserved bytes; ClipInfo() always starts here
_CLIP_INFO_RESERVED = 128  # fixed reserved block inside ClipInfo(), per clpi_parse.c
HDMV_STREAM_TYPE = 1  # ClipInfo.clip_stream_type for an HDMV movie (the only kind we author)

IG_APPLICATION_TYPE = 0x05  # "Sub TS for a sub-path of Interactive Graphics menu"


def _clip_info(*, application_type: int, ts_recording_rate: int, num_source_packets: int) -> bytes:
    body = struct.pack(">HBB", 0, HDMV_STREAM_TYPE, application_type)  # reserved(2), stream_type, app_type
    body += struct.pack(">I", 0)  # reserved(31 bits) + is_atc_delta(1 bit) = 0: no ATC delta table
    body += struct.pack(">II", ts_recording_rate, num_source_packets)
    body += bytes(_CLIP_INFO_RESERVED)
    ts_type_info = struct.pack(">B4s", 0x80, b"HDMV")  # validity_flags, format_id (matches a real disc's own)
    body += struct.pack(">H", len(ts_type_info)) + ts_type_info
    return struct.pack(">I", len(body)) + body


def _sequence_info(*, pcr_pid: int, presentation_start: int, presentation_end: int) -> bytes:
    # pcr_pid, spn_stc_start, start, end (45kHz)
    stc_seq = struct.pack(">HIII", pcr_pid, 0, presentation_start, presentation_end)
    atc_seq = struct.pack(">IBB", 0, 1, 0) + stc_seq  # spn_atc_start, num_stc_seq=1, offset_stc_id=0
    body = struct.pack(">BB", 0, 1) + atc_seq  # reserved(1), num_atc_seq=1
    return struct.pack(">I", len(body)) + body


def _program_info(*, pmt_pid: int, pid: int, coding_type: int, language: str = "und") -> bytes:
    stream_attr = struct.pack(">B", coding_type) + language.encode("ascii")  # coding_type(1) + lang(3)
    stream = struct.pack(">H", pid) + struct.pack(">B", len(stream_attr)) + stream_attr
    program = struct.pack(">IHBB", 0, pmt_pid, 1, 0) + stream  # spn_start, pmt_pid, num_streams=1, num_groups=0
    body = struct.pack(">BB", 0, 1) + program  # reserved(1), num_program_sequences=1
    return struct.pack(">I", len(body)) + body


def build_clip_info(
    *,
    pmt_pid: int,
    pcr_pid: int,
    stream_pid: int,
    stream_coding_type: int,
    num_source_packets: int,
    presentation_end: int,
    presentation_start: int = 0,
    application_type: int = IG_APPLICATION_TYPE,
    ts_recording_rate: int = 6_000_000,
) -> bytes:
    """A complete `.clpi`, from scratch, for a clip with a single elementary stream.

    `presentation_start`/`presentation_end` are in the CLPI's own 45 kHz clock (see
    docs/hdmv-ig-notes.md) -- half the 90 kHz PES `PTS`/`DTS` clock the clip's own stream uses.
    """
    clip_info = _clip_info(
        application_type=application_type, ts_recording_rate=ts_recording_rate, num_source_packets=num_source_packets
    )
    sequence_info = _sequence_info(
        pcr_pid=pcr_pid, presentation_start=presentation_start, presentation_end=presentation_end
    )
    program_info = _program_info(pmt_pid=pmt_pid, pid=stream_pid, coding_type=stream_coding_type)
    empty_cpi = struct.pack(">I", 0)  # no EP map: this clip is not meant to be seekable
    empty_clip_mark = struct.pack(">I", 0)

    sequence_info_addr = _HEADER_LEN + len(clip_info)
    program_info_addr = sequence_info_addr + len(sequence_info)
    cpi_addr = program_info_addr + len(program_info)
    clip_mark_addr = cpi_addr + len(empty_cpi)

    header = _SIGNATURE1 + _SIGNATURE2
    header += struct.pack(">IIIII", sequence_info_addr, program_info_addr, cpi_addr, clip_mark_addr, 0)
    header += bytes(_HEADER_LEN - len(header))  # pad to the fixed 40-byte header

    return header + clip_info + sequence_info + program_info + empty_cpi + empty_clip_mark
