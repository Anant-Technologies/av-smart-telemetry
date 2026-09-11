from fitvid.events import EventEmitter
from fitvid.inspect import FieldStats, inspect_payload, inventory_fields

from fitvid.models import Activity, TelemetryPoint
from datetime import datetime, timezone, timedelta


def test_inspect_payload_has_labels():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    records = [
        TelemetryPoint(start + timedelta(seconds=i), speed=5.0, heart_rate=120, power=200)
        for i in range(10)
    ]
    act = Activity("cycling", start, start + timedelta(seconds=10), [], [], records)
    payload = inspect_payload(act)
    names = {f["name"] for f in payload["fields"]}
    assert "speed" in names
    assert "heart_rate" in names
    speed = next(f for f in payload["fields"] if f["name"] == "speed")
    assert speed["label"] == "Speed"
    assert speed["category"] == "common"


def test_inspect_includes_fit_device_when_present():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    act = Activity(
        "cycling",
        start,
        start + timedelta(seconds=10),
        [],
        [],
        [TelemetryPoint(start, speed=1.0)],
        fit_device={"label": "Garmin Edge 840", "manufacturer": "garmin"},
    )
    payload = inspect_payload(act)
    assert payload["fit_device"]["label"] == "Garmin Edge 840"


def test_overlay_auto_stacks_duplicate_anchors(tmp_path):
    from fitvid.overlay.composite import load_overlay_config

    path = tmp_path / "ov.yaml"
    path.write_text(
        """
overlay:
  text:
    - field: altitude
      format: "{value:.0f} m"
      anchor: bottom-left
    - field: speed
      format: "{value}"
      anchor: bottom-right
    - field: heart_rate
      format: "{value}"
      anchor: top-left
    - field: cadence
      format: "{value}"
      anchor: top-right
    - field: distance
      format: "{value}"
      anchor: center
    - field: power
      format: "{value}"
      anchor: bottom-left
  map:
    style: route-only
    anchor: top-right
    width_px: 280
    height_px: 280
"""
    )
    cfg = load_overlay_config(path)
    assert len(cfg.text) == 6
    # After auto-stack, positions should be unique
    positions = [el.position for el in cfg.text]
    assert all(p is not None for p in positions)
    assert len(set(positions)) == 6
