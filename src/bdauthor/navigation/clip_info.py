"""Patching of CLIPINF/*.clpi files: the streams of a clip are listed in its program info."""

import struct

_SIGNATURE = b"HDMV"
_PROGRAM_INFO_START = 12  # u32 in the header: where ProgramInfo() starts


class ClipInfoError(Exception):
    """The clip info file is not one this module knows how to patch."""


def relabel_stream(data: bytes, *, old_pid: int, new_pid: int, old_coding: int, new_coding: int) -> bytes:
    """Change the PID and coding type of the stream `old_pid`/`old_coding` in the program info."""
    if data[:4] != _SIGNATURE:
        raise ClipInfoError("not a clip info file")
    patched = bytearray(data)
    try:
        (start,) = struct.unpack_from(">I", data, _PROGRAM_INFO_START)
        # ProgramInfo(): length(4) reserved(1) number_of_program_sequences(1)
        programs = data[start + 5]
        position = start + 6
        for _ in range(programs):
            # spn(4) program_map_pid(2) number_of_streams(1) number_of_groups(1)
            streams = data[position + 6]
            position += 8
            for _ in range(streams):
                pid = struct.unpack_from(">H", data, position)[0]
                attributes = data[position + 2]  # length of the stream coding info
                if pid == old_pid and data[position + 3] == old_coding:
                    struct.pack_into(">H", patched, position, new_pid)
                    patched[position + 3] = new_coding
                    return bytes(patched)
                position += 3 + attributes
    except (struct.error, IndexError) as exc:
        raise ClipInfoError("the clip info file is truncated") from exc
    raise ClipInfoError(f"the clip has no stream with PID {old_pid:#x} and coding type {old_coding:#x}")


def program_map_pid(data: bytes) -> int:
    """PID of the program map table of the clip's first (and normally only) program."""
    if data[:4] != _SIGNATURE:
        raise ClipInfoError("not a clip info file")
    try:
        (start,) = struct.unpack_from(">I", data, _PROGRAM_INFO_START)
        # ProgramInfo(): length(4) reserved(1) number_of_program_sequences(1), then spn(4) pmt pid(2)
        return struct.unpack_from(">H", data, start + 6 + 4)[0]
    except struct.error as exc:
        raise ClipInfoError("the clip info file is truncated") from exc
