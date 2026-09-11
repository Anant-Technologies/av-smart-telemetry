"""Lap-based clip selection."""

from __future__ import annotations

from fitvid.models import Activity
from fitvid.selectors.base import TimeRange


class LapSelector:
    """One range per lap, or every ``every_n`` laps grouped."""

    def __init__(self, every_n: int = 1, lap_indices: list[int] | None = None):
        if every_n < 1:
            raise ValueError("every_n must be >= 1")
        self.every_n = every_n
        self.lap_indices = set(lap_indices) if lap_indices is not None else None

    def select(self, activity: Activity) -> list[TimeRange]:
        laps = activity.laps
        if not laps:
            return []

        if self.lap_indices is not None:
            chosen = [lap for lap in laps if lap.index in self.lap_indices]
            return [
                (lap.start_time, lap.end_time, f"lap:{lap.index}")
                for lap in chosen
                if lap.end_time > lap.start_time
            ]

        if self.every_n == 1:
            return [
                (lap.start_time, lap.end_time, f"lap:{lap.index}")
                for lap in laps
                if lap.end_time > lap.start_time
            ]

        ranges: list[TimeRange] = []
        for i in range(0, len(laps), self.every_n):
            group = laps[i : i + self.every_n]
            start = group[0].start_time
            end = group[-1].end_time
            indices = ",".join(str(lap.index) for lap in group)
            if end > start:
                ranges.append((start, end, f"laps:{indices}"))
        return ranges
