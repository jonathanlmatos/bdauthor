"""The simplest menu: Play, plus a scene-selection page when the movie has more than one chapter."""

from dataclasses import dataclass

from PIL import Image as PilImage

from bdauthor.menu.ig import (
    FRAME_RATE_CODES,
    NONE_ID,
    Button,
    Image,
    InteractiveComposition,
    Page,
    Palette,
    display_set,
)
from bdauthor.menu.render import button_states, draw_title, to_indexed
from bdauthor.model import Chapter, VideoStream
from bdauthor.mux.tsmuxer import chapter_marks
from bdauthor.navigation.movie_object import Instruction, imm, jump_title, move, reg, set_button_page

PAGE_MAIN = 0
PAGE_SCENES = 1
PLAY_BUTTON = 1
SCENES_BUTTON = 2
BACK_BUTTON = 0  # on the scenes page; scene buttons are numbered 1..N
MOVIE_TITLE = 1

_REFERENCE_HEIGHT = 1080  # sizes below are for 1080p and scale with the video height
_PLAY_SIZE = (360, 90)
_PLAY_BOTTOM_MARGIN = 200
_SCENE_SIZE = (170, 90)
_SCENE_COLUMNS = 5
_SCENE_SPACING = 24
_SCENES_TOP_MARGIN = 170
_BACK_GAP = 40
TITLE_HEIGHT_FRACTION = 0.08


class MenuGraphicsError(Exception):
    """The menu graphics cannot be built for this video."""


@dataclass(frozen=True)
class MenuGraphics:
    composition: InteractiveComposition
    palettes: list[Palette]
    images: list[Image]
    title: "PilImage.Image | None" = None  # composited onto the background clip, not part of the IG

    def segments(self) -> list[bytes]:
        return display_set(self.composition, self.palettes, self.images)


def frame_rate_code(video: VideoStream) -> int:
    code = FRAME_RATE_CODES.get((video.fps.numerator, video.fps.denominator)) if video.fps else None
    if code is None:
        raise MenuGraphicsError(f"no IG frame rate code for {video.fps} fps")
    return code


@dataclass(frozen=True)
class _ButtonSpec:
    """Everything needed to place one button, before its state images exist."""

    id: int
    x: int
    y: int
    width: int
    height: int
    label: str
    commands: tuple[Instruction, ...]
    upper: int = NONE_ID
    lower: int = NONE_ID
    left: int = NONE_ID
    right: int = NONE_ID


def _chapter_jump(mark_index: int) -> tuple[Instruction, ...]:
    """Play the movie (title 1) starting at `mark_index` (see `chapter_marks`)."""
    return move(reg(0), imm(mark_index)), jump_title(imm(MOVIE_TITLE))


def _main_page_specs(video: VideoStream, scale: float, has_scenes: bool) -> list[_ButtonSpec]:
    width, height = (round(size * scale) for size in _PLAY_SIZE)
    x = (video.width - width) // 2
    y = video.height - round(_PLAY_BOTTOM_MARGIN * scale) - height // 2
    play = _ButtonSpec(
        id=PLAY_BUTTON,
        x=x,
        y=y,
        width=width,
        height=height,
        label="Play",
        commands=(jump_title(imm(MOVIE_TITLE)),),
        lower=SCENES_BUTTON if has_scenes else NONE_ID,
    )
    if not has_scenes:
        return [play]
    scenes = _ButtonSpec(
        id=SCENES_BUTTON,
        x=x,
        y=y + height + round(_SCENE_SPACING * scale),
        width=width,
        height=height,
        label="Scenes",
        commands=(set_button_page(PAGE_SCENES, 1),),
        upper=PLAY_BUTTON,
    )
    return [play, scenes]


