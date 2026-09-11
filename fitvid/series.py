"""Export downsampled FIT telemetry series for native UI charts."""

from __future__ import annotations

from typing import Any

from fitvid.models import Activity, NUMERIC_FIELDS
from fitvid.units import convert_si_value, display_unit, normalize_unit_system


def _field_value(rec, name: str) -> float | None:
    if name in NUMERIC_FIELDS:
        val = getattr(rec, name)
    else:
        val = rec.extras.get(name)
    if val is None or not isinstance(val, (int, float)):
        return None
    return float(val)


def export_series(
    activity: Activity,
    *,
    fields: list[str] | None = None,
    max_points: int = 800,
    unit_system: str = "fps",
) -> dict[str, Any]:
    """Return chart-ready series for selected (or all populated) fields.

    Values are converted into the requested measurement system. Timestamps are
    host-local ISO strings (same as inspect).
    """
    system = normalize_unit_system(unit_system)
    if not activity.records:
        return {
            "session_start": activity.session_start.isoformat(),
            "session_end": activity.session_end.isoformat(),
            "unit_system": system,
            "fields": [],
        }

    # Discover populated fields if not specified
    if not fields:
        seen: list[str] = []
        for rec in activity.records:
            for name in NUMERIC_FIELDS:
                if name in ("lat", "lon"):
                    continue
                if _field_value(rec, name) is not None and name not in seen:
                    seen.append(name)
            for key, val in rec.extras.items():
                if isinstance(val, (int, float)) and key not in seen:
                    seen.append(key)
        fields = seen

    # Skip non-chartable GPS coords by default even if requested
    fields = [f for f in fields if f not in ("lat", "lon")]

    stride = max(1, len(activity.records) // max(1, max_points))
    sampled = activity.records[::stride]
    if sampled[-1] is not activity.records[-1]:
        sampled = list(sampled) + [activity.records[-1]]

    from fitvid.inspect import FIELD_LABELS, FIELD_UNITS

    out_fields: list[dict[str, Any]] = []
    for name in fields:
        points: list[dict[str, Any]] = []
        for rec in sampled:
            raw = _field_value(rec, name)
            if raw is None:
                continue
            points.append(
                {
                    "t": rec.timestamp.isoformat(),
                    "v": convert_si_value(name, raw, system),
                }
            )
        if not points:
            continue
        si = FIELD_UNITS.get(name)
        out_fields.append(
            {
                "name": name,
                "label": FIELD_LABELS.get(name, name.replace("_", " ").title()),
                "unit": display_unit(name, system, fallback=si),
                "points": points,
            }
        )

    return {
        "session_start": activity.session_start.isoformat(),
        "session_end": activity.session_end.isoformat(),
        "record_count": len(activity.records),
        "sampled_count": len(sampled),
        "unit_system": system,
        "fields": out_fields,
        "laps": [
            {
                "index": lap.index,
                "start_time": lap.start_time.isoformat(),
                "end_time": lap.end_time.isoformat(),
            }
            for lap in activity.laps
        ],
    }
