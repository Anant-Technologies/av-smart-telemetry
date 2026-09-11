"""GPS proximity-based clip selection."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from fitvid.models import Activity
from fitvid.selectors.base import TimeRange


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


class ProximitySelector:
    def __init__(
        self,
        lat: float,
        lon: float,
        radius_m: float,
        *,
        min_duration: float = 0.0,
        gap_tolerance_s: float = 5.0,
        reason: str | None = None,
    ):
        self.lat = lat
        self.lon = lon
        self.radius_m = radius_m
        self.min_duration = min_duration
        self.gap_tolerance_s = gap_tolerance_s
        self.reason = reason or f"proximity:{lat:.5f},{lon:.5f}±{radius_m}m"

    def select(self, activity: Activity) -> list[TimeRange]:
        # Build per-record inside flags; GPS dropouts hold last known state
        flags: list[tuple[datetime, bool]] = []
        last_known = False
        have_fix = False
        for rec in activity.records:
            if rec.lat is None or rec.lon is None:
                flags.append((rec.timestamp, last_known if have_fix else False))
                continue
            inside = haversine_m(self.lat, self.lon, rec.lat, rec.lon) <= self.radius_m
            last_known = inside
            have_fix = True
            flags.append((rec.timestamp, inside))

        ranges: list[TimeRange] = []
        open_start: datetime | None = None
        last_inside: datetime | None = None

        def close(end: datetime) -> None:
            nonlocal open_start, last_inside
            if open_start is None or last_inside is None:
                return
            end = last_inside + timedelta(seconds=1)
            if (end - open_start).total_seconds() >= self.min_duration:
                ranges.append((open_start, end, self.reason))
            open_start = None
            last_inside = None

        for ts, inside in flags:
            if inside:
                if open_start is None:
                    open_start = ts
                last_inside = ts
            else:
                if open_start is not None and last_inside is not None:
                    gap = (ts - last_inside).total_seconds()
                    if gap > self.gap_tolerance_s:
                        close(ts)

        if open_start is not None:
            close(activity.session_end)

        return ranges
