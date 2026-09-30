"""A standalone BDAV clip carrying only Interactive Graphics: no video, no audio.

tsMuxeR refuses to mux a stream with no video or audio track ("Can't create EP map. One
audio or video stream is needed."), so a real out-of-mux menu SubPath clip -- which is
exactly a graphics-only clip, per a real commercial disc's own CLIPINF (`application_type
5`, a single Interactive Graphics stream, no video/audio; see docs/hdmv-ig-notes.md) --
cannot come out of our existing tsMuxeR pipeline. This module writes one directly: PAT,
PMT, a periodic PCR, and the IG segments as PES packets, wrapped in 192-byte BDAV source
packets.

Simplification: every source packet's arrival time stamp is either 0 (PAT/PMT/the IG
PES, all sent essentially at once right at the start, matching `decode_gap`'s own "front
loaded" model) or the PCR value it carries. A real muxer paces these more evenly for
recording purposes; this does not affect playback correctness (BD playback timing is
driven by PCR/PTS/DTS, not the arrival time stamp).
"""

import struct
from dataclasses import dataclass, field

from bdauthor.bdav import IG_PID, IG_STREAM_TYPE, SOURCE_PACKET, crc32_mpeg
from bdauthor.menu.ig import PTS_CLOCK_HZ, decode_gap

PAT_PID = 0x0000
PMT_PID = 0x0100
PCR_PID = 0x1001
_NULL_PID = 0x1FFF
_PES_STREAM_ID_PRIVATE_1 = 0xBD
_PCR_INTERVAL_PTS = 8_100  # 90 ms at the 90 kHz PTS clock: matches the reference disc's own IG clip cadence
_TS_SYNC = 0x47
_TS_PACKET_SIZE = SOURCE_PACKET - 4
_MAX_TS_PAYLOAD = _TS_PACKET_SIZE - 4  # minus the 4-byte TS header, no adaptation field
_ALIGNED_UNIT_LEN = 32 * SOURCE_PACKET  # 6144 bytes: a BDAV clip's size must be a multiple of this


@dataclass
class _ContinuityCounters:
    counters: dict[int, int] = field(default_factory=dict)

    def next(self, pid: int) -> int:
        value = self.counters.get(pid, 0)
        self.counters[pid] = (value + 1) & 0xF
        return value


def _source_packet(ts_packet: bytes, arrival_time: int) -> bytes:
    """4-byte TP_extra_header (copy_permission=0, 30-bit arrival time stamp) + the TS packet."""
    if len(ts_packet) != _TS_PACKET_SIZE:
        raise ValueError(f"a TS packet is {_TS_PACKET_SIZE} bytes, got {len(ts_packet)}")
    return struct.pack(">I", arrival_time & 0x3FFFFFFF) + ts_packet


def _ts_packet(pid: int, cc: int, payload: bytes, *, pusi: bool = False, adaptation: bytes = b"") -> bytes:
    """One TS packet: 4-byte header, then an adaptation field (stuffed to fit) and/or payload."""
    if len(adaptation) + len(payload) > _MAX_TS_PAYLOAD:
        raise ValueError("adaptation field + payload does not fit in one TS packet")
    stuffing = _MAX_TS_PAYLOAD - len(adaptation) - len(payload)
    if stuffing:
        if adaptation:
            length = adaptation[0] + stuffing
            adaptation = bytes([length]) + adaptation[1:] + b"\xFF" * stuffing
        elif stuffing == 1:
            adaptation = b"\x00"  # a length-only adaptation field: no flags, pure stuffing
        else:
            adaptation = bytes([stuffing - 1, 0x00]) + b"\xFF" * (stuffing - 2)
    afc = (0b10 if adaptation else 0) | (0b01 if payload else 0)
    header = bytes([_TS_SYNC, (pusi << 6) | (pid >> 8), pid & 0xFF, (afc << 4) | cc])
    return header + adaptation + payload


def _section(table_id: int, payload: bytes) -> bytes:
    """A PSI section (PAT/PMT), CRC included; `payload` is everything after section_length."""
    header = struct.pack(">BH", table_id, 0xB000 | (len(payload) + 4))  # section_syntax_indicator=1
    section = header + payload
    return section + crc32_mpeg(section).to_bytes(4, "big")


_NETWORK_PID = 0x001F  # every real clip inspected (the reference disc's and tsMuxeR's own) lists this


def _pat_section(program_number: int = 1) -> bytes:
    # transport_stream_id, version=0/current, section_number=0, last_section_number=0 -- two
    # distinct single-byte fields, not one: a real disc's own PAT/PMT (and this bug, found by
    # comparing byte-for-byte against one) has 0x00 0x00 here, not a single combined 0x00.
    payload = struct.pack(">HBBB", 1, 0xC1, 0x00, 0x00)
    payload += struct.pack(">HH", 0, 0xE000 | _NETWORK_PID)  # program_number 0: the network PID entry
    payload += struct.pack(">HH", program_number, 0xE000 | PMT_PID)
    return _section(0x00, payload)


