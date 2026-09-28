"""Reading .m2ts files in tests: just enough to look inside a graphics stream."""

import collections
from pathlib import Path

from bdauthor.bdav import SOURCE_PACKET, crc32_mpeg


def transport_packets(m2ts: Path) -> list[bytes]:
    """The 188-byte transport stream packets of a BDAV file."""
    data = m2ts.read_bytes()
    assert len(data) % SOURCE_PACKET == 0
    return [data[i + 4 : i + SOURCE_PACKET] for i in range(0, len(data), SOURCE_PACKET)]


def pid_of(packet: bytes) -> int:
    return (packet[1] & 0x1F) << 8 | packet[2]


def payload_of(packet: bytes) -> bytes:
    offset = 4 + (1 + packet[4] if packet[3] & 0x20 else 0)
    return packet[offset:] if packet[3] & 0x10 else b""


def pid_counts(m2ts: Path) -> dict[int, int]:
    return dict(collections.Counter(pid_of(p) for p in transport_packets(m2ts)))


def pes_payloads(m2ts: Path, pid: int) -> list[bytes]:
    """The payload (after the PES header) of every PES packet of `pid`."""
    packets, current = [], None
    for packet in transport_packets(m2ts):
        if pid_of(packet) != pid:
            continue
        if packet[1] & 0x40:  # payload unit start
            if current is not None:
                packets.append(current)
            current = bytearray(payload_of(packet))
        elif current is not None:
            current += payload_of(packet)
    if current is not None:
        packets.append(current)
    out = []
    for pes in packets:
        assert pes[:3] == b"\x00\x00\x01"
        length = int.from_bytes(pes[4:6], "big")
        end = 6 + length if length else len(pes)
        out.append(bytes(pes[9 + pes[8] : end]))
    return out


def pmt_sections(m2ts: Path, pmt_pid: int) -> list[bytes]:
    """Every program map table section in the file, CRC included."""
    sections = []
    for packet in transport_packets(m2ts):
        if pid_of(packet) == pmt_pid and packet[1] & 0x40:
            payload = payload_of(packet)
            start = 1 + payload[0]
            length = (payload[start + 1] & 0x0F) << 8 | payload[start + 2]
            sections.append(payload[start : start + 3 + length])
    return sections


def crc_is_valid(section: bytes) -> bool:
    """A section is intact when the CRC over all of it, its own CRC included, is zero."""
    return crc32_mpeg(section) == 0
