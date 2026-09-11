"""Measurement system helpers for telemetry overlays.

FIT stores SI. Overlays display either:
- ``fps`` — foot–pound–second / US customary (mph, mi, ft, °F) — **default**
- ``metric`` — km/h, m, °C

``imperial`` is accepted as an alias of ``fps`` for YAML/spec compatibility.
"""

from __future__ import annotations

from typing import Literal

UnitSystem = Literal["metric", "fps"]
# Stored/accepted on TextOverlayElement (fps preferred; imperial = fps)
UnitSystemName = Literal["metric", "fps", "imperial"]

DEFAULT_UNIT_SYSTEM: UnitSystem = "fps"

FORMATS: dict[UnitSystem, dict[str, str]] = {
    "metric": {
        "speed": "{value:.1f} km/h",
        "heart_rate": "{value:.0f} bpm",
        "grade": "{value:.1f}%",
        "power": "{value:.0f} W",
        "cadence": "{value:.0f}",
        "altitude": "{value:.0f} m",
        "distance": "{value:.0f} m",
        "temperature": "{value:.1f} °C",
        "core_temperature": "{value:.1f} °C",
    },
    "fps": {
        "speed": "{value:.1f} mph",
        "heart_rate": "{value:.0f} bpm",
        "grade": "{value:.1f}%",
        "power": "{value:.0f} W",
        "cadence": "{value:.0f}",
        "altitude": "{value:.0f} ft",
        "distance": "{value:.2f} mi",
        "temperature": "{value:.1f} °F",
        "core_temperature": "{value:.1f} °F",
    },
}

# Short unit labels for field-picker range captions (SI min/max → display).
DISPLAY_UNITS: dict[UnitSystem, dict[str, str]] = {
    "metric": {
        "speed": "km/h",
        "altitude": "m",
        "distance": "m",
        "temperature": "°C",
        "core_temperature": "°C",
    },
    "fps": {
        "speed": "mph",
        "altitude": "ft",
        "distance": "mi",
        "temperature": "°F",
        "core_temperature": "°F",
        "vertical_oscillation": "in",
        "step_length": "in",
    },
}


def normalize_unit_system(
    value: str | None,
    *,
    default: UnitSystem = DEFAULT_UNIT_SYSTEM,
) -> UnitSystem:
    """Map user/YAML names onto ``metric`` or ``fps``."""
    if value is None:
        return default
    v = str(value).strip().lower()
    if not v:
        return default
    if v in ("fps", "imperial", "us", "customary", "english"):
        return "fps"
    if v in ("metric", "si", "metric_system"):
        return "metric"
    return default


def is_fps(system: str | None) -> bool:
    return normalize_unit_system(system) == "fps"


def overlay_format(field: str, system: str | None = None) -> str:
    """Default format string for a field in the given measurement system."""
    sys = normalize_unit_system(system)
    return FORMATS[sys].get(field, "{value}")


def display_unit(field: str, system: str | None = None, *, fallback: str | None = None) -> str:
    """Unit label for picker captions after converting out of SI."""
    sys = normalize_unit_system(system)
    if field in DISPLAY_UNITS[sys]:
        return DISPLAY_UNITS[sys][field]
    return fallback or ""


def format_range_caption(
    field: str,
    min_v: float | None,
    max_v: float | None,
    system: str | None,
    *,
    si_unit: str | None = None,
) -> str:
    """Human caption like ``mph  12.4 – 28.1`` for the field picker."""
    unit = display_unit(field, system, fallback=si_unit)
    def _fmt(v: float | None) -> str:
        if v is None:
            return "-"
        disp = convert_si_value(field, float(v), system)
        # Compact but readable
        if abs(disp) >= 100:
            return f"{disp:.0f}"
        if abs(disp) >= 10:
            return f"{disp:.1f}"
        return f"{disp:.3g}"

    return f"{unit}  {_fmt(min_v)} – {_fmt(max_v)}".strip()


def convert_si_value(field: str, raw: float, system: str | None) -> float:
    """Convert a FIT SI value into the display unit for ``system``."""
    if is_fps(system):
        if field == "speed":
            return float(raw) * 2.236936  # m/s → mph
        if field == "altitude":
            return float(raw) * 3.28084  # m → ft
        if field == "distance":
            return float(raw) / 1609.344  # m → mi
        if field in ("temperature", "core_temperature"):
            return float(raw) * 9.0 / 5.0 + 32.0  # °C → °F
        if field in ("vertical_oscillation", "step_length"):
            return float(raw) / 25.4  # mm → in
        return float(raw)
    # metric
    if field == "speed":
        return float(raw) * 3.6  # m/s → km/h
    return float(raw)
