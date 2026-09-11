"""Threshold-based clip selection over any inspected field."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from fitvid.inspect import populated_field_names
from fitvid.models import Activity, TelemetryPoint
from fitvid.selectors.base import TimeRange

Op = Literal[">", ">=", "<", "<=", "between"]

PRESETS: dict[str, dict[str, Any]] = {
    "climbing": {"field": "grade", "op": ">", "value": 3.0, "min_duration": 10.0},
    "descending-fast": {
        "field": "grade",
        "op": "<",
        "value": -5.0,
        "min_duration": 5.0,
        "and": [{"field": "speed", "op": ">", "value": 8.0}],
    },
    "sprint": {"field": "power", "op": ">", "value": 400.0, "min_duration": 5.0},
}


def _get_field(point: TelemetryPoint, field: str) -> float | None:
    val = point.get(field)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _make_predicate(
    field: str,
    op: Op,
    value: float | tuple[float, float],
) -> Callable[[TelemetryPoint], bool]:
    def pred(point: TelemetryPoint) -> bool:
        v = _get_field(point, field)
        if v is None:
            return False
        if op == ">":
            return v > float(value)  # type: ignore[arg-type]
        if op == ">=":
            return v >= float(value)  # type: ignore[arg-type]
        if op == "<":
            return v < float(value)  # type: ignore[arg-type]
        if op == "<=":
            return v <= float(value)  # type: ignore[arg-type]
        if op == "between":
            lo, hi = value  # type: ignore[misc]
            return float(lo) <= v <= float(hi)
        raise ValueError(f"Unknown op: {op}")

    return pred


class ThresholdSelector:
    def __init__(
        self,
        field: str | None = None,
        op: Op = ">",
        value: float | tuple[float, float] | None = None,
        min_duration: float = 0.0,
        *,
        preset: str | None = None,
        and_conditions: list[dict[str, Any]] | None = None,
        reason: str | None = None,
    ):
        if preset is not None:
            if preset not in PRESETS:
                raise ValueError(
                    f"Unknown preset '{preset}'. Available: {', '.join(PRESETS)}"
                )
            cfg = dict(PRESETS[preset])
            self.field = str(cfg["field"])
            self.op = cfg["op"]
            self.value = cfg["value"]
            self.min_duration = float(cfg.get("min_duration", min_duration))
            self.and_conditions = list(cfg.get("and") or [])
            self.reason = reason or f"threshold:preset:{preset}"
        else:
            if field is None or value is None:
                raise ValueError("field and value required unless preset is set")
            self.field = field
            self.op = op
            self.value = value
            self.min_duration = min_duration
            self.and_conditions = list(and_conditions or [])
            self.reason = reason or f"threshold:{field}{op}{value}"

    def select(self, activity: Activity) -> list[TimeRange]:
        needed = [self.field] + [c["field"] for c in self.and_conditions]
        available = populated_field_names(activity)
        missing = [f for f in needed if f not in available]
        if missing:
            raise ValueError(
                f"ThresholdSelector: field(s) not present: {', '.join(missing)}. "
                f"Available: {', '.join(sorted(available)) or '(none)'}"
            )

        preds = [_make_predicate(self.field, self.op, self.value)]  # type: ignore[arg-type]
        for cond in self.and_conditions:
            preds.append(
                _make_predicate(cond["field"], cond["op"], cond["value"])
            )

        def match(p: TelemetryPoint) -> bool:
            return all(pred(p) for pred in preds)

        ranges: list[TimeRange] = []
        open_start: datetime | None = None
        last_ts: datetime | None = None

        for rec in activity.records:
            if match(rec):
                if open_start is None:
                    open_start = rec.timestamp
                last_ts = rec.timestamp
            else:
                if open_start is not None and last_ts is not None:
                    dur = (last_ts - open_start).total_seconds()
                    # Extend end by ~1s to cover the last matching sample
                    end = last_ts + timedelta(seconds=1)
                    if dur >= self.min_duration:
                        ranges.append((open_start, end, self.reason))
                open_start = None
                last_ts = None

        if open_start is not None and last_ts is not None:
            dur = (last_ts - open_start).total_seconds()
            end = last_ts + timedelta(seconds=1)
            if dur >= self.min_duration:
                ranges.append((open_start, end, self.reason))

        return ranges
