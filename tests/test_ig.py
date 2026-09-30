"""IG segment encoding.

`decode_*` below are small transliterations of libbluray's decoder (ig_decode.c, pg_decode.c): they
catch slips in our field widths and order, but they are not an independent oracle for the format.
The real check is libbluray executing a disc that carries the stream (see test_menu.py).
"""

import random
import struct

import pytest

from bdauthor.menu.ig import (
    NONE_ID,
    Button,
    CompositionState,
    Image,
    InteractiveComposition,
    Page,
    Palette,
    Segment,
    decode_gap,
    display_set,
    encode_rle,
    end_segment,
    segment,
)
from bdauthor.navigation.movie_object import imm, jump_object


class Bits:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def read(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = value << 1 | (self.data[self.pos >> 3] >> (7 - self.pos % 8)) & 1
            self.pos += 1
        return value

    def skip(self, count: int) -> None:
        self.pos += count

    def bytes(self, count: int) -> bytes:
        assert self.pos % 8 == 0
        out = self.data[self.pos // 8 : self.pos // 8 + count]
        self.pos += 8 * count
        return out

    def eof(self) -> bool:
        return self.pos >= 8 * len(self.data)


def decode_rle(data: bytes, width: int, height: int) -> list[int]:
    bits, pixels = Bits(data), []
    while not bits.eof():
        length, color = 1, bits.read(8)
        if not color:
            if not bits.read(1):
                length = bits.read(6) if not bits.read(1) else bits.read(14)
            else:
                length = bits.read(6) if not bits.read(1) else bits.read(14)
                color = bits.read(8)
        pixels += [color] * length
    assert len(pixels) == width * height
    return pixels


def split_segment(data: bytes) -> tuple[int, bytes]:
    kind, length = struct.unpack_from(">BH", data)
    assert length == len(data) - 3
    return kind, data[3:]


def decode_ics(payload: bytes) -> dict:
    bits = Bits(payload)
    ics = {"width": bits.read(16), "height": bits.read(16), "frame_rate": bits.read(4)}
    bits.skip(4)
    ics["number"], ics["state"] = bits.read(16), bits.read(2)
    bits.skip(6)
    assert bits.read(8) == 0xC0  # first and last fragment
    assert bits.read(24) == (len(payload) * 8 - bits.pos) // 8
    ics["stream_model"], ics["ui_model"] = bits.read(1), bits.read(1)
    bits.skip(6)
    assert ics["stream_model"] == 0
    bits.skip(7)
    ics["composition_timeout"] = bits.read(33)
    bits.skip(7)
    ics["selection_timeout"] = bits.read(33)
    ics["user_timeout"] = bits.read(24)
    ics["pages"] = [decode_page(bits) for _ in range(bits.read(8))]
    assert bits.eof()
    return ics


def decode_page(bits: Bits) -> dict:
    page = {"id": bits.read(8), "version": bits.read(8), "uo_mask": bits.bytes(8)}
    for name in ("in", "out"):
        assert bits.read(8) == 0, f"{name} effect windows"
        assert bits.read(8) == 0, f"{name} effects"
    page["frame_rate"] = bits.read(8)
    page["default_selected"], page["default_activated"] = bits.read(16), bits.read(16)
    page["palette"] = bits.read(8)
    page["bogs"] = []
    for _ in range(bits.read(8)):
        bog = {"default_valid": bits.read(16), "buttons": []}
        for _ in range(bits.read(8)):
            bog["buttons"].append(decode_button(bits))
        page["bogs"].append(bog)
    return page


def decode_button(bits: Bits) -> dict:
    button = {"id": bits.read(16), "numeric": bits.read(16), "auto": bits.read(1)}
    bits.skip(7)
    button.update(x=bits.read(16), y=bits.read(16))
    button.update(upper=bits.read(16), lower=bits.read(16), left=bits.read(16), right=bits.read(16))
    button["normal"] = (bits.read(16), bits.read(16), bits.read(1))
    bits.skip(7)
    button["selected"] = (bits.read(8), bits.read(16), bits.read(16), bits.read(1))
    bits.skip(7)
    button["activated"] = (bits.read(8), bits.read(16), bits.read(16))
    button["commands"] = [bits.bytes(12) for _ in range(bits.read(16))]
    return button


def sample_composition(**overrides) -> InteractiveComposition:
    play = Button(
        id=1, x=800, y=800, normal=1, selected=2, activated=3, lower=2, commands=(jump_object(imm(0)),)
    )
    chapters = Button(id=2, x=800, y=900, normal=4, selected=5, activated=6, upper=1)
    page = Page(id=0, palette_id=0, buttons=(play, chapters), default_selected=1)
    return InteractiveComposition(1920, 1080, 1, (page,), **overrides)


# --- run-length coding ------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_rle_round_trips_random_images(seed):
    rng = random.Random(seed)
    width, height = rng.choice([(1, 1), (7, 3), (200, 40), (1920, 4)])
    # runs of every length, colour 0 included, so every encoding form is used
    pixels = bytearray()
    while len(pixels) < width * height:
        pixels += bytes([rng.choice([0, 0, 1, 2, 255])]) * rng.choice([1, 2, 62, 63, 64, 65, 300, 1000])
    pixels = bytes(pixels[: width * height])
    assert decode_rle(encode_rle(pixels, width, height), width, height) == list(pixels)


def test_rle_forms():
    assert encode_rle(bytes([5]), 1, 1) == bytes([5, 0, 0])  # a single pixel is itself
    assert encode_rle(bytes([0, 0, 0]), 3, 1) == bytes([0, 3, 0, 0])  # colour 0 run
    assert encode_rle(bytes([7, 7, 7]), 3, 1) == bytes([0, 0x83, 7, 0, 0])  # short run of a colour
    assert encode_rle(bytes([7] * 100), 100, 1) == bytes([0, 0xC0, 100, 7, 0, 0])  # long run
    assert encode_rle(bytes([0] * 100), 100, 1) == bytes([0, 0x40, 100, 0, 0])  # long run of 0


def test_every_line_ends_with_the_end_of_line_marker():
    assert encode_rle(bytes([1, 2, 3, 4]), 2, 2) == bytes([1, 2, 0, 0, 3, 4, 0, 0])


# --- segments ---------------------------------------------------------------------------------------


def test_segment_framing():
    assert segment(Segment.END, b"") == bytes([0x80, 0, 0])
    assert end_segment() == bytes([0x80, 0, 0])
    assert segment(Segment.PALETTE, b"abc") == bytes([0x14, 0, 3]) + b"abc"
    with pytest.raises(ValueError, match="too large"):
        segment(Segment.OBJECT, bytes(0x10000))


def test_palette_layout():
    kind, payload = split_segment(Palette(id=3, entries={0: (16, 128, 128, 0), 1: (235, 128, 128, 255)}).pack())
    assert kind == 0x14
    assert payload == bytes([3, 0, 0, 16, 128, 128, 0, 1, 235, 128, 128, 255])


def test_image_object_layout():
    kind, payload = split_segment(Image(id=9, width=2, height=1, pixels=bytes([1, 2])).pack())
    assert kind == 0x15
    bits = Bits(payload)
    assert bits.read(16) == 9  # object id
    bits.skip(8)  # version
    assert bits.read(8) == 0xC0  # first and last fragment
    assert bits.read(24) == len(payload) - 7  # width + height + the coded lines
    assert (bits.read(16), bits.read(16)) == (2, 1)
    assert payload[bits.pos // 8 :] == bytes([1, 2, 0, 0])


def test_image_needs_the_right_number_of_pixels():
    with pytest.raises(ValueError):
        Image(id=1, width=2, height=2, pixels=bytes(3))


def test_interactive_composition_round_trips():
    kind, payload = split_segment(sample_composition(composition_number=7).pack())
    assert kind == 0x18
    ics = decode_ics(payload)
    assert (ics["width"], ics["height"], ics["frame_rate"]) == (1920, 1080, 1)
    assert (ics["number"], ics["state"]) == (7, CompositionState.EPOCH_START)
    assert (ics["ui_model"], ics["composition_timeout"], ics["selection_timeout"]) == (0, 0, 0)
    (page,) = ics["pages"]
    assert (page["id"], page["palette"], page["default_selected"], page["default_activated"]) == (0, 0, 1, NONE_ID)
    assert page["uo_mask"] == bytes(8)
    assert [bog["default_valid"] for bog in page["bogs"]] == [1, 2]  # one overlap group per button
    play, chapters = (bog["buttons"][0] for bog in page["bogs"])
    assert (play["x"], play["y"], play["lower"], play["upper"]) == (800, 800, 2, NONE_ID)
    assert play["normal"] == (1, 1, 0) and play["selected"] == (0xFF, 2, 2, 0) and play["activated"] == (0xFF, 3, 3)
    assert play["commands"] == [jump_object(imm(0)).pack()]
    assert chapters["upper"] == 1 and chapters["commands"] == []


def test_pop_up_and_timeouts_are_written():
    ics = decode_ics(split_segment(sample_composition(pop_up=True, composition_timeout_pts=90000 * 5).pack())[1])
    assert ics["ui_model"] == 1 and ics["composition_timeout"] == 450000


def test_page_validation():
    button = Button(id=1, x=0, y=0, normal=1, selected=2, activated=3)
    with pytest.raises(ValueError, match="unique"):
        Page(0, 0, (button, button), default_selected=1).pack()
    with pytest.raises(ValueError, match="default selected"):
        Page(0, 0, (button,), default_selected=9).pack()
    with pytest.raises(ValueError, match="at least one page"):
        InteractiveComposition(1920, 1080, 1, ()).pack()
    with pytest.raises(ValueError, match="33-bit"):
        sample_composition(composition_timeout_pts=2**33).pack()


def test_display_set_order():
    segments = display_set(
        sample_composition(),
        [Palette(0, {1: (16, 128, 128, 255)})],
        [Image(1, 1, 1, bytes([1])), Image(2, 1, 1, bytes([1]))],
    )
    assert [segment_[0] for segment_ in segments] == [0x18, 0x14, 0x15, 0x15, 0x80]


def test_decode_gap_has_a_floor_and_grows_linearly_above_a_threshold():
    small_gap = decode_gap(0)
    assert small_gap == decode_gap(8_000) > 0  # the floor covers anything up to the threshold
    assert decode_gap(8_100) > small_gap  # and it grows past it
    assert decode_gap(16_000) > decode_gap(12_000)  # monotonically
