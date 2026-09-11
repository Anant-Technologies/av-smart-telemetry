"""Core data model for FIT-driven video compilation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


def _to_aware(dt: datetime) -> datetime:
    """Normalize to host-local aware time for comparisons."""
    from fitvid.timeutil import to_local

    return to_local(dt, assume_utc_if_naive=False)


@dataclass
class TelemetryPoint:
    timestamp: datetime
    lat: float | None = None
    lon: float | None = None
    altitude: float | None = None
    speed: float | None = None
    heart_rate: int | None = None
    cadence: int | None = None
    power: int | None = None
    distance: float | None = None
    # Extra fields discovered in the FIT file (grade, enhanced_*, etc.)
    extras: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        if hasattr(self, name) and name != "extras":
            return getattr(self, name)
        return self.extras.get(name)


NUMERIC_FIELDS = (
    "lat",
    "lon",
    "altitude",
    "speed",
    "heart_rate",
    "cadence",
    "power",
    "distance",
)


@dataclass
class Lap:
    index: int
    start_time: datetime
    end_time: datetime
    total_timer_time: float
    avg_speed: float | None = None
    avg_power: float | None = None
    avg_heart_rate: float | None = None
    max_speed: float | None = None
    trigger: str = ""


@dataclass
class PauseEvent:
    start_time: datetime
    end_time: datetime


@dataclass
class FitEvent:
    timestamp: datetime
    event: str
    event_type: str
    event_group: int | None = None


@dataclass
class Activity:
    sport: str
    session_start: datetime
    session_end: datetime
    laps: list[Lap]
    pauses: list[PauseEvent]
    records: list[TelemetryPoint]
    events: list[FitEvent] = field(default_factory=list)
    source_path: str | None = None
    fit_device: dict[str, Any] | None = None

    def interpolate(self, t: datetime, *, extrapolate: bool = True) -> TelemetryPoint:
        """Linearly interpolate numeric fields between bracketing records.

        GPS coordinates hold the last known value when a bracketing point is
        missing rather than snapping to (0, 0). Extra numeric fields in
        ``extras`` are interpolated the same way.

        When ``extrapolate`` is False, times outside the record span return an
        empty point (all fields None) instead of clamping to the first/last
        sample — important for overlays so misaligned clocks show blank rather
        than a frozen edge value for the whole clip.
        """
        t = _to_aware(t)
        if not self.records:
            return TelemetryPoint(timestamp=t)

        records = self.records
        t0 = _to_aware(records[0].timestamp)
        t1 = _to_aware(records[-1].timestamp)
        if t < t0:
            return _copy_point(records[0], t) if extrapolate else TelemetryPoint(timestamp=t)
        if t > t1:
            return _copy_point(records[-1], t) if extrapolate else TelemetryPoint(timestamp=t)
        if t == t0:
            return _copy_point(records[0], t)
        if t == t1:
            return _copy_point(records[-1], t)

        lo, hi = 0, len(records) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if _to_aware(records[mid].timestamp) <= t:
                lo = mid
            else:
                hi = mid

        a, b = records[lo], records[hi]
        ta = _to_aware(a.timestamp).timestamp()
        tb = _to_aware(b.timestamp).timestamp()
        tt = t.timestamp()
        if tb <= ta:
            return _copy_point(a, t)
        frac = (tt - ta) / (tb - ta)

        kwargs: dict[str, Any] = {"timestamp": t, "extras": {}}
        for name in NUMERIC_FIELDS:
            va, vb = getattr(a, name), getattr(b, name)
            kwargs[name] = _interp_or_hold(va, vb, frac, hold_a=name in ("lat", "lon"))

        extra_keys = set(a.extras) | set(b.extras)
        for key in extra_keys:
            va, vb = a.extras.get(key), b.extras.get(key)
            # FIT extras can mix numerics with enums/strings (e.g. left_right_balance).
            # Only interpolate when every present value is numeric (exclude bool).
            if _is_numeric(va) and _is_numeric(vb):
                kwargs["extras"][key] = _interp_or_hold(va, vb, frac)
            elif _is_numeric(va) and vb is None:
                kwargs["extras"][key] = va
            elif _is_numeric(vb) and va is None:
                kwargs["extras"][key] = vb
            else:
                kwargs["extras"][key] = va if frac < 0.5 else vb
        return TelemetryPoint(**kwargs)

    def overlaps_window(self, start: datetime, end: datetime) -> bool:
        """True if [start, end] overlaps the activity record span."""
        if not self.records:
            return False
        a0 = _to_aware(self.records[0].timestamp)
        a1 = _to_aware(self.records[-1].timestamp)
        return _to_aware(end) > a0 and _to_aware(start) < a1

    def to_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "session_start": self.session_start.isoformat(),
            "session_end": self.session_end.isoformat(),
            "source_path": self.source_path,
            "laps": [asdict(lap) for lap in self.laps],
            "pauses": [asdict(p) for p in self.pauses],
            "events": [asdict(e) for e in self.events],
            "record_count": len(self.records),
            "records": [_point_dict(r) for r in self.records],
        }


def _is_numeric(v: Any) -> bool:
    """True for int/float used for telemetry interpolation (not bool)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _interp_or_hold(
    a: float | int | None,
    b: float | int | None,
    frac: float,
    *,
    hold_a: bool = False,
) -> float | int | None:
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a if hold_a else a
    val = float(a) + (float(b) - float(a)) * frac
    if isinstance(a, int) and isinstance(b, int):
        return int(round(val))
    return val


