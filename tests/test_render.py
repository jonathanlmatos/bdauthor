import pytest
from PIL import Image as PilImage

from bdauthor.menu.render import DEFAULT_COLORS, draw_button, rgb_to_ycrcb, to_indexed
from bdauthor.menu.simple import MenuGraphicsError, simple_menu
from tests.builders import video


def test_colour_conversion_matches_bt709_limited_range():
    assert rgb_to_ycrcb(0, 0, 0) == (16, 128, 128)
    assert rgb_to_ycrcb(255, 255, 255) == (235, 128, 128)
    y, cr, cb = rgb_to_ycrcb(255, 0, 0)
    assert (y, cr) == (63, 240) and cb < 128  # red: low luma, maximum Cr
    y, cr, cb = rgb_to_ycrcb(0, 0, 255)
    assert (y, cb) == (32, 240) and cr < 128


def test_a_button_is_drawn_on_a_transparent_background():
    image = draw_button("Play", 240, 60, DEFAULT_COLORS.selected)
    assert image.size == (240, 60)
    assert image.getpixel((0, 0))[3] == 0  # the rounded corner is transparent
    assert image.getpixel((120, 4))[3] == 255  # the body is opaque


def test_to_indexed_gives_one_palette_and_an_image_per_state():
    states = [draw_button("Play", 240, 60, c) for c in (DEFAULT_COLORS.normal, DEFAULT_COLORS.selected)]
    palette, images = to_indexed(states, palette_id=4, first_object_id=10)
    assert palette.id == 4 and len(palette.entries) <= 255
    assert [image.id for image in images] == [10, 11]
    assert all((image.width, image.height) == (240, 60) for image in images)
    assert all(max(image.pixels) < len(palette.entries) for image in images)
    assert any(alpha == 0 for *_, alpha in palette.entries.values())  # transparency survives


def test_the_indexed_image_reproduces_the_button():
    original = draw_button("Play", 240, 60, DEFAULT_COLORS.activated)
    palette, (image,) = to_indexed([original], palette_id=0)
    body = palette.entries[image.pixels[30 * 240 + 120]]  # a pixel in the middle of the button
    y, cr, cb = rgb_to_ycrcb(*DEFAULT_COLORS.activated[0][:3])
    assert body[3] == 255
    assert all(abs(a - b) <= 4 for a, b in zip(body[:3], (y, cr, cb)))


def test_to_indexed_needs_images_of_the_same_width():
    with pytest.raises(ValueError, match="same width"):
        to_indexed([PilImage.new("RGBA", (10, 5)), PilImage.new("RGBA", (12, 5))], palette_id=0)
    with pytest.raises(ValueError, match="no images"):
        to_indexed([], palette_id=0)


@pytest.mark.parametrize(("width", "height", "size", "position"), [(1920, 1080, (360, 90), (780, 835)), (1280, 720, (240, 60), (520, 557))])
def test_the_play_button_is_centred_near_the_bottom_and_scales_with_the_video(width, height, size, position):
    graphics = simple_menu(video(width=width, height=height))
    button = graphics.composition.pages[0].buttons[0]
    assert (button.x, button.y) == position
    assert {(image.width, image.height) for image in graphics.images} == {size}
    assert (graphics.composition.width, graphics.composition.height) == (width, height)


def test_simple_menu_needs_a_known_frame_rate():
    from fractions import Fraction

    with pytest.raises(MenuGraphicsError, match="frame rate"):
        simple_menu(video(fps=Fraction(17, 1)))
    with pytest.raises(MenuGraphicsError, match="frame rate"):
        simple_menu(video(fps=None))
