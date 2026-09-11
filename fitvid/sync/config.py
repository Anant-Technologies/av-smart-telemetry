"""Sync config: FIT generator + camera/audio device clock offsets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from fitvid.models import Activity, AudioSource, SyncResult, VideoSource
from fitvid.timeutil import parse_datetime, to_local


def _aware(dt: datetime) -> datetime:
    return to_local(dt, assume_utc_if_naive=False)


def _parse_dt(value: Any) -> datetime | None:
    return parse_datetime(value, assume_utc_if_naive=False)


@dataclass
class FitGeneratorSync:
    label: str
    fit_reference: datetime
    watch_clock_at_reference: datetime | None = None
    wall_clock_at_reference: datetime | None = None
    offset_seconds: float = 0.0

    @staticmethod
    def compute_offset(
        *,
        fit_reference: datetime,
        watch_clock: datetime | None,
        wall_clock: datetime | None,
    ) -> float:
        """Seconds to add to FIT file timestamps.

        If wall_clock is set: offset = wall - watch (or wall - fit_reference if no watch).
        Else if watch differs from fit_reference: offset = watch - fit_reference
          (user says watch actually showed X while file stamped fit_reference).
        Else: 0.
        """
        fit_reference = _aware(fit_reference)
        if wall_clock is not None:
            base = _aware(watch_clock) if watch_clock is not None else fit_reference
            return (_aware(wall_clock) - base).total_seconds()
        if watch_clock is not None:
            # Correct for watch display vs stored stamp at the sync moment
            return (_aware(watch_clock) - fit_reference).total_seconds()
        return 0.0


@dataclass
class RecordingDevice:
    id: str
    label: str
    kind: Literal["video", "audio"]
    offset_seconds: float = 0.0
    device_clock_at_reference: datetime | None = None
    files: list[str] = field(default_factory=list)

    @staticmethod
    def compute_offset(
        *,
        spine_time: datetime,
        device_clock: datetime | None,
    ) -> float:
        """Seconds to add to media metadata start times.

        spine_time is the sync moment on the chosen spine (wall if set, else
        FIT reference after watch correction, else FIT file reference).
        device_clock is what the recorder showed at that moment.
        offset = spine_time - device_clock
        so corrected_start = metadata_start + offset aligns to spine.
        """
        if device_clock is None:
            return 0.0
        return (_aware(spine_time) - _aware(device_clock)).total_seconds()


@dataclass
class SyncConfig:
    fit_generator: FitGeneratorSync | None = None
    devices: list[RecordingDevice] = field(default_factory=list)

    def path_offsets(self, kind: Literal["video", "audio"] | None = None) -> dict[str, float]:
        out: dict[str, float] = {}
        for dev in self.devices:
            if kind is not None and dev.kind != kind:
                continue
            for f in dev.files:
                out[str(Path(f).resolve())] = dev.offset_seconds
                out[f] = dev.offset_seconds
        return out


def load_sync_config(path: str | Path) -> SyncConfig:
    # Native UI text fields (esp. macOS SwiftUI) can inject control chars like VT
    # (U+000B) when Tab is pressed; PyYAML rejects those as non-printable.
    raw = Path(path).read_text(encoding="utf-8")
    cleaned = "".join(
        ch if (ch in "\n\r\t" or ord(ch) >= 0x20) else " " for ch in raw
    )
    data = yaml.safe_load(cleaned) or {}
    sync = data.get("sync", data)
    fit_gen = None
    raw_fit = sync.get("fit_generator") or sync.get("fit")
    if raw_fit:
        fit_ref = _parse_dt(raw_fit.get("fit_reference") or raw_fit.get("reference"))
        if fit_ref is None:
            raise ValueError("sync.fit_generator.fit_reference is required")
        watch = _parse_dt(raw_fit.get("watch_clock") or raw_fit.get("watch_clock_at_reference"))
        wall = _parse_dt(raw_fit.get("wall_clock") or raw_fit.get("wall_clock_at_reference"))
        offset = raw_fit.get("offset_seconds")
        if offset is None:
            offset = FitGeneratorSync.compute_offset(
                fit_reference=fit_ref, watch_clock=watch, wall_clock=wall
            )
        fit_gen = FitGeneratorSync(
            label=str(raw_fit.get("label") or "FIT device"),
            fit_reference=fit_ref,
            watch_clock_at_reference=watch,
            wall_clock_at_reference=wall,
            offset_seconds=float(offset),
        )

    # Spine for media: wall if present, else FIT reference + fit_offset
    if fit_gen and fit_gen.wall_clock_at_reference is not None:
        spine = fit_gen.wall_clock_at_reference
    elif fit_gen:
        spine = fit_gen.fit_reference + timedelta(seconds=fit_gen.offset_seconds)
    else:
        spine = None

    devices: list[RecordingDevice] = []
    for raw in sync.get("devices") or []:
        kind = raw.get("kind") or "video"
        if kind not in ("video", "audio"):
            raise ValueError(f"Unknown device kind: {kind}")
        files = [str(p) for p in (raw.get("files") or [])]
        device_clock = _parse_dt(raw.get("device_clock") or raw.get("device_clock_at_reference"))
        offset = raw.get("offset_seconds")
        if offset is None:
            if spine is None:
                raise ValueError(
                    f"Device {raw.get('id')}: need offset_seconds or fit_generator "
                    "reference to derive offset from device_clock"
                )
            offset = RecordingDevice.compute_offset(
                spine_time=spine, device_clock=device_clock
            )
        devices.append(
            RecordingDevice(
                id=str(raw.get("id") or raw.get("label") or f"device-{len(devices)}"),
                label=str(raw.get("label") or raw.get("id") or "Device"),
                kind=kind,  # type: ignore[arg-type]
                offset_seconds=float(offset),
                device_clock_at_reference=device_clock,
                files=files,
            )
        )
    return SyncConfig(fit_generator=fit_gen, devices=devices)


def shift_activity(activity: Activity, offset_seconds: float) -> Activity:
    """Return a copy of Activity with all timestamps shifted by offset_seconds."""
    if abs(offset_seconds) < 1e-9:
        return activity
    delta = timedelta(seconds=offset_seconds)

    def shift_dt(dt: datetime) -> datetime:
        return _aware(dt) + delta

    records = []
    for r in activity.records:
        records.append(
            type(r)(
                timestamp=shift_dt(r.timestamp),
                lat=r.lat,
                lon=r.lon,
                altitude=r.altitude,
                speed=r.speed,
                heart_rate=r.heart_rate,
                cadence=r.cadence,
                power=r.power,
                distance=r.distance,
                extras=dict(r.extras),
            )
        )
    laps = [
        type(lap)(
            index=lap.index,
            start_time=shift_dt(lap.start_time),
            end_time=shift_dt(lap.end_time),
            total_timer_time=lap.total_timer_time,
            avg_speed=lap.avg_speed,
            avg_power=lap.avg_power,
            avg_heart_rate=lap.avg_heart_rate,
            max_speed=lap.max_speed,
            trigger=lap.trigger,
        )
        for lap in activity.laps
    ]
    pauses = [
        type(p)(start_time=shift_dt(p.start_time), end_time=shift_dt(p.end_time))
        for p in activity.pauses
    ]
    events = [
        type(e)(
            timestamp=shift_dt(e.timestamp),
            event=e.event,
            event_type=e.event_type,
            event_group=e.event_group,
        )
        for e in activity.events
    ]
    return Activity(
        sport=activity.sport,
        session_start=shift_dt(activity.session_start),
        session_end=shift_dt(activity.session_end),
        laps=laps,
        pauses=pauses,
        records=records,
        events=events,
        source_path=activity.source_path,
        fit_device=dict(activity.fit_device) if activity.fit_device else None,
    )


def apply_media_offsets(
    videos: list[VideoSource],
    audios: list[AudioSource],
    config: SyncConfig,
) -> tuple[list[VideoSource], list[AudioSource], list[SyncResult]]:
    """Apply per-device offsets to media start times; return SyncResults."""
    results: list[SyncResult] = []
    v_map = config.path_offsets("video")
    a_map = config.path_offsets("audio")

    for video in videos:
        key = str(Path(video.path).resolve())
        off = v_map.get(key, v_map.get(video.path, 0.0))
        if abs(off) > 1e-9:
            video.start_time = _aware(video.start_time) + timedelta(seconds=off)
            video.trust = "manual"
            results.append(
                SyncResult(
                    source_a="fit-spine",
                    source_b=video.path,
                    offset_seconds=off,
                    drift_ppm=0.0,
                    method="manual",
                    confidence=1.0,
                )
            )

    for audio in audios:
        key = str(Path(audio.path).resolve())
        off = a_map.get(key, a_map.get(audio.path, 0.0))
        if abs(off) > 1e-9:
            audio.start_time = _aware(audio.start_time) + timedelta(seconds=off)
            audio.trust = "manual"
            results.append(
                SyncResult(
                    source_a="fit-spine",
                    source_b=audio.path,
                    offset_seconds=off,
                    drift_ppm=0.0,
                    method="manual",
                    confidence=1.0,
                )
            )

    if config.fit_generator and abs(config.fit_generator.offset_seconds) > 1e-9:
        results.append(
            SyncResult(
                source_a="wall-or-corrected",
                source_b="fit",
                offset_seconds=config.fit_generator.offset_seconds,
                drift_ppm=0.0,
                method="manual",
                confidence=1.0,
            )
        )

    return videos, audios, results
