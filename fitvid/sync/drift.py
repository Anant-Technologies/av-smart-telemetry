"""Clock drift correction for long sessions (§5.3)."""

from __future__ import annotations

from datetime import datetime, timezone

from fitvid.models import SyncResult


def _ts(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def compute_drift_ppm(
    offset_start: float,
    offset_end: float,
    t_start: datetime,
    t_end: datetime,
) -> float:
    """Compute clock drift in parts-per-million from two sync events."""
    duration = _ts(t_end) - _ts(t_start)
    if duration <= 0:
        return 0.0
    # offset grows by (offset_end - offset_start) over duration seconds
    return ((offset_end - offset_start) / duration) * 1_000_000.0


def interpolate_offset(
    t: datetime,
    offset_start: float,
    offset_end: float,
    t_start: datetime,
    t_end: datetime,
) -> float:
    """Linear offset interpolation between start and end sync events."""
    duration = _ts(t_end) - _ts(t_start)
    if duration <= 0:
        return offset_start
    frac = (_ts(t) - _ts(t_start)) / duration
    frac = max(0.0, min(1.0, frac))
    return offset_start + (offset_end - offset_start) * frac


def apply_drift_offset(
    t: datetime,
    *,
    sync_start: SyncResult | None,
    sync_end: SyncResult | None,
    t_start: datetime,
    t_end: datetime,
) -> tuple[float, float, bool]:
    """Return (offset_at_t, drift_ppm, assumed_zero_drift).

    When only one sync event exists, assume zero drift and flag that.
    """
    if sync_start is not None and sync_end is not None:
        ppm = compute_drift_ppm(
            sync_start.offset_seconds,
            sync_end.offset_seconds,
            t_start,
            t_end,
        )
        off = interpolate_offset(
            t,
            sync_start.offset_seconds,
            sync_end.offset_seconds,
            t_start,
            t_end,
        )
        return off, ppm, False
    if sync_start is not None:
        return sync_start.offset_seconds, 0.0, True
    if sync_end is not None:
        return sync_end.offset_seconds, 0.0, True
    return 0.0, 0.0, True