def _copy_point(p: TelemetryPoint, t: datetime) -> TelemetryPoint:
    return TelemetryPoint(
        timestamp=t,
        lat=p.lat,
        lon=p.lon,
        altitude=p.altitude,
        speed=p.speed,
        heart_rate=p.heart_rate,
        cadence=p.cadence,
        power=p.power,
        distance=p.distance,
        extras=dict(p.extras),
    )


def _point_dict(p: TelemetryPoint) -> dict[str, Any]:
    d = asdict(p)
    d["timestamp"] = p.timestamp.isoformat()
    return d


TrustLevel = Literal["metadata", "manual", "sync-derived"]


@dataclass
class MediaSource:
    path: str
    start_time: datetime
    duration: float
    trust: TrustLevel = "metadata"
    clock_drift_ppm: float = 0.0

    @property
    def end_time(self) -> datetime:
        from datetime import timedelta

        return self.start_time + timedelta(seconds=self.duration)

    def media_offset(self, absolute: datetime) -> float:
        """Seconds into this media corresponding to an absolute timestamp."""
        abs_t = _to_aware(absolute).timestamp()
        start = _to_aware(self.start_time).timestamp()
        # Apply linear drift: true_elapsed = media_elapsed * (1 + ppm/1e6)
        raw = abs_t - start
        if self.clock_drift_ppm:
            return raw / (1.0 + self.clock_drift_ppm / 1e6)
        return raw


@dataclass
class VideoSource(MediaSource):
    has_audio: bool = True


@dataclass
class AudioSource(MediaSource):
    priority: int = 0


@dataclass
class ClipSpec:
    video: VideoSource
    audio: AudioSource | None
    in_time: datetime
    out_time: datetime
    reason: str


@dataclass
class SyncResult:
    source_a: str
    source_b: str
    offset_seconds: float
    drift_ppm: float
    method: Literal["cross-correlation", "clap-event", "manual"]
    confidence: float | None = None


Anchor = Literal[
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
]


@dataclass
class TextOverlayElement:
    field: str
    format: str = "{value}"
    label: str | None = None
    unit_system: Literal["metric", "imperial"] = "metric"
    anchor: Anchor | None = None
    position: tuple[float, float] | None = None
    margin: int = 24
    font_size: int = 32
    color: str = "#FFFFFF"
    outline_color: str | None = "#000000"


@dataclass
class MapOverlayElement:
    style: Literal["route-only", "tiles"] = "route-only"
    anchor: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = (
        "bottom-right"
    )
    position: tuple[float, float] | None = None
    margin: int = 24
    width_px: int = 320
    height_px: int = 320
    route_color: str = "#FF5A36"
    route_width: int = 3
    marker_color: str = "#FFFFFF"
    marker_radius: int = 6
    padding_pct: float = 0.1
    tile_provider: str | None = None
