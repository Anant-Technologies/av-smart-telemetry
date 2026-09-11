from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fitvid.models import Activity, Lap, TelemetryPoint
from fitvid.selectors.base import MergeOptions, merge_ranges
from fitvid.selectors.lap import LapSelector
from fitvid.selectors.manual import ManualMarker, ManualTimestampSelector
from fitvid.selectors.proximity import ProximitySelector, haversine_m
from fitvid.selectors.threshold import ThresholdSelector
from fitvid.sync.drift import compute_drift_ppm, interpolate_offset
from fitvid.overlay.composite import resolve_position


def _activity(n: int = 100, hz: float = 1.0) -> Activity:
    start = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    records = []
    for i in range(n):
        t = start + timedelta(seconds=i / hz)
        records.append(
            TelemetryPoint(
                timestamp=t,
                lat=37.0 + i * 0.0001,
                lon=-122.0 + i * 0.0001,
                speed=5.0 + (i % 10),
                heart_rate=120 + (i % 20),
                power=200 + (10 if i % 30 > 20 else 0),
                altitude=100.0,
                distance=float(i),
                extras={"grade": 5.0 if 40 <= i < 60 else 0.0},
            )
        )
    laps = [
        Lap(0, start, start + timedelta(seconds=50), 50.0),
        Lap(1, start + timedelta(seconds=50), start + timedelta(seconds=100), 50.0),
    ]
    return Activity(
        sport="cycling",
        session_start=start,
        session_end=start + timedelta(seconds=n / hz),
        laps=laps,
        pauses=[],
        records=records,
    )


def test_interpolate_midpoint():
    act = _activity(10)
    t0 = act.records[0].timestamp
    t1 = act.records[1].timestamp
    mid = t0 + (t1 - t0) / 2
    p = act.interpolate(mid)
    assert p.speed == pytest.approx(
        (act.records[0].speed + act.records[1].speed) / 2
    )


def test_interpolate_holds_gps_when_missing():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    records = [
        TelemetryPoint(start, lat=1.0, lon=2.0, speed=1.0),
        TelemetryPoint(start + timedelta(seconds=1), lat=None, lon=None, speed=2.0),
    ]
    act = Activity("run", start, start + timedelta(seconds=1), [], [], records)
    p = act.interpolate(start + timedelta(milliseconds=500))
    assert p.lat == pytest.approx(1.0)
    assert p.lon == pytest.approx(2.0)
    assert p.speed == pytest.approx(1.5)


def test_interpolate_no_extrapolate_outside_span():
    act = _activity(10)
    before = act.session_start - timedelta(seconds=30)
    after = act.session_end + timedelta(seconds=30)
    p0 = act.interpolate(before, extrapolate=False)
    p1 = act.interpolate(after, extrapolate=False)
    assert p0.speed is None and p0.lat is None
    assert p1.speed is None and p1.lat is None
    mid = act.records[5].timestamp
    p_mid = act.interpolate(mid, extrapolate=False)
    assert p_mid.speed is not None


def test_interpolate_string_and_mixed_extras():
    """Non-numeric FIT extras (e.g. left/right balance enums) must not crash."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    records = [
        TelemetryPoint(
            start,
            speed=1.0,
            extras={"side": "left", "grade": 1.0, "flag": True},
        ),
        TelemetryPoint(
            start + timedelta(seconds=1),
            speed=2.0,
            extras={"side": "right", "grade": 3.0, "flag": False},
        ),
    ]
    act = Activity("ride", start, start + timedelta(seconds=1), [], [], records)
    p = act.interpolate(start + timedelta(milliseconds=500), extrapolate=False)
    assert p.extras["side"] in ("left", "right")
    assert p.extras["grade"] == pytest.approx(2.0)
    assert p.extras["flag"] in (True, False)

    # Numeric on one side, string on the other — hold nearest, do not float().
    mixed = [
        TelemetryPoint(start, extras={"bal": 50}),
        TelemetryPoint(start + timedelta(seconds=1), extras={"bal": "right"}),
    ]
    act2 = Activity("ride", start, start + timedelta(seconds=1), [], [], mixed)
    p2 = act2.interpolate(start + timedelta(milliseconds=750), extrapolate=False)
    assert p2.extras["bal"] == "right"


def test_lap_selector():
    act = _activity()
    ranges = LapSelector().select(act)
    assert len(ranges) == 2
    assert ranges[0][2] == "lap:0"


def test_manual_selector():
    act = _activity()
    sel = ManualTimestampSelector(
        [ManualMarker(30.0, "mid", duration_before=2, duration_after=2)]
    )
    ranges = sel.select(act)
    assert len(ranges) == 1
    assert "manual:mid" in ranges[0][2]


def test_merge_ranges_pads_and_merges():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ranges = [
        (start, start + timedelta(seconds=10), "a"),
        (start + timedelta(seconds=8), start + timedelta(seconds=20), "b"),
    ]
    merged = merge_ranges(ranges, options=MergeOptions(pad_before=1, pad_after=1))
    assert len(merged) == 1
    assert (merged[0][1] - merged[0][0]).total_seconds() == pytest.approx(22)


def test_threshold_selector_power():
    act = _activity()
    sel = ThresholdSelector(field="power", op=">", value=205, min_duration=2)
    ranges = sel.select(act)
    assert len(ranges) >= 1


def test_threshold_missing_field():
    act = _activity()
    sel = ThresholdSelector(field="core_temperature", op=">", value=37)
    with pytest.raises(ValueError, match="not present"):
        sel.select(act)


def test_proximity_and_haversine():
    assert haversine_m(0, 0, 0, 0) == 0
    act = _activity()
    # Center near start
    sel = ProximitySelector(37.0, -122.0, radius_m=50, min_duration=1)
    ranges = sel.select(act)
    assert len(ranges) >= 1


def test_drift_ppm():
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=1000)
    ppm = compute_drift_ppm(0.0, 0.1, t0, t1)
    assert ppm == pytest.approx(100.0)
    off = interpolate_offset(t0 + timedelta(seconds=500), 0.0, 0.1, t0, t1)
    assert off == pytest.approx(0.05)


def test_resolve_position_anchor_and_fraction():
    x, y = resolve_position("bottom-right", None, 24, frame_size=(1000, 800), element_size=(100, 50))
    assert x == 1000 - 100 - 24
    assert y == 800 - 50 - 24
    x, y = resolve_position(None, (0.5, 0.25), 0, frame_size=(1000, 800), element_size=(10, 10))
    assert x == 500
    assert y == 200
