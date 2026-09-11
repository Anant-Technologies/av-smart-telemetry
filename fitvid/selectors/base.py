"""Clip selector protocol and range merge utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from fitvid.models import Activity

TimeRange = tuple[datetime, datetime, str]


@dataclass
class MergeOptions:
    pad_before: float = 0.0
    pad_after: float = 0.0
    min_duration: float = 0.0
    max_duration: float | None = None


class ClipSelector(Protocol):
    def select(self, activity: Activity) -> list[TimeRange]:
        """Return (start, end, reason) absolute time ranges."""
        ...


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def merge_ranges(
    ranges: list[TimeRange],
    *,
    options: MergeOptions | None = None,
    session_start: datetime | None = None,
    session_end: datetime | None = None,
) -> list[TimeRange]:
    """Pad, clamp, merge overlaps, enforce min/max duration."""
    opts = options or MergeOptions()
    if not ranges:
        return []

    padded: list[TimeRange] = []
    for start, end, reason in ranges:
        start = _aware(start) - timedelta(seconds=opts.pad_before)
        end = _aware(end) + timedelta(seconds=opts.pad_after)
        if session_start is not None:
            start = max(start, _aware(session_start))
        if session_end is not None:
            end = min(end, _aware(session_end))
        if end <= start:
            continue
        duration = (end - start).total_seconds()
        if opts.max_duration is not None and duration > opts.max_duration:
            end = start + timedelta(seconds=opts.max_duration)
        padded.append((start, end, reason))

    if not padded:
        return []

    padded.sort(key=lambda r: r[0])
    merged: list[TimeRange] = [padded[0]]
    for start, end, reason in padded[1:]:
        prev_start, prev_end, prev_reason = merged[-1]
        if start <= prev_end:
            new_end = max(prev_end, end)
            new_reason = prev_reason if prev_reason == reason else f"{prev_reason}+{reason}"
            merged[-1] = (prev_start, new_end, new_reason)
        else:
            merged.append((start, end, reason))

    if opts.min_duration > 0:
        filtered: list[TimeRange] = []
        for start, end, reason in merged:
            if (end - start).total_seconds() >= opts.min_duration:
                filtered.append((start, end, reason))
        return filtered
    return merged
