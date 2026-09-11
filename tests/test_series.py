"""Tests for FIT series export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fitvid.models import Activity, TelemetryPoint
from fitvid.series import export_series


def test_export_series_converts_speed_to_fps():
    start = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    records = [
        TelemetryPoint(start + timedelta(seconds=i), speed=10.0, heart_rate=120 + i)
        for i in range(0, 100, 1)
    ]
    act = Activity("ride", start, start + timedelta(seconds=99), [], [], records)
    payload = export_series(
        act, fields=["speed", "heart_rate"], max_points=20, unit_system="fps"
    )
    assert payload["unit_system"] == "fps"
    names = {f["name"] for f in payload["fields"]}
    assert names == {"speed", "heart_rate"}
    speed = next(f for f in payload["fields"] if f["name"] == "speed")
    assert speed["unit"] == "mph"
    assert speed["points"][0]["v"] == pytest.approx(22.36936)
    assert len(speed["points"]) <= 21
