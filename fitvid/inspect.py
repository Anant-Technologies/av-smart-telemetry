"""Field inventory for FIT activities (fitvid inspect)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from fitvid.models import Activity, NUMERIC_FIELDS

Category = Literal["common", "cycling", "running", "other"]

# Human-oriented units for common fields
FIELD_UNITS: dict[str, str] = {
    "lat": "deg",
    "lon": "deg",
    "altitude": "m",
    "speed": "m/s",
    "heart_rate": "bpm",
    "cadence": "rpm",
    "power": "W",
    "distance": "m",
    "grade": "%",
    "temperature": "C",
    "vertical_oscillation": "mm",
    "stance_time": "ms",
    "step_length": "mm",
    "vertical_ratio": "",
    "respiration_rate": "brpm",
    "left_right_balance": "%",
    "torque_effectiveness": "%",
    "pedal_smoothness": "%",
    "core_temperature": "C",
}

FIELD_LABELS: dict[str, str] = {
    "lat": "Latitude",
    "lon": "Longitude",
    "altitude": "Altitude",
    "speed": "Speed",
    "heart_rate": "Heart rate",
    "cadence": "Cadence",
    "power": "Power",
    "distance": "Distance",
    "grade": "Slope / grade",
    "temperature": "Temperature",
    "vertical_oscillation": "Vertical oscillation",
    "stance_time": "Stance time",
    "step_length": "Step length",
    "vertical_ratio": "Vertical ratio",
    "respiration_rate": "Respiration rate",
    "left_right_balance": "Left/right balance",
    "torque_effectiveness": "Torque effectiveness",
    "pedal_smoothness": "Pedal smoothness",
    "core_temperature": "Core temperature",
}

FIELD_CATEGORY: dict[str, Category] = {
    "lat": "common",
    "lon": "common",
    "altitude": "common",
    "speed": "common",
    "heart_rate": "common",
    "distance": "common",
    "grade": "common",
    "temperature": "common",
    "power": "cycling",
    "cadence": "cycling",
    "left_right_balance": "cycling",
    "torque_effectiveness": "cycling",
    "pedal_smoothness": "cycling",
    "vertical_oscillation": "running",
    "stance_time": "running",
    "step_length": "running",
    "vertical_ratio": "running",
    "respiration_rate": "other",
    "core_temperature": "other",
}

# Cadence appears in both sports; treat as common for picker grouping
FIELD_CATEGORY["cadence"] = "common"


@dataclass
class FieldStats:
    name: str
    count: int
    min: float | None
    max: float | None
    unit: str | None
    label: str = ""
    category: Category = "other"

    def __post_init__(self) -> None:
        if not self.label:
            self.label = FIELD_LABELS.get(self.name, self.name.replace("_", " ").title())
        if self.category == "other" and self.name in FIELD_CATEGORY:
            self.category = FIELD_CATEGORY[self.name]


def inventory_fields(activity: Activity) -> list[FieldStats]:
    """Return every populated record field with observed min/max and units."""
    accum: dict[str, list[float]] = {}

    for rec in activity.records:
        for name in NUMERIC_FIELDS:
            val = getattr(rec, name)
            if val is not None and isinstance(val, (int, float)):
                accum.setdefault(name, []).append(float(val))
        for key, val in rec.extras.items():
            if isinstance(val, (int, float)):
                accum.setdefault(key, []).append(float(val))

    stats: list[FieldStats] = []
    for name, values in sorted(accum.items()):
        stats.append(
            FieldStats(
                name=name,
                count=len(values),
                min=min(values) if values else None,
                max=max(values) if values else None,
                unit=FIELD_UNITS.get(name),
                label=FIELD_LABELS.get(name, name.replace("_", " ").title()),
                category=FIELD_CATEGORY.get(name, "other"),
            )
        )
    # Prefer common → cycling → running → other, then name
    order = {"common": 0, "cycling": 1, "running": 2, "other": 3}
    stats.sort(key=lambda s: (order.get(s.category, 9), s.name))
    return stats


def populated_field_names(activity: Activity) -> set[str]:
    return {s.name for s in inventory_fields(activity)}


def inspect_payload(activity: Activity, *, session_index: int = 0) -> dict[str, Any]:
    """JSON shape consumed by native UI field pickers."""
    has_gps = any(r.lat is not None and r.lon is not None for r in activity.records)
    return {
        "sport": activity.sport,
        "session_index": session_index,
        "session_start": activity.session_start.isoformat(),
        "session_end": activity.session_end.isoformat(),
        "record_count": len(activity.records),
        "laps": len(activity.laps),
        "pauses": len(activity.pauses),
        "events": len(activity.events),
        "has_gps": has_gps,
        "source_path": activity.source_path,
        "fit_device": activity.fit_device,
        "fields": [asdict(s) for s in inventory_fields(activity)],
        "lap_times": [
            {
                "index": lap.index,
                "start_time": lap.start_time.isoformat(),
                "end_time": lap.end_time.isoformat(),
            }
            for lap in activity.laps
        ],
    }


def format_inventory(activity: Activity) -> str:
    lines = [
        f"Sport: {activity.sport}",
        f"Session: {activity.session_start.isoformat()} → {activity.session_end.isoformat()}",
        f"Records: {len(activity.records)}",
        f"Laps: {len(activity.laps)}",
        f"Pauses: {len(activity.pauses)}",
        f"Events: {len(activity.events)}",
        "",
        f"{'FIELD':<28} {'COUNT':>8} {'MIN':>14} {'MAX':>14} {'UNIT':<8} {'CAT':<8}",
        "-" * 86,
    ]
    for s in inventory_fields(activity):
        unit = s.unit or ""
        mn = f"{s.min:.4g}" if s.min is not None else "-"
        mx = f"{s.max:.4g}" if s.max is not None else "-"
        lines.append(
            f"{s.name:<28} {s.count:>8} {mn:>14} {mx:>14} {unit:<8} {s.category:<8}"
        )
    return "\n".join(lines)


def require_fields(activity: Activity, fields: list[str], context: str) -> None:
    """Fail fast if requested fields are absent."""
    available = populated_field_names(activity)
    missing = [f for f in fields if f not in available]
    if missing:
        raise ValueError(
            f"{context}: field(s) not present in FIT file: {', '.join(missing)}. "
            f"Available: {', '.join(sorted(available)) or '(none)'}"
        )
