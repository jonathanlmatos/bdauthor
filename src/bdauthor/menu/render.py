"""Drawing menu buttons with Pillow and turning them into IG palettes and indexed images."""

from dataclasses import dataclass

from PIL import Image as PilImage
from PIL import ImageDraw, ImageFont

from bdauthor.menu.ig import Image, Palette

_MAX_COLORS = 255
RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class ButtonColors:
    """Background and text colours of a button in each of its three states."""

    normal: tuple[RGBA, RGBA]  # (background, text)
    selected: tuple[RGBA, RGBA]
    activated: tuple[RGBA, RGBA]


DEFAULT_COLORS = ButtonColors(
    normal=((40, 40, 40, 200), (230, 230, 230, 255)),
    selected=((240, 240, 240, 255), (20, 20, 20, 255)),
    activated=((255, 190, 0, 255), (20, 20, 20, 255)),
)


def draw_button(label: str, width: int, height: int, colors: tuple[RGBA, RGBA]) -> PilImage.Image:
    """A rounded button with `label` centred in it, on a transparent background."""
    background, text = colors
    image = PilImage.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 5, fill=background)
    font = ImageFont.load_default(size=round(height * 0.45))
    draw.text((width / 2, height / 2), label, font=font, fill=text, anchor="mm")
    return image


def button_states(label: str, width: int, height: int) -> list[PilImage.Image]:
    """The three images (normal, selected, activated) of one button."""
    return [
        draw_button(label, width, height, colors)
        for colors in (DEFAULT_COLORS.normal, DEFAULT_COLORS.selected, DEFAULT_COLORS.activated)
    ]


def draw_title(text: str, width: int, height: int) -> PilImage.Image:
    """The movie title, centred, on a transparent background; `height` is the text's own box."""
    image = PilImage.new("RGBA", (width, height), (0, 0, 0, 0))
    font = ImageFont.load_default(size=round(height * 0.7))
    draw = ImageDraw.Draw(image)
    draw.text((width / 2, height / 2), text, font=font, fill=(255, 255, 255, 255), anchor="mm")
    return image


def rgb_to_ycrcb(red: int, green: int, blue: int) -> tuple[int, int, int]:
    """BT.709 limited-range (Y, Cr, Cb), the colour space of HD Blu-ray graphics."""
    r, g, b = red / 255, green / 255, blue / 255
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    cb = (b - y) / 1.8556
    cr = (r - y) / 1.5748
    return round(16 + 219 * y), round(128 + 224 * cr), round(128 + 224 * cb)


def to_indexed(images: list[PilImage.Image], palette_id: int, first_object_id: int = 0) -> tuple[Palette, list[Image]]:
    """Quantise `images` (same height) to one shared palette; returns the IG palette and image objects."""
    if not images:
        raise ValueError("no images")
    height = images[0].height
    if any(image.height != height for image in images):
        raise ValueError("all images must have the same height")
    sheet = PilImage.new("RGBA", (sum(image.width for image in images), height), (0, 0, 0, 0))
    left = 0
    for image in images:
        sheet.paste(image, (left, 0))
        left += image.width
    indexed = sheet.quantize(colors=_MAX_COLORS, method=PilImage.Quantize.FASTOCTREE, dither=PilImage.Dither.NONE)

    flat = indexed.getpalette(rawmode="RGBA") or []
    entries = {}
    for index in range(len(flat) // 4):
        red, green, blue, alpha = flat[index * 4 : index * 4 + 4]
        y, cr, cb = rgb_to_ycrcb(red, green, blue)
        entries[index] = (y, cr, cb, alpha)

    sheet_width = sum(image.width for image in images)
    row_bytes = indexed.tobytes()  # one byte per pixel, row-major over the whole sheet
    objects, left = [], 0
    for number, image in enumerate(images):
        pixels = b"".join(
            row_bytes[row * sheet_width + left : row * sheet_width + left + image.width] for row in range(height)
        )
        objects.append(Image(first_object_id + number, image.width, image.height, pixels))
        left += image.width
    return Palette(palette_id, entries), objects
