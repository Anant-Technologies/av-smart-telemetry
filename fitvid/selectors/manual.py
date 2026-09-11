"""Manual timestamp clip selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fitvid.models import Activity
from fitvid.selectors.base import TimeRange


@dataclass
class ManualMarker:
    """Absolute time or offset-from-session-start, with padding windows."""

    time_or_offset: datetime | float
    label: str = "manual"
    duration_before: float = 5.0
    duration_after: float = 5.0


class ManualTimestampSelector:
    def __init__(self, markers: list[ManualMarker]):
        self.markers = markers

    def select(self, activity: Activity) -> list[TimeRange]:
        ranges: list[TimeRange] = []
        session_start = activity.session_start
        session_end = activity.session_end
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=timezone.utc)
        if session_end.tzinfo is None:
            session_end = session_end.replace(tzinfo=timezone.utc)

        for marker in self.markers:
            if isinstance(marker.time_or_offset, datetime):
                center = marker.time_or_offset
                if center.tzinfo is None:
                    center = center.replace(tzinfo=timezone.utc)
            else:
                center = session_start + timedelta(seconds=float(marker.time_or_offset))

            start = center - timedelta(seconds=marker.duration_before)
            end = center + timedelta(seconds=marker.duration_after)

            if end < session_start or start > session_end:
                raise ValueError(
                    f"Manual marker '{marker.label}' at {center.isoformat()} "
                    f"is outside activity range "
                    f"{session_start.isoformat()}–{session_end.isoformat()}"
                )
            start = max(start, session_start)
            end = min(end, session_end)
            if end > start:
                ranges.append((start, end, f"manual:{marker.label}"))
        return ranges
