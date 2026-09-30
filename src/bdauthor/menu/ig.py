"""Interactive Graphics (IG) streams: the buttons of an HDMV menu.

An IG display set is a list of segments: one ICS (the pages and buttons), palettes (PDS), the
button images (ODS, run-length coded) and an END segment. Field layouts follow libbluray's
decoder (ig_decode.c, pg_decode.c, graphics_processor.c). Only what a static menu needs is
supported: no effects, no animation, no button sounds, one composition per epoch.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

from bdauthor.navigation.movie_object import Instruction

NONE_ID = 0xFFFF  # "no object" / "no button"
NO_SOUND = 0xFF
_MAX_RUN = 0x3FFF


class Segment(IntEnum):
    PALETTE = 0x14
    OBJECT = 0x15
    INTERACTIVE_COMPOSITION = 0x18
    END = 0x80


class CompositionState(IntEnum):
    NORMAL = 0
    ACQUISITION_POINT = 1
    EPOCH_START = 2


# Frame rate codes of the video descriptor (the same table as index.bdmv).
FRAME_RATE_CODES = {
    (24000, 1001): 1,
    (24, 1): 2,
    (25, 1): 3,
    (30000, 1001): 4,
    (50, 1): 6,
    (60000, 1001): 7,
}


def segment(kind: Segment, payload: bytes) -> bytes:
    """One segment as it is carried in a PES packet: type, length, data."""
    if len(payload) > 0xFFFF:
        raise ValueError(f"segment too large: {len(payload)} bytes")
    return struct.pack(">BH", kind, len(payload)) + payload


def end_segment() -> bytes:
    return segment(Segment.END, b"")


# --- palettes and images ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Palette:
    """Palette entries as (Y, Cr, Cb, alpha) by index; alpha 0 is transparent, 255 opaque."""

    id: int
    entries: dict[int, tuple[int, int, int, int]]
    version: int = 0

    def pack(self) -> bytes:
        body = bytearray(struct.pack(">BB", self.id, self.version))
        for index in sorted(self.entries):
            if not 0 <= index <= 255:
                raise ValueError(f"palette index out of range: {index}")
            body += struct.pack(">B4B", index, *self.entries[index])
        return segment(Segment.PALETTE, bytes(body))


@dataclass(frozen=True)
class Image:
    """A button image: `pixels` holds width*height palette indexes, row by row."""

    id: int
    width: int
    height: int
    pixels: bytes
    version: int = 0

    def __post_init__(self) -> None:
        if len(self.pixels) != self.width * self.height:
            raise ValueError("pixels must hold width*height palette indexes")

    def pack(self) -> bytes:
        rle = encode_rle(self.pixels, self.width, self.height)
        # data length = width + height (4 bytes) + the coded lines; 0xC0 = first and last fragment
        header = struct.pack(">HBB", self.id, self.version, 0xC0) + _u24(4 + len(rle))
        return segment(Segment.OBJECT, header + struct.pack(">HH", self.width, self.height) + rle)


def encode_rle(pixels: bytes, width: int, height: int) -> bytes:
    """Run-length code an image: single pixels as themselves, runs as 0x00 escapes, 0x00 0x00 ends a line."""
    out = bytearray()
    for row in range(height):
        line = pixels[row * width : (row + 1) * width]
        position = 0
        while position < width:
            color = line[position]
            run = 1
            while position + run < width and line[position + run] == color and run < _MAX_RUN:
                run += 1
            position += run
            if color == 0:
                out += bytes([0, run]) if run < 64 else bytes([0, 0x40 | run >> 8, run & 0xFF])
            elif run == 1:
                out.append(color)
            elif run < 64:
                out += bytes([0, 0x80 | run, color])
            else:
                out += bytes([0, 0xC0 | run >> 8, run & 0xFF, color])
        out += b"\x00\x00"
    return bytes(out)


# --- the menu structure ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Button:
    """A button; each state shows one image (ids of `Image`s), NONE_ID hides it in that state."""

    id: int
    x: int
    y: int
    normal: int
    selected: int
    activated: int
    upper: int = NONE_ID
    lower: int = NONE_ID
    left: int = NONE_ID
    right: int = NONE_ID
    commands: tuple[Instruction, ...] = ()
    auto_action: bool = False
    numeric_select_value: int = 0

    def pack(self) -> bytes:
        body = struct.pack(
            ">HHBHH4H",
            self.id,
            self.numeric_select_value,
            0x80 if self.auto_action else 0,
            self.x,
            self.y,
            self.upper,
            self.lower,
            self.left,
            self.right,
        )
        body += struct.pack(">HHB", self.normal, self.normal, 0)  # state images: start, end, repeat flag
        body += struct.pack(">BHHB", NO_SOUND, self.selected, self.selected, 0)
        body += struct.pack(">BHH", NO_SOUND, self.activated, self.activated)
        body += struct.pack(">H", len(self.commands))
        return body + b"".join(command.pack() for command in self.commands)


@dataclass(frozen=True)
class Page:
    """A menu page. Every button gets its own overlap group, so they can all be visible together."""

    id: int
    palette_id: int
    buttons: tuple[Button, ...]
    default_selected: int
    default_activated: int = NONE_ID

    def pack(self) -> bytes:
        ids = [button.id for button in self.buttons]
        if len(set(ids)) != len(ids):
            raise ValueError("button ids must be unique within a page")
        if self.default_selected not in ids:
            raise ValueError("the default selected button is not on the page")
        no_effects = b"\x00\x00"  # no windows, no effects
        body = struct.pack(">BB8s", self.id, 0, bytes(8))  # id, version, UO mask: everything allowed
        body += no_effects + no_effects
        body += struct.pack(">BHHB", 0, self.default_selected, self.default_activated, self.palette_id)
        body += struct.pack(">B", len(self.buttons))
        for button in self.buttons:
            body += struct.pack(">HB", button.id, 1) + button.pack()
        return body


@dataclass(frozen=True)
class InteractiveComposition:
    """The ICS: the pages of a menu that is multiplexed with the video and always on screen."""

    width: int
    height: int
    frame_rate_code: int
    pages: tuple[Page, ...]
    composition_number: int = 0
    state: CompositionState = CompositionState.EPOCH_START
    stream_model: int = 0  # 0: multiplexed with the video ("in-mux"); 1: a separate SubPath clip
    pop_up: bool = False  # False: always-on menu; True: the viewer opens it with a key
    composition_timeout_pts: int = 0  # 0 = the menu never closes by itself; only sent when stream_model == 0
    selection_timeout_pts: int = 0  # 0 = the selected button is never activated by itself; ditto
    user_timeout_duration: int = 0

    def pack(self) -> bytes:
        if not self.pages:
            raise ValueError("an interactive composition needs at least one page")
        for value in (self.composition_timeout_pts, self.selection_timeout_pts):
            if not 0 <= value < 2**33:
                raise ValueError("timeouts are 33-bit PTS values")
        composition = struct.pack(">B", (self.stream_model << 7) | (self.pop_up << 6))
        if self.stream_model == 0:  # out-of-mux compositions omit these two fields entirely, see docs
            composition += _u40(self.composition_timeout_pts) + _u40(self.selection_timeout_pts)
        composition += _u24(self.user_timeout_duration) + struct.pack(">B", len(self.pages))
        composition += b"".join(page.pack() for page in self.pages)

        header = struct.pack(">HHB", self.width, self.height, self.frame_rate_code << 4)  # video descriptor
        header += struct.pack(">HB", self.composition_number, self.state << 6)  # composition descriptor
        header += b"\xC0"  # sequence descriptor: first and last fragment
        return segment(Segment.INTERACTIVE_COMPOSITION, header + _u24(len(composition)) + composition)


def _u24(value: int) -> bytes:
    return value.to_bytes(3, "big")


def _u40(value: int) -> bytes:
    """7 reserved bits + a 33-bit value."""
    return value.to_bytes(5, "big")


def display_set(
    composition: InteractiveComposition, palettes: list[Palette], images: list[Image]
) -> list[bytes]:
    """The segments of one display set, in stream order: ICS, palettes, images, END."""
    return [
        composition.pack(),
        *(palette.pack() for palette in palettes),
        *(image.pack() for image in images),
        end_segment(),
    ]


PTS_CLOCK_HZ = 90_000
MIN_DECODE_GAP_PTS = 9_000  # 100 ms floor: even a tiny display set needs some decode time
_LINEAR_GROWTH_THRESHOLD_BYTES = 8_000  # below this, the floor alone covers it
_DECODE_RATE_BYTES_PER_SEC = 600_000  # conservative decode rate; a real commercial menu showed ~580 KB/s


def decode_gap(total_bytes: int) -> int:
    """PTS ticks to keep between decoding and presenting a display set this big.

    A decoder needs real time to decode a composition (plus its palettes and images) before it can
    show it; sending it with `dts == pts` (as if decoding took no time at all) is not something a
    real commercial disc ever does, and is a plausible cause of decoders stalling or blanking on our
    menus. `MIN_DECODE_GAP_PTS` covers any display set, growing linearly above
    `_LINEAR_GROWTH_THRESHOLD_BYTES` at `_DECODE_RATE_BYTES_PER_SEC`.
    """
    extra = max(0, total_bytes - _LINEAR_GROWTH_THRESHOLD_BYTES)
    return MIN_DECODE_GAP_PTS + round(extra * PTS_CLOCK_HZ / _DECODE_RATE_BYTES_PER_SEC)
