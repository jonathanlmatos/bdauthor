import pytest
from PIL import Image as PilImage

from bdauthor.menu.render import DEFAULT_COLORS, draw_button, draw_title, rgb_to_ycrcb, to_indexed
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


def test_to_indexed_needs_images_of_the_same_height():
    with pytest.raises(ValueError, match="same height"):
        to_indexed([PilImage.new("RGBA", (10, 5)), PilImage.new("RGBA", (10, 7))], palette_id=0)
    with pytest.raises(ValueError, match="no images"):
        to_indexed([], palette_id=0)


def test_to_indexed_allows_different_widths():
    narrow = draw_button("1", 100, 60, DEFAULT_COLORS.normal)
    wide = draw_button("Back", 300, 60, DEFAULT_COLORS.normal)
    palette, (narrow_image, wide_image) = to_indexed([narrow, wide], palette_id=0)
    assert (narrow_image.width, narrow_image.height) == (100, 60)
    assert (wide_image.width, wide_image.height) == (300, 60)
    assert len(narrow_image.pixels) == 100 * 60
    assert len(wide_image.pixels) == 300 * 60


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


# --- title text -------------------------------------------------------------------------------------


def test_draw_title_is_white_text_on_a_transparent_background():
    image = draw_title("My Movie", 600, 60)
    assert image.size == (600, 60)
    assert image.getpixel((0, 0))[3] == 0  # corners are transparent, nothing drawn there
    # some pixel in the middle row is opaque white text (anti-aliased edges aside)
    middle = [image.getpixel((x, 30)) for x in range(200, 400)]
    assert any(pixel[3] > 200 and pixel[:3] == (255, 255, 255) for pixel in middle)


def test_draw_title_is_centred():
    left = draw_title("Hi", 400, 60)
    right = draw_title("Hi", 400, 60)
    # same text at the same size draws the same image regardless of canvas content around it
    assert left.tobytes() == right.tobytes()


# --- the scene-selection menu layout -----------------------------------------------------------

from bdauthor.menu.ig import NONE_ID
from bdauthor.menu.simple import BACK_BUTTON, MOVIE_TITLE, PAGE_MAIN, PAGE_SCENES, PLAY_BUTTON, SCENES_BUTTON
from bdauthor.model import Chapter
from bdauthor.navigation.movie_object import imm, jump_title, move, reg, set_button_page


def make_chapter(start: float) -> Chapter:
    return Chapter(start=start, end=start + 100, title=None)


def menu_with_chapters(count: int, **kwargs):
    chapters = tuple(make_chapter(i * 100) for i in range(count))
    return simple_menu(video(width=1280, height=720), chapters=chapters, **kwargs)


def test_no_chapters_or_a_single_chapter_means_no_scenes_page():
    for graphics in (menu_with_chapters(0), menu_with_chapters(1)):
        assert len(graphics.composition.pages) == 1
        assert [b.id for b in graphics.composition.pages[0].buttons] == [PLAY_BUTTON]


def test_more_than_one_chapter_adds_a_scenes_button_and_page():
    graphics = menu_with_chapters(3)
    pages = {p.id: p for p in graphics.composition.pages}
    assert set(pages) == {PAGE_MAIN, PAGE_SCENES}
    assert {b.id for b in pages[PAGE_MAIN].buttons} == {PLAY_BUTTON, SCENES_BUTTON}
    assert {b.id for b in pages[PAGE_SCENES].buttons} == {0, 1, 2, 3}  # Back + 3 scenes


def test_every_scene_button_jumps_to_its_own_mark():
    graphics = menu_with_chapters(4)
    scenes_page = next(p for p in graphics.composition.pages if p.id == PAGE_SCENES)
    title_reg = reg(4076)
    for button in scenes_page.buttons:
        if button.id == BACK_BUTTON:
            continue
        mark_index = button.id - 1
        assert button.commands == (
            move(reg(0), imm(mark_index)),
            move(title_reg, imm(MOVIE_TITLE)),
            jump_title(title_reg),
        )


def test_the_back_button_returns_to_the_main_page():
    graphics = menu_with_chapters(3)
    scenes_page = next(p for p in graphics.composition.pages if p.id == PAGE_SCENES)
    back = next(b for b in scenes_page.buttons if b.id == BACK_BUTTON)
    assert back.commands == set_button_page(PAGE_MAIN, PLAY_BUTTON)


def test_scene_button_images_have_no_name_collisions_and_use_the_shared_palette():
    graphics = menu_with_chapters(12)
    ids = [image.id for image in graphics.images]
    assert len(ids) == len(set(ids))  # every state image has a unique id
    assert len(graphics.palettes) == 1
    for page in graphics.composition.pages:
        assert page.palette_id == graphics.palettes[0].id


def test_scene_grid_neighbours_are_consistent_with_position():
    graphics = menu_with_chapters(7)  # two rows of a 5-column grid
    scenes_page = next(p for p in graphics.composition.pages if p.id == PAGE_SCENES)
    by_id = {b.id: b for b in scenes_page.buttons}
    assert by_id[1].left == NONE_ID and by_id[1].right == 2
    assert by_id[5].right == NONE_ID
    assert by_id[6].upper == 1  # directly below button 1 (same column)
    assert by_id[6].left == NONE_ID and by_id[6].right == 7
    assert by_id[6].lower == BACK_BUTTON  # last row -> Back
    assert by_id[BACK_BUTTON].upper == 6  # Back -> first button of the last row
