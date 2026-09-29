"""The simplest menu: a black screen with a "Play" button near the bottom."""

from dataclasses import dataclass

from bdauthor.menu.ig import (
    FRAME_RATE_CODES,
    Button,
    Image,
    InteractiveComposition,
    Page,
    Palette,
    display_set,
)
from bdauthor.menu.render import DEFAULT_COLORS, draw_button, draw_title, to_indexed
from PIL import Image as PilImage

from bdauthor.model import VideoStream
from bdauthor.navigation.movie_object import imm, jump_title

PLAY_BUTTON = 1
_REFERENCE_HEIGHT = 1080  # sizes below are for 1080p and scale with the video height
_BUTTON_SIZE = (360, 90)
_BOTTOM_MARGIN = 200
MOVIE_TITLE = 1
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


def simple_menu(video: VideoStream, label: str = "Play", title: str | None = None) -> MenuGraphics:
    """A page with one button that starts the movie (title 1); `title` is the movie's name, shown above it."""
    code = frame_rate_code(video)

    scale = video.height / _REFERENCE_HEIGHT
    width, height = (round(size * scale) for size in _BUTTON_SIZE)
    x = (video.width - width) // 2
    y = video.height - round(_BOTTOM_MARGIN * scale) - height // 2

    states = [
        draw_button(label, width, height, colors)
        for colors in (DEFAULT_COLORS.normal, DEFAULT_COLORS.selected, DEFAULT_COLORS.activated)
    ]
    palette, images = to_indexed(states, palette_id=0, first_object_id=1)
    normal, selected, activated = (image.id for image in images)

    button = Button(
        id=PLAY_BUTTON,
        x=x,
        y=y,
        normal=normal,
        selected=selected,
        activated=activated,
        commands=(jump_title(imm(MOVIE_TITLE)),),
    )
    page = Page(id=0, palette_id=palette.id, buttons=(button,), default_selected=PLAY_BUTTON)
    composition = InteractiveComposition(video.width, video.height, code, (page,))
    title_image = draw_title(title, video.width, round(video.height * TITLE_HEIGHT_FRACTION)) if title else None
    return MenuGraphics(composition, [palette], images, title_image)
