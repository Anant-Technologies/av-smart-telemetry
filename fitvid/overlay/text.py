"""Text overlay rendering driven by Activity.interpolate."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fitvid.models import Activity, TextOverlayElement
from fitvid.overlay.composite import resolve_position
from fitvid.units import convert_si_value


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class TextOverlayRenderer:
    def __init__(self, elements: list[TextOverlayElement], activity: Activity):
        self.elements = elements
        self.activity = activity

    def format_value(self, element: TextOverlayElement, t: datetime) -> str:
        point = self.activity.interpolate(t, extrapolate=False)
        raw = point.get(element.field)
        if raw is None:
            value_str = "--"
        else:
            value = convert_si_value(element.field, float(raw), element.unit_system)
            try:
                value_str = element.format.format(value=value)
            except Exception:
                value_str = str(value)
        if element.label:
            return f"{element.label} {value_str}"
        return value_str

    def render_frame(
        self,
        frame_size: tuple[int, int],
        t: datetime,
    ) -> Image.Image:
        """Return an RGBA overlay image for one frame."""
        w, h = frame_size
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for el in self.elements:
            text = self.format_value(el, t)
            font = _load_font(el.font_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x, y = resolve_position(
                el.anchor,
                el.position,
                el.margin,
                frame_size=(w, h),
                element_size=(tw, th),
            )
            if el.outline_color:
                outline = _hex_to_rgb(el.outline_color) + (255,)
                for dx in (-2, -1, 0, 1, 2):
                    for dy in (-2, -1, 0, 1, 2):
                        if dx == 0 and dy == 0:
                            continue
                        draw.text((x + dx, y + dy), text, font=font, fill=outline)
            fill = _hex_to_rgb(el.color) + (255,)
            draw.text((x, y), text, font=font, fill=fill)
        return img

    def render_sequence(
        self,
        frame_size: tuple[int, int],
        timestamps: list[datetime],
        out_dir: str | Path,
    ) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for i, t in enumerate(timestamps):
            path = out_dir / f"text_{i:06d}.png"
            self.render_frame(frame_size, t).save(path)
            paths.append(path)
        return paths