# A real commercial disc's own PMT (both its menu clips and tsMuxeR's own output for ours) always
# carries these two descriptors at the program level: a registration_descriptor (tag 5) with
# format_identifier "HDMV" -- the standard way an MPEG-TS program declares itself as BD-ROM HDMV
# content -- and a second, vendor-private one (tag 0x88) that even libbluray's own PMT dissector
# reports as "Unknown Private". Its 4 payload bytes were not the same on tsMuxeR's own output and
# on the reference disc's own clip, so they are not some fixed disc-wide constant; the tag and
# length being present at all is what a strict parser could plausibly care about, so this
# reproduces the reference disc's own exact bytes. See docs/hdmv-ig-notes.md.
_HDMV_REGISTRATION_DESCRIPTOR = bytes([0x05, 0x04]) + b"HDMV"
_PRIVATE_DESCRIPTOR_0x88 = bytes([0x88, 0x04, 0x0F, 0xFF, 0xFF, 0xFF])
_PROGRAM_DESCRIPTORS = _HDMV_REGISTRATION_DESCRIPTOR + _PRIVATE_DESCRIPTOR_0x88


def _pmt_section() -> bytes:
    # program_number=1, version=0/current, section_number=0, last_section_number=0
    payload = struct.pack(">HBBB", 1, 0xC1, 0x00, 0x00)
    payload += struct.pack(">H", 0xE000 | PCR_PID)
    payload += struct.pack(">H", 0xF000 | len(_PROGRAM_DESCRIPTORS)) + _PROGRAM_DESCRIPTORS
    payload += struct.pack(">BH", IG_STREAM_TYPE, 0xE000 | IG_PID) + struct.pack(">H", 0xF000)  # ES_info_length = 0
    return _section(0x02, payload)


def _psi_packet(pid: int, cc: int, section: bytes) -> bytes:
    return _ts_packet(pid, cc, bytes([0]) + section, pusi=True)  # pointer_field = 0


def _pcr_packet(pts: int) -> bytes:
    base = pts & 0x1FFFFFFFF
    pcr = struct.pack(">I", base >> 1) + bytes([((base & 1) << 7) | 0x7E, 0])  # extension always 0
    adaptation = bytes([len(pcr) + 1, 0x10]) + pcr  # length, flags (PCR_flag=1)
    return _ts_packet(PCR_PID, 0, b"", adaptation=adaptation)  # no payload: continuity_counter is unused


def _pts_dts_bytes(value: int, marker: int) -> bytes:
    v = value & 0x1FFFFFFFF
    return bytes(
        [
            (marker << 4) | (((v >> 30) & 0x7) << 1) | 1,
            (v >> 22) & 0xFF,
            (((v >> 15) & 0x7F) << 1) | 1,
            (v >> 7) & 0xFF,
            ((v & 0x7F) << 1) | 1,
        ]
    )


def _pes_packet(segment: bytes, pts: int, dts: int | None) -> bytes:
    """One private_stream_1 PES packet wrapping `segment`, with PTS (and optionally DTS)."""
    has_dts = dts is not None and dts != pts
    header_data = _pts_dts_bytes(pts, 0x3 if has_dts else 0x2)
    if has_dts:
        header_data += _pts_dts_bytes(dts, 0x1)
    flags = 0xC0 if has_dts else 0x80
    body = bytes([0x80, flags, len(header_data)]) + header_data + segment  # 0x80: fixed "10" marker bits
    packet_length = len(body)
    length_field = struct.pack(">H", packet_length) if packet_length <= 0xFFFF else b"\x00\x00"
    return bytes([0, 0, 1, _PES_STREAM_ID_PRIVATE_1]) + length_field + body


def _packetize(pid: int, pes: bytes, cc: _ContinuityCounters) -> list[bytes]:
    packets = []
    offset = 0
    first = True
    while offset < len(pes) or first:
        chunk = pes[offset : offset + _MAX_TS_PAYLOAD]
        packets.append(_ts_packet(pid, cc.next(pid), chunk, pusi=first))
        offset += len(chunk)
        first = False
    return packets


def build_ig_clip(segments: list[bytes], seconds: float, pts: int = 0) -> bytes:
    """A complete .m2ts (BDAV source packets): PAT, PMT, periodic PCR, and `segments` as IG PES.

    `pts` is the earliest allowed presentation instant, exactly like `ig.pgs_carrier`: every
    segment gets the same `(pts, dts)` pair, `decode_gap` ticks apart (see there). The clip spans
    `seconds` (matching the menu's own looping background clip) purely to carry PCR for that
    long; the composition itself is still sent only once, as on a real disc.
    """
    gap = decode_gap(sum(len(data) for data in segments))
    dts = max(0, pts - gap)
    pts = dts + gap
    duration_pts = round(seconds * PTS_CLOCK_HZ)

    cc = _ContinuityCounters()
    out = bytearray()
    out += _source_packet(_psi_packet(PAT_PID, cc.next(PAT_PID), _pat_section()), 0)
    out += _source_packet(_psi_packet(PMT_PID, cc.next(PMT_PID), _pmt_section()), 0)
    for segment in segments:
        for packet in _packetize(IG_PID, _pes_packet(segment, pts, dts), cc):
            out += _source_packet(packet, 0)

    pcr_pts = 0
    while pcr_pts <= duration_pts:
        out += _source_packet(_pcr_packet(pcr_pts), pcr_pts)
        pcr_pts += _PCR_INTERVAL_PTS

    padding = -len(out) % _ALIGNED_UNIT_LEN  # a BDAV clip's file size must be a whole number of aligned units
    while padding:
        out += _source_packet(_ts_packet(_NULL_PID, 0, bytes(_MAX_TS_PAYLOAD)), pcr_pts)
        padding -= SOURCE_PACKET

    return bytes(out)
