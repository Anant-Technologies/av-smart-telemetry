"""FIT file parsing via fitdecode."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import fitdecode

from fitvid.models import Activity, FitEvent, Lap, PauseEvent, TelemetryPoint
from fitvid.timeutil import to_local

# Standard fields mapped onto TelemetryPoint attributes.
_RECORD_ATTR_MAP = {
    "position_lat": "lat",
    "position_long": "lon",
    "altitude": "altitude",
    "enhanced_altitude": "altitude",
    "speed": "speed",
    "enhanced_speed": "speed",
    "heart_rate": "heart_rate",
    "cadence": "cadence",
    "power": "power",
    "distance": "distance",
}

# Semicircles → degrees
_SEMICIRCLES_TO_DEG = 180.0 / (2**31)


def _to_datetime(value: Any) -> datetime | None:
    """FIT timestamps are UTC; convert to host-local for the shared timeline."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_local(value, assume_utc_if_naive=True)
    return None


def _get_field(frame: fitdecode.FitDataMessage, name: str) -> Any:
    try:
        field = frame.get_field(name)
    except KeyError:
        return None
    if field is None:
        return None
    return field.value


def _semicircles_to_deg(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) * _SEMICIRCLES_TO_DEG
    except (TypeError, ValueError):
        return None


def parse_fit(path: str | Path) -> list[Activity]:
    """Parse a FIT file into one or more Activity objects (multi-session aware)."""
    path = Path(path)
    sessions: list[dict[str, Any]] = []
    current_records: list[TelemetryPoint] = []
    current_laps: list[Lap] = []
    current_events: list[FitEvent] = []
    all_records: list[TelemetryPoint] = []
    all_laps: list[dict[str, Any]] = []
    all_events: list[FitEvent] = []
    session_metas: list[dict[str, Any]] = []
    pause_open: datetime | None = None
    pauses: list[PauseEvent] = []
    fit_device: dict[str, Any] = {}

    with fitdecode.FitReader(str(path)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            name = frame.name

            if name == "file_id":
                fit_device.setdefault(
                    "manufacturer",
                    str(_get_field(frame, "manufacturer") or "") or None,
                )
                fit_device.setdefault(
                    "product",
                    _get_field(frame, "product") or _get_field(frame, "garmin_product"),
                )
                fit_device.setdefault("serial_number", _get_field(frame, "serial_number"))
                fit_device.setdefault("time_created", _to_datetime(_get_field(frame, "time_created")))

            elif name == "device_info":
                # Prefer the first meaningful device_info with manufacturer/product
                if not fit_device.get("device_index"):
                    mfr = _get_field(frame, "manufacturer")
                    prod = _get_field(frame, "product") or _get_field(frame, "garmin_product")
                    if mfr or prod:
                        fit_device["manufacturer"] = str(mfr) if mfr is not None else fit_device.get("manufacturer")
                        fit_device["product"] = prod if prod is not None else fit_device.get("product")
                        fit_device["device_index"] = _get_field(frame, "device_index")
                        fit_device["serial_number"] = (
                            _get_field(frame, "serial_number") or fit_device.get("serial_number")
                        )

            elif name == "record":
                point = _parse_record(frame)
                if point is not None:
                    all_records.append(point)
                    current_records.append(point)

            elif name == "lap":
                lap_data = _parse_lap_fields(frame, len(all_laps))
                all_laps.append(lap_data)

            elif name == "session":
                meta = {
                    "sport": str(_get_field(frame, "sport") or "unknown"),
                    "start_time": _to_datetime(_get_field(frame, "start_time")),
                    "total_elapsed_time": _get_field(frame, "total_elapsed_time"),
                    "total_timer_time": _get_field(frame, "total_timer_time"),
                    "timestamp": _to_datetime(_get_field(frame, "timestamp")),
                }
                session_metas.append(meta)

            elif name == "event":
                ts = _to_datetime(_get_field(frame, "timestamp"))
                event = str(_get_field(frame, "event") or "")
                event_type = str(_get_field(frame, "event_type") or "")
                if ts is not None:
                    all_events.append(
                        FitEvent(
                            timestamp=ts,
                            event=event,
                            event_type=event_type,
                            event_group=_get_field(frame, "event_group"),
                        )
                    )
                    # Track timer stop/start as pauses
                    if event.lower() in ("timer", "timer_trigger") or "timer" in event.lower():
                        if event_type.lower() in ("stop", "stop_all"):
                            pause_open = ts
                        elif event_type.lower() in ("start",) and pause_open is not None:
                            pauses.append(PauseEvent(start_time=pause_open, end_time=ts))
                            pause_open = None

    if not session_metas and all_records:
        session_metas.append(
            {
                "sport": "unknown",
                "start_time": all_records[0].timestamp,
                "total_elapsed_time": None,
                "total_timer_time": None,
                "timestamp": all_records[-1].timestamp,
            }
        )

    activities: list[Activity] = []
    if len(session_metas) <= 1:
        meta = session_metas[0] if session_metas else {}
        start = meta.get("start_time") or (all_records[0].timestamp if all_records else None)
        end = meta.get("timestamp")
        if end is None and all_records:
            end = all_records[-1].timestamp
        if start is None or end is None:
            warnings.warn(f"No usable session window in {path}", stacklevel=2)
            return []
        elapsed = meta.get("total_elapsed_time")
        if elapsed and start and not end:
            end = start + timedelta(seconds=float(elapsed))
        laps = [_lap_from_data(d, i) for i, d in enumerate(all_laps)]
        activities.append(
            Activity(
                sport=str(meta.get("sport") or "unknown"),
                session_start=start,
                session_end=end,
                laps=laps,
                pauses=pauses,
                records=all_records,
                events=all_events,
                source_path=str(path),
                fit_device=_clean_fit_device(fit_device),
            )
        )
    else:
        # Multi-session: partition records by session windows
        for i, meta in enumerate(session_metas):
            start = meta.get("start_time")
            end = meta.get("timestamp")
            if start is None:
                continue
            if end is None:
                elapsed = meta.get("total_elapsed_time")
                end = (
                    start + timedelta(seconds=float(elapsed))
                    if elapsed
                    else (session_metas[i + 1]["start_time"] if i + 1 < len(session_metas) else all_records[-1].timestamp)
                )
            recs = [
                r
                for r in all_records
                if start <= r.timestamp <= end  # type: ignore[operator]
            ]
            session_laps = [
                _lap_from_data(d, j)
                for j, d in enumerate(all_laps)
                if d.get("start_time") and start <= d["start_time"] <= end  # type: ignore[operator]
            ]
            session_events = [e for e in all_events if start <= e.timestamp <= end]  # type: ignore[operator]
            session_pauses = [
                p for p in pauses if start <= p.start_time <= end  # type: ignore[operator]
            ]
            activities.append(
                Activity(
                    sport=str(meta.get("sport") or "unknown"),
                    session_start=start,
                    session_end=end,
                    laps=session_laps,
                    pauses=session_pauses,
                    records=recs,
                    events=session_events,
                    source_path=str(path),
                    fit_device=_clean_fit_device(fit_device),
                )
            )

    return activities


def _clean_fit_device(raw: dict[str, Any]) -> dict[str, Any] | None:
    cleaned = {k: v for k, v in raw.items() if v is not None and v != ""}
    if not cleaned:
        return None
    parts = []
    if cleaned.get("manufacturer"):
        parts.append(str(cleaned["manufacturer"]).replace("_", " ").title())
    if cleaned.get("product") is not None:
        parts.append(str(cleaned["product"]))
    if parts:
        cleaned["label"] = " ".join(parts)
    else:
        cleaned["label"] = "FIT device"
    # Serialize datetime
    if isinstance(cleaned.get("time_created"), datetime):
        cleaned["time_created"] = cleaned["time_created"].isoformat()
    return cleaned


def _parse_record(frame: fitdecode.FitDataMessage) -> TelemetryPoint | None:
    ts = _to_datetime(_get_field(frame, "timestamp"))
    if ts is None:
        return None

    kwargs: dict[str, Any] = {"timestamp": ts, "extras": {}}
    seen_attrs: set[str] = set()

    for field in frame.fields:
        if field.name is None or field.value is None:
            continue
        if field.name == "timestamp":
            continue
        if field.name in _RECORD_ATTR_MAP:
            attr = _RECORD_ATTR_MAP[field.name]
            # Prefer enhanced_* over base when both present
            if attr in seen_attrs and not field.name.startswith("enhanced_"):
                continue
            value = field.value
            if attr in ("lat", "lon"):
                value = _semicircles_to_deg(value)
            kwargs[attr] = value
            seen_attrs.add(attr)
        elif field.name not in ("position_lat", "position_long"):
            # Keep other numeric / useful fields in extras
            if isinstance(field.value, (int, float, str, bool)):
                kwargs["extras"][field.name] = field.value

    return TelemetryPoint(**kwargs)


def _parse_lap_fields(frame: fitdecode.FitDataMessage, index: int) -> dict[str, Any]:
    start = _to_datetime(_get_field(frame, "start_time"))
    end = _to_datetime(_get_field(frame, "timestamp"))
    timer = _get_field(frame, "total_timer_time")
    return {
        "index": index,
        "start_time": start,
        "end_time": end,
        "total_timer_time": float(timer) if timer is not None else 0.0,
        "avg_speed": _get_field(frame, "enhanced_avg_speed") or _get_field(frame, "avg_speed"),
        "avg_power": _get_field(frame, "avg_power"),
        "avg_heart_rate": _get_field(frame, "avg_heart_rate"),
        "max_speed": _get_field(frame, "enhanced_max_speed") or _get_field(frame, "max_speed"),
        "trigger": str(_get_field(frame, "lap_trigger") or ""),
    }


def _lap_from_data(d: dict[str, Any], index: int) -> Lap:
    start = d.get("start_time")
    end = d.get("end_time")
    if start is None:
        start = datetime.now(timezone.utc)
    if end is None:
        end = start
    return Lap(
        index=d.get("index", index),
        start_time=start,
        end_time=end,
        total_timer_time=float(d.get("total_timer_time") or 0.0),
        avg_speed=d.get("avg_speed"),
        avg_power=d.get("avg_power"),
        avg_heart_rate=d.get("avg_heart_rate"),
        max_speed=d.get("max_speed"),
        trigger=str(d.get("trigger") or ""),
    )
