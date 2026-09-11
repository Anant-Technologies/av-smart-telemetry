"""Tests for metric / FPS overlay unit conversion."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fitvid.models import Activity, TelemetryPoint, TextOverlayElement
from fitvid.overlay.composite import apply_unit_system, load_overlay_config
from fitvid.overlay.text import TextOverlayRenderer
from fitvid.units import convert_si_value, normalize_unit_system, overlay_format


def test_normalize_defaults_to_fps():
    assert normalize_unit_system(None) == "fps"
    assert normalize_unit_system("imperial") == "fps"
    assert normalize_unit_system("FPS") == "fps"
    assert normalize_unit_system("metric") == "metric"


def test_convert_speed_altitude_temp():
    assert convert_si_value("speed", 10.0, "metric") == 36.0  # m/s → km/h
    assert convert_si_value("speed", 10.0, "fps") == pytest.approx(22.36936)
    assert convert_si_value("altitude", 100.0, "fps") == pytest.approx(328.084)
    assert convert_si_value("distance", 1609.344, "fps") == pytest.approx(1.0)
    assert convert_si_value("distance", 1000.0, "metric") == 1000.0
    assert convert_si_value("temperature", 20.0, "fps") == pytest.approx(68.0)
    assert convert_si_value("temperature", 20.0, "metric") == 20.0


def test_overlay_format_catalog():
    assert "mph" in overlay_format("speed", "fps")
    assert "km/h" in overlay_format("speed", "metric")
    assert "ft" in overlay_format("altitude", "fps")
    assert "mi" in overlay_format("distance", "fps")


def test_text_renderer_fps_default():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    act = Activity(
        "ride",
        start,
        start,
        [],
        [],
        [TelemetryPoint(start, speed=10.0, altitude=100.0)],
    )
    el = TextOverlayElement(
        field="speed",
        format="{value:.1f} mph",
        unit_system="fps",
    )
    text = TextOverlayRenderer([el], act).format_value(el, start)
    assert "22.4 mph" in text


def test_load_overlay_defaults_fps(tmp_path):
    p = tmp_path / "ov.yaml"
    p.write_text(
        """
overlay:
  text:
    - field: speed
      position: [0.02, 0.88]
""",
        encoding="utf-8",
    )
    cfg = load_overlay_config(p)
    assert cfg.unit_system == "fps"
    assert cfg.text[0].unit_system == "fps"
    assert "mph" in cfg.text[0].format


def test_format_range_caption_fps():
    from fitvid.units import format_range_caption

    cap = format_range_caption("altitude", 100.0, 200.0, "fps", si_unit="m")
    assert "ft" in cap
    assert "328" in cap or "656" in cap
    cap_d = format_range_caption("distance", 1609.344, 3218.688, "fps", si_unit="m")
    assert "mi" in cap_d
    cap_m = format_range_caption("speed", 10.0, 20.0, "metric", si_unit="m/s")
    assert "km/h" in cap_m


def test_apply_unit_system_rewrites_formats(tmp_path):
    p = tmp_path / "ov.yaml"
    p.write_text(
        """
overlay:
  unit_system: fps
  text:
    - field: speed
      format: "{value:.1f} mph"
      position: [0.02, 0.88]
""",
        encoding="utf-8",
    )
    cfg = load_overlay_config(p)
    apply_unit_system(cfg, "metric")
    assert cfg.unit_system == "metric"
    assert cfg.text[0].unit_system == "metric"
    assert "km/h" in cfg.text[0].format
