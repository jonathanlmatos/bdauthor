"""Patching of BDAV transport streams (.m2ts): 192-byte source packets, each a 4-byte header
(copy permission + arrival time) followed by a 188-byte MPEG transport stream packet.

Only what turning a muxed PGS stream into an interactive graphics (IG) stream needs. The whole
file is read into memory: this is meant for the short menu clip.
"""

from dataclasses import dataclass
from pathlib import Path

SOURCE_PACKET = 192
_TS = 4  # offset of the transport stream packet inside a source packet
_SYNC = 0x47
_PES_STREAM_ID_PRIVATE_1 = 0xBD

PGS_PID = 0x1200  # where tsMuxeR puts its presentation graphics stream
IG_PID = 0x1400  # first interactive graphics PID
PGS_STREAM_TYPE = 0x90
IG_STREAM_TYPE = 0x91
_PCS = 0x16  # the PGS composition segment an ICS travels as through tsMuxeR
_ICS = 0x18


class BdavError(Exception):
    """The stream is not what the patch expects."""


@dataclass(frozen=True)
class GraphicsConversion:
    packets: int  # transport packets of the graphics stream that were re-labelled
    compositions: int  # interactive composition segments restored
    tables: int  # program map tables patched


def crc32_mpeg(data: bytes) -> int:
    """CRC-32/MPEG-2, as used at the end of program-specific information sections."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = (crc << 1 ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def convert_graphics_to_interactive(m2ts: Path, pmt_pid: int) -> GraphicsConversion:
    """Turn the PGS stream muxed by tsMuxeR into an IG stream, in place.

    Moves the packets from PID 0x1200 to 0x1400, restores the type of the composition segments
    that were disguised as PGS ones, and rewrites the program map table (type 0x90 -> 0x91).
    """
    data = bytearray(m2ts.read_bytes())
    if len(data) % SOURCE_PACKET:
        raise BdavError(f"{m2ts.name} is not made of {SOURCE_PACKET}-byte source packets")

    packets = compositions = tables = 0
    for start in range(0, len(data), SOURCE_PACKET):
        ts = start + _TS
        if data[ts] != _SYNC:
            raise BdavError(f"lost synchronisation at source packet {start // SOURCE_PACKET}")
        pid = (data[ts + 1] & 0x1F) << 8 | data[ts + 2]
        starts_payload = bool(data[ts + 1] & 0x40)
        if pid == PGS_PID:
            data[ts + 1] = data[ts + 1] & 0xE0 | IG_PID >> 8
            data[ts + 2] = IG_PID & 0xFF
            packets += 1
            if starts_payload and _restore_composition(data, ts):
                compositions += 1
        elif pid == pmt_pid and starts_payload:
            tables += _patch_pmt(data, ts)

    if not packets:
        raise BdavError(f"{m2ts.name} has no graphics stream (PID {PGS_PID:#x})")
    if not tables:
        raise BdavError(f"{m2ts.name} has no program map table listing the graphics stream")
    m2ts.write_bytes(data)
    return GraphicsConversion(packets, compositions, tables)


def _payload_offset(data: bytearray, ts: int) -> int:
    offset = ts + 4
    if data[ts + 3] & 0x20:  # adaptation field
        offset += 1 + data[ts + 4]
    return offset


def _restore_composition(data: bytearray, ts: int) -> bool:
    """If this packet starts a PES packet carrying a disguised ICS, give it its real type back."""
    payload = _payload_offset(data, ts)
    if data[payload : payload + 4] != bytes([0, 0, 1, _PES_STREAM_ID_PRIVATE_1]):
        raise BdavError("a graphics packet does not start a private stream PES packet")
    segment = payload + 9 + data[payload + 8]  # PES header: 9 bytes + optional fields
    if data[segment] == _PCS:
        data[segment] = _ICS
        return True
    return False


def _patch_pmt(data: bytearray, ts: int) -> int:
    """Relabel the PGS entry of a program map table; returns 1 if the table lists one."""
    payload = _payload_offset(data, ts)
    section = payload + 1 + data[payload]  # skip the pointer field
    if data[section] != 0x02:
        return 0
    section_length = (data[section + 1] & 0x0F) << 8 | data[section + 2]
    end = section + 3 + section_length  # one past the CRC
    position = section + 12 + ((data[section + 10] & 0x0F) << 8 | data[section + 11])
    patched = 0
    while position + 5 <= end - 4:
        stream_type = data[position]
        pid = (data[position + 1] & 0x1F) << 8 | data[position + 2]
        if stream_type == PGS_STREAM_TYPE and pid == PGS_PID:
            data[position] = IG_STREAM_TYPE
            data[position + 1] = data[position + 1] & 0xE0 | IG_PID >> 8
            data[position + 2] = IG_PID & 0xFF
            patched = 1
        position += 5 + ((data[position + 3] & 0x0F) << 8 | data[position + 4])
    if patched:
        data[end - 4 : end] = crc32_mpeg(bytes(data[section : end - 4])).to_bytes(4, "big")
    return patched