def _scenes_page_specs(video: VideoStream, scale: float, marks: list[int]) -> tuple[list[_ButtonSpec], _ButtonSpec]:
    """Scene buttons (ids 1..N, one per mark) and the Back button."""
    count = len(marks)
    width, height = (round(size * scale) for size in _SCENE_SIZE)
    spacing = round(_SCENE_SPACING * scale)
    columns = min(_SCENE_COLUMNS, count)
    grid_width = columns * width + (columns - 1) * spacing
    x0 = (video.width - grid_width) // 2
    y0 = round(_SCENES_TOP_MARGIN * scale)
    last_row = (count - 1) // columns

    specs = []
    for i, mark_index in enumerate(range(count)):
        row, col = divmod(i, columns)
        above = i - columns
        below = i + columns
        specs.append(
            _ButtonSpec(
                id=i + 1,
                x=x0 + col * (width + spacing),
                y=y0 + row * (height + spacing),
                width=width,
                height=height,
                label=str(i + 1),
                commands=_chapter_jump(mark_index),
                upper=above + 1 if above >= 0 else NONE_ID,
                lower=below + 1 if below < count else (BACK_BUTTON if row == last_row else NONE_ID),
                left=i if col > 0 else NONE_ID,
                right=i + 2 if col < columns - 1 and i + 1 < count else NONE_ID,
            )
        )

    first_of_last_row = last_row * columns
    back = _ButtonSpec(
        id=BACK_BUTTON,
        x=(video.width - width) // 2,
        y=y0 + (last_row + 1) * (height + spacing) + round(_BACK_GAP * scale) - spacing,
        width=width,
        height=height,
        label="Back",
        commands=(set_button_page(PAGE_MAIN, PLAY_BUTTON),),
        upper=first_of_last_row + 1,
    )
    return specs, back


def _build_page(
    page_id: int,
    specs: list[_ButtonSpec],
    default_selected: int,
    palette_id: int,
    image_ids: dict[int, tuple[int, int, int]],
) -> Page:
    buttons = tuple(
        Button(
            id=spec.id,
            x=spec.x,
            y=spec.y,
            normal=image_ids[spec.id][0],
            selected=image_ids[spec.id][1],
            activated=image_ids[spec.id][2],
            upper=spec.upper,
            lower=spec.lower,
            left=spec.left,
            right=spec.right,
            commands=spec.commands,
        )
        for spec in specs
    )
    return Page(id=page_id, palette_id=palette_id, buttons=buttons, default_selected=default_selected)


def simple_menu(video: VideoStream, title: str | None = None, chapters: tuple[Chapter, ...] = ()) -> MenuGraphics:
    """The menu: Play (always), plus a Scenes page when `chapters` has more than one mark.

    Scene N jumps to the same mark tsMuxeR's playlist will actually have at that index
    (`chapter_marks`), so it always lands where it visually points, even with duplicate or
    out-of-order chapter timestamps.
    """
    code = frame_rate_code(video)
    scale = video.height / _REFERENCE_HEIGHT
    marks = chapter_marks(chapters)
    has_scenes = len(marks) > 1

    main_specs = _main_page_specs(video, scale, has_scenes)
    scene_specs, back_spec = _scenes_page_specs(video, scale, marks) if has_scenes else ([], None)
    all_specs = main_specs + scene_specs + ([back_spec] if back_spec else [])

    states = [image for spec in all_specs for image in button_states(spec.label, spec.width, spec.height)]
    palette, ig_images = to_indexed(states, palette_id=0, first_object_id=1)
    image_ids = {
        spec.id: tuple(image.id for image in ig_images[i * 3 : i * 3 + 3])
        for i, spec in enumerate(main_specs)
    }
    main_page = _build_page(PAGE_MAIN, main_specs, PLAY_BUTTON, palette.id, image_ids)
    pages = [main_page]

    if has_scenes:
        scene_and_back = scene_specs + [back_spec]
        offset = len(main_specs)
        scene_image_ids = {
            spec.id: tuple(image.id for image in ig_images[(offset + i) * 3 : (offset + i) * 3 + 3])
            for i, spec in enumerate(scene_and_back)
        }
        pages.append(_build_page(PAGE_SCENES, scene_and_back, 1, palette.id, scene_image_ids))

    composition = InteractiveComposition(video.width, video.height, code, tuple(pages))
    title_image = draw_title(title, video.width, round(video.height * TITLE_HEIGHT_FRACTION)) if title else None
    return MenuGraphics(composition, [palette], ig_images, title_image)
