"""Overlay config loading, placement, and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from fitvid.inspect import require_fields
from fitvid.models import Activity, MapOverlayElement, TextOverlayElement
from fitvid.units import FORMATS, normalize_unit_system, overlay_format


@dataclass
class OverlayConfig:
    text: list[TextOverlayElement] = field(default_factory=list)
    map: MapOverlayElement | None = None
    allow_overlap: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    unit_system: str = "fps"


def apply_unit_system(cfg: OverlayConfig, system: str) -> None:
    """Force every text element onto ``system`` and refresh known format strings."""
    sys = normalize_unit_system(system)
    cfg.unit_system = sys
    for el in cfg.text:
        el.unit_system = sys
        if el.field in FORMATS[sys]:
            el.format = overlay_format(el.field, sys)


def load_overlay_config(path: str | Path) -> OverlayConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    overlay = data.get("overlay", data)
    default_system = normalize_unit_system(overlay.get("unit_system"), default="fps")
    text_els: list[TextOverlayElement] = []
    for item in overlay.get("text") or []:
        pos = item.get("position")
        position = tuple(pos) if pos is not None else None
        field_name = item["field"]
        system = normalize_unit_system(
            item.get("unit_system"), default=default_system
        )
        fmt = item.get("format")
        if not fmt:
            fmt = overlay_format(field_name, system)
        text_els.append(
            TextOverlayElement(
                field=field_name,
                format=fmt,
                label=item.get("label"),
                unit_system=system,
                anchor=item.get("anchor"),
                position=position,  # type: ignore[arg-type]
                margin=int(item.get("margin", 24)),
                font_size=int(item.get("font_size", 32)),
                color=item.get("color", "#FFFFFF"),
                outline_color=item.get("outline_color", "#000000"),
            )
        )
    map_el = None
    if overlay.get("map"):
        m = overlay["map"]
        pos = m.get("position")
        map_el = MapOverlayElement(
            style=m.get("style", "route-only"),
            anchor=m.get("anchor", "bottom-right"),
            position=tuple(pos) if pos is not None else None,  # type: ignore[arg-type]
            margin=int(m.get("margin", 24)),
            width_px=int(m.get("width_px", 320)),
            height_px=int(m.get("height_px", 320)),
            route_color=m.get("route_color", "#FF5A36"),
            route_width=int(m.get("route_width", 3)),
            marker_color=m.get("marker_color", "#FFFFFF"),
            marker_radius=int(m.get("marker_radius", 6)),
            padding_pct=float(m.get("padding_pct", 0.1)),
            tile_provider=m.get("tile_provider"),
        )
    cfg = OverlayConfig(
        text=text_els,
        map=map_el,
        allow_overlap=bool(overlay.get("allow_overlap", False)),
        raw=overlay,
        unit_system=default_system,
    )
    if not cfg.allow_overlap:
        # Anchor cycling (e.g. UI pickers) can stack many fields on the same
        # corner — restack into a non-overlapping left column before validating.
        if _has_overlaps(cfg, frame_size=(1920, 1080)):
            _stack_text_on_left(cfg)
        _check_overlaps(cfg, frame_size=(1920, 1080))
    return cfg


def _stack_text_on_left(config: OverlayConfig) -> None:
    """Place text fields in a vertical stack on the left; keep map on the right."""
    step = 0.065
    start_y = 0.88
    for i, el in enumerate(config.text):
        el.anchor = None
        el.position = (0.02, max(0.04, start_y - i * step))
        el.margin = 0
    if config.map is not None and config.map.position is None:
        # Prefer bottom-right so it clears a tall left text stack
        config.map.anchor = "bottom-right"


def _has_overlaps(config: OverlayConfig, frame_size: tuple[int, int]) -> bool:
    try:
        _check_overlaps(config, frame_size)
        return False
    except ValueError:
        return True


def validate_overlay_fields(activity: Activity, config: OverlayConfig) -> None:
    fields = [el.field for el in config.text]
    if fields:
        require_fields(activity, fields, "overlay")
    if config.map is not None:
        # Map needs lat/lon present at least somewhere
        has_gps = any(r.lat is not None and r.lon is not None for r in activity.records)
        if not has_gps:
            raise ValueError("overlay map requested but FIT file has no lat/lon records")


def resolve_position(
    anchor: str | None,
    position: tuple[float, float] | None,
    margin: int,
    *,
    frame_size: tuple[int, int],
    element_size: tuple[int, int],
) -> tuple[int, int]:
    fw, fh = frame_size
    ew, eh = element_size

    if position is not None:
        x, y = position
        # Infer fraction vs pixels: values in (0, 1] treated as fractions
        # unless both are clearly pixel coords (> 1)
        if 0 <= x <= 1 and 0 <= y <= 1:
            return int(x * fw), int(y * fh)
        return int(x), int(y)

    anchor = anchor or "top-left"
    if anchor == "top-left":
        return margin, margin
    if anchor == "top-right":
        return fw - ew - margin, margin
    if anchor == "bottom-left":
        return margin, fh - eh - margin
    if anchor == "bottom-right":
        return fw - ew - margin, fh - eh - margin
    if anchor == "center":
        return (fw - ew) // 2, (fh - eh) // 2
    raise ValueError(f"Unknown anchor: {anchor}")


def element_bbox(
    anchor: str | None,
    position: tuple[float, float] | None,
    margin: int,
    element_size: tuple[int, int],
    frame_size: tuple[int, int] = (1920, 1080),
) -> tuple[int, int, int, int]:
    x, y = resolve_position(
        anchor, position, margin, frame_size=frame_size, element_size=element_size
    )
    w, h = element_size
    return x, y, x + w, y + h


def _boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _check_overlaps(config: OverlayConfig, frame_size: tuple[int, int]) -> None:
    boxes: list[tuple[str, tuple[int, int, int, int]]] = []
    # Approximate text size — font_size * chars heuristic
    for i, el in enumerate(config.text):
        approx_w = max(80, int(el.font_size * 8))
        approx_h = int(el.font_size * 1.4)
        box = element_bbox(
            el.anchor, el.position, el.margin, (approx_w, approx_h), frame_size
        )
        boxes.append((f"text[{i}]:{el.field}", box))
    if config.map is not None:
        m = config.map
        box = element_bbox(
            m.anchor, m.position, m.margin, (m.width_px, m.height_px), frame_size
        )
        boxes.append(("map", box))

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _boxes_overlap(boxes[i][1], boxes[j][1]):
                raise ValueError(
                    f"Overlay elements overlap: {boxes[i][0]} and {boxes[j][0]}. "
                    "Adjust anchors/positions or set allow_overlap: true"
                )
