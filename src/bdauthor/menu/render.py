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


def rgb_to_ycrcb(red: int, green: int, blue: int) -> tuple[int, int, int]:
    """BT.709 limited-range (Y, Cr, Cb), the colour space of HD Blu-ray graphics."""
    r, g, b = red / 255, green / 255, blue / 255
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    cb = (b - y) / 1.8556
    cr = (r - y) / 1.5748
    return round(16 + 219 * y), round(128 + 224 * cr), round(128 + 224 * cb)


def to_indexed(images: list[PilImage.Image], palette_id: int, first_object_id: int = 0) -> tuple[Palette, list[Image]]:
    """Quantise `images` (same width) to one shared palette; returns the IG palette and image objects."""
    if not images:
        raise ValueError("no images")
    width = images[0].width
    if any(image.width != width for image in images):
        raise ValueError("all images must have the same width")
    sheet = PilImage.new("RGBA", (width, sum(image.height for image in images)), (0, 0, 0, 0))
    top = 0
    for image in images:
        sheet.paste(image, (0, top))
        top += image.height
    indexed = sheet.quantize(colors=_MAX_COLORS, method=PilImage.Quantize.FASTOCTREE, dither=PilImage.Dither.NONE)

    flat = indexed.getpalette(rawmode="RGBA") or []
    entries = {}
    for index in range(len(flat) // 4):
        red, green, blue, alpha = flat[index * 4 : index * 4 + 4]
        y, cr, cb = rgb_to_ycrcb(red, green, blue)
        entries[index] = (y, cr, cb, alpha)

    pixels = indexed.tobytes()
    objects, offset = [], 0
    for number, image in enumerate(images):
        size = image.width * image.height
        objects.append(Image(first_object_id + number, image.width, image.height, pixels[offset : offset + size]))
        offset += size
    return Palette(palette_id, entries), objects
