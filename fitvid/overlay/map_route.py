"""Route-only map overlay with live position marker."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from fitvid.models import Activity, MapOverlayElement
from fitvid.overlay.composite import resolve_position


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


class RouteMapRenderer:
    def __init__(self, element: MapOverlayElement, activity: Activity):
        if element.style == "tiles":
            raise NotImplementedError(
                "tiles-style map is not implemented in v1; use style: route-only"
            )
        self.element = element
        self.activity = activity
        self._route_points = [
            (r.lat, r.lon)
            for r in activity.records
            if r.lat is not None and r.lon is not None
        ]
        self._bbox = self._compute_bbox()
        self._cached_route: Image.Image | None = None
        self._last_known: tuple[float, float] | None = (
            self._route_points[-1] if self._route_points else None
        )

    def _compute_bbox(
        self,
    ) -> tuple[float, float, float, float] | None:
        if not self._route_points:
            return None
        lats = [p[0] for p in self._route_points]
        lons = [p[1] for p in self._route_points]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        # Avoid zero-size bbox
        if abs(max_lat - min_lat) < 1e-8:
            min_lat -= 0.0001
            max_lat += 0.0001
        if abs(max_lon - min_lon) < 1e-8:
            min_lon -= 0.0001
            max_lon += 0.0001
        pad = self.element.padding_pct
        lat_pad = (max_lat - min_lat) * pad
        lon_pad = (max_lon - min_lon) * pad
        return (
            min_lat - lat_pad,
            max_lat + lat_pad,
            min_lon - lon_pad,
            max_lon + lon_pad,
        )

    def project(self, lat: float, lon: float) -> tuple[int, int]:
        assert self._bbox is not None
        min_lat, max_lat, min_lon, max_lon = self._bbox
        w, h = self.element.width_px, self.element.height_px
        x = (lon - min_lon) / (max_lon - min_lon) * (w - 1)
        # lat: north at top
        y = (1.0 - (lat - min_lat) / (max_lat - min_lat)) * (h - 1)
        return int(round(x)), int(round(y))

    def route_layer(self) -> Image.Image:
        if self._cached_route is not None:
            return self._cached_route.copy()
        w, h = self.element.width_px, self.element.height_px
        img = Image.new("RGBA", (w, h), (0, 0, 0, 160))
        draw = ImageDraw.Draw(img)
        if self._bbox and len(self._route_points) >= 2:
            pts = [self.project(lat, lon) for lat, lon in self._route_points]
            color = _hex_to_rgb(self.element.route_color) + (230,)
            draw.line(pts, fill=color, width=self.element.route_width, joint="curve")
        # Border
        draw.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255, 80))
        self._cached_route = img
        return img.copy()

    def render_at(self, t: datetime) -> Image.Image:
        img = self.route_layer()
        if self._bbox is None:
            return img
        point = self.activity.interpolate(t, extrapolate=False)
        lat, lon = point.lat, point.lon
        if lat is None or lon is None:
            return img  # no marker outside FIT coverage
        self._last_known = (lat, lon)
        x, y = self.project(lat, lon)
        draw = ImageDraw.Draw(img)
        r = self.element.marker_radius
        fill = _hex_to_rgb(self.element.marker_color) + (255,)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)
        # dark outline for contrast
        draw.ellipse([x - r, y - r, x + r, y + r], outline=(0, 0, 0, 200))
        return img

    def render_full_frame(
        self,
        frame_size: tuple[int, int],
        t: datetime,
    ) -> Image.Image:
        """Map tile composited onto a transparent full-frame canvas."""
        w, h = frame_size
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        tile = self.render_at(t)
        x, y = resolve_position(
            self.element.anchor,
            self.element.position,
            self.element.margin,
            frame_size=(w, h),
            element_size=(self.element.width_px, self.element.height_px),
        )
        canvas.alpha_composite(tile, (x, y))
        return canvas

    def save_sequence(
        self,
        frame_size: tuple[int, int],
        timestamps: list[datetime],
        out_dir: str | Path,
    ) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for i, t in enumerate(timestamps):
            path = out_dir / f"map_{i:06d}.png"
            self.render_full_frame(frame_size, t).save(path)
            paths.append(path)
        return paths
