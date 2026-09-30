"""BDAV transport stream (.m2ts) constants and helpers shared across the menu-authoring code.

192-byte source packets, each a 4-byte header (copy permission + arrival time) followed by a
188-byte MPEG transport stream packet.
"""

SOURCE_PACKET = 192

IG_PID = 0x1400  # first interactive graphics PID
IG_STREAM_TYPE = 0x91


def crc32_mpeg(data: bytes) -> int:
    """CRC-32/MPEG-2, as used at the end of program-specific information sections."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = (crc << 1 ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc
