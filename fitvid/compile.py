"""End-to-end compile orchestrator."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from fitvid.events import EventEmitter
from fitvid.ffmpeg_ops import concat_clips, cut_clip, write_manifest
from fitvid.fit_parser import parse_fit
from fitvid.media import load_audio_source, load_video_source
from fitvid.models import Activity, AudioSource, ClipSpec, SyncResult, VideoSource
from fitvid.overlay.burn import burn_overlay
from fitvid.overlay.composite import (
    OverlayConfig,
    apply_unit_system,
    load_overlay_config,
    validate_overlay_fields,
)
from fitvid.resolve import resolve_all_videos, resolve_clips
from fitvid.selectors.base import MergeOptions, TimeRange, merge_ranges
from fitvid.selectors.lap import LapSelector
from fitvid.selectors.manual import ManualMarker, ManualTimestampSelector
from fitvid.selectors.proximity import ProximitySelector
from fitvid.selectors.threshold import ThresholdSelector
from fitvid.sync.clap import match_clap_to_fit_event
from fitvid.sync.config import apply_media_offsets, load_sync_config, shift_activity
from fitvid.sync.crosscorr import CONFIDENCE_FLOOR, correlate_audio_files
from fitvid.sync.drift import apply_drift_offset

log = logging.getLogger(__name__)


@dataclass
class CompileConfig:
    fit_path: Path
    video_paths: list[Path]
    audio_paths: list[Path] = field(default_factory=list)
    select: str = "laps"  # laps | videos | manual | path-to-yaml
    overlay_path: Path | None = None
    out_path: Path = Path("highlight.mp4")
    pad_before: float = 0.0
    pad_after: float = 0.0
    min_duration: float = 0.0
    max_duration: float | None = None
    video_starts: list[datetime | None] = field(default_factory=list)
    audio_starts: list[datetime | None] = field(default_factory=list)
    video_durations: list[float | None] = field(default_factory=list)
    audio_durations: list[float | None] = field(default_factory=list)
    manual_markers: list[ManualMarker] = field(default_factory=list)
    multi_camera: bool = False
    sync_mode: str = "auto"  # auto | manual | none
    sync_config_path: Path | None = None
    dry_run: bool = False
    activity_index: int = 0
    reencode: bool = False
    overlay_fps: float = 10.0
    # Overlay measurement system: fps (US customary, default) | metric
    unit_system: str | None = None
    events: EventEmitter | None = None


@dataclass
class CompileResult:
    out_path: Path | None
    ranges: list[TimeRange]
    clips: list[ClipSpec]
    gaps: list[str]
    sync_results: list[SyncResult]
    manifest_path: Path | None
    assumed_zero_drift: bool
    activity: Activity


def load_select_config(path: Path) -> list[Any]:
    data = yaml.safe_load(path.read_text()) or {}
    return list(data.get("select") or [])


def is_videos_select(select: str) -> bool:
    """True when compile should concat all videos by creation time."""
    if select in ("videos", "all"):
        return True
    path = Path(select)
    if not path.exists():
        return False
    try:
        rules = load_select_config(path)
    except Exception:
        return False
    if not rules:
        return False
    return any((r.get("type") in ("videos", "all")) for r in rules)


def build_selectors(
    select: str,
    *,
    manual_markers: list[ManualMarker] | None = None,
) -> list[Any]:
    if select in ("videos", "all"):
        return []  # handled via resolve_all_videos
    if select == "laps":
        return [LapSelector()]
    if select == "manual":
        if not manual_markers:
            raise ValueError("select=manual requires manual markers")
        return [ManualTimestampSelector(manual_markers)]
    path = Path(select)
    if not path.exists():
        raise ValueError(f"Unknown select mode or missing config: {select}")
    rules = load_select_config(path)
    selectors: list[Any] = []
    for rule in rules:
        rtype = rule.get("type")
        if rtype in ("videos", "all"):
            continue  # exclusive videos mode is detected by is_videos_select
        if rtype == "laps" or rtype == "lap":
            selectors.append(
                LapSelector(
                    every_n=int(rule.get("every_n", 1)),
                    lap_indices=rule.get("lap_indices"),
                )
            )
        elif rtype == "manual":
            markers = [
                ManualMarker(
                    time_or_offset=m.get("offset", m.get("time")),
                    label=m.get("label", "manual"),
                    duration_before=float(m.get("duration_before", 5)),
                    duration_after=float(m.get("duration_after", 5)),
                )
                for m in rule.get("markers") or []
            ]
            # resolve ISO times
            resolved = []
            for m in markers:
                t = m.time_or_offset
                if isinstance(t, str):
                    t = datetime.fromisoformat(t.replace("Z", "+00:00"))
                resolved.append(
                    ManualMarker(t, m.label, m.duration_before, m.duration_after)
                )
            selectors.append(ManualTimestampSelector(resolved))
        elif rtype == "threshold":
            selectors.append(
                ThresholdSelector(
                    field=rule.get("field"),
                    op=rule.get("op", ">"),
                    value=rule.get("value"),
                    min_duration=float(rule.get("min_duration", 0)),
                    preset=rule.get("preset"),
                    and_conditions=rule.get("and"),
                )
            )
        elif rtype == "proximity":
            selectors.append(
                ProximitySelector(
                    lat=float(rule["lat"]),
                    lon=float(rule["lon"]),
                    radius_m=float(rule.get("radius_m", 50)),
                    min_duration=float(rule.get("min_duration", 0)),
                )
            )
        else:
            raise ValueError(f"Unknown select type: {rtype}")
    return selectors


def _apply_sync(
    activity: Activity,
    videos: list[VideoSource],
    audios: list[AudioSource],
    sync_mode: str,
) -> tuple[list[VideoSource], list[AudioSource], list[SyncResult], bool]:
    sync_results: list[SyncResult] = []
    assumed_zero = True

    if sync_mode == "none":
        return videos, audios, sync_results, True

    # Audio↔video cross-correlation
    if sync_mode == "auto" and audios and videos:
        for video in videos:
            if not video.has_audio:
                continue
            for audio in audios:
                try:
                    result = correlate_audio_files(video.path, audio.path)
                    sync_results.append(result)
                    if result.confidence is not None and result.confidence < CONFIDENCE_FLOOR:
                        log.warning(
                            "Low cross-correlation confidence (%.3f) for %s ↔ %s; "
                            "not applying offset automatically",
                            result.confidence,
                            video.path,
                            audio.path,
                        )
                        continue
                    # Interpret offset: positive means audio starts later in the
                    # correlation window relative to video — shift audio start
                    from datetime import timedelta

                    audio.start_time = audio.start_time + timedelta(
                        seconds=-result.offset_seconds
                    )
                    audio.trust = "sync-derived"
                    log.info(
                        "Applied cross-corr offset %.3fs (confidence %.3f)",
                        -result.offset_seconds,
                        result.confidence or 0,
                    )
                except Exception as exc:
                    log.warning("Cross-correlation failed: %s", exc)

    # Clap ↔ FIT for each video (and audio)
    if sync_mode == "auto":
        for video in videos:
            try:
                clap = match_clap_to_fit_event(activity, video.path, video.start_time)
                if clap is not None:
                    sync_results.append(clap)
                    if clap.confidence and clap.confidence > 0.3:
                        from datetime import timedelta

                        video.start_time = video.start_time + timedelta(
                            seconds=clap.offset_seconds
                        )
                        video.trust = "sync-derived"
                        log.info(
                            "Applied clap-FIT offset %.3fs to video %s",
                            clap.offset_seconds,
                            video.path,
                        )
            except Exception as exc:
                log.warning("Clap sync failed for %s: %s", video.path, exc)

    # Drift: only one sync pair typically → flag zero drift
    if sync_results:
        # If we somehow have start/end, apply; else flag
        assumed_zero = True
        for media in list(videos) + list(audios):
            off, ppm, zero = apply_drift_offset(
                media.start_time,
                sync_start=sync_results[0] if sync_results else None,
                sync_end=sync_results[1] if len(sync_results) > 1 else None,
                t_start=activity.session_start,
                t_end=activity.session_end,
            )
            media.clock_drift_ppm = ppm
            assumed_zero = assumed_zero and zero

    return videos, audios, sync_results, assumed_zero


def compile_video(cfg: CompileConfig) -> CompileResult:
    ev = cfg.events or EventEmitter(enabled=False)
    ev.emit(
        "start",
        fit=str(cfg.fit_path),
        videos=[str(p) for p in cfg.video_paths],
        audios=[str(p) for p in cfg.audio_paths],
        dry_run=cfg.dry_run,
    )

    activities = parse_fit(cfg.fit_path)
    if not activities:
        raise ValueError(f"No activities found in {cfg.fit_path}")
    if cfg.activity_index >= len(activities):
        raise ValueError(
            f"activity_index {cfg.activity_index} out of range "
            f"(file has {len(activities)} session(s))"
        )
    activity = activities[cfg.activity_index]
    ev.log(f"Loaded activity ({activity.sport}), {len(activity.records)} records")

    sync_results: list[SyncResult] = []
    assumed_zero = True

    if cfg.sync_config_path:
        sync_cfg = load_sync_config(cfg.sync_config_path)
        if sync_cfg.fit_generator and abs(sync_cfg.fit_generator.offset_seconds) > 1e-9:
            activity = shift_activity(activity, sync_cfg.fit_generator.offset_seconds)
            ev.log(
                f"Applied FIT generator offset "
                f"{sync_cfg.fit_generator.offset_seconds:+.3f}s "
                f"({sync_cfg.fit_generator.label})"
            )
        # Defer media offsets until sources are loaded
    else:
        sync_cfg = None

    overlay: OverlayConfig | None = None
    if cfg.overlay_path:
        overlay = load_overlay_config(cfg.overlay_path)
        if cfg.unit_system:
            apply_unit_system(overlay, cfg.unit_system)
            ev.log(f"Overlay unit system: {overlay.unit_system}")
        validate_overlay_fields(activity, overlay)
        ev.log(f"Overlay config: {cfg.overlay_path}")

    videos: list[VideoSource] = []
    for i, path in enumerate(cfg.video_paths):
        start = cfg.video_starts[i] if i < len(cfg.video_starts) else None
        dur = cfg.video_durations[i] if i < len(cfg.video_durations) else None
        videos.append(load_video_source(path, start_time=start, duration=dur))

    audios: list[AudioSource] = []
    for i, path in enumerate(cfg.audio_paths):
        start = cfg.audio_starts[i] if i < len(cfg.audio_starts) else None
        dur = cfg.audio_durations[i] if i < len(cfg.audio_durations) else None
        audios.append(load_audio_source(path, start_time=start, duration=dur, priority=i))

    if sync_cfg is not None:
        videos, audios, manual_sync = apply_media_offsets(videos, audios, sync_cfg)
        sync_results.extend(manual_sync)
        assumed_zero = True
        # When sync-config is provided, skip auto clap/crosscorr unless sync_mode is auto
        if cfg.sync_mode == "auto":
            cfg_sync_mode = "none"
        else:
            cfg_sync_mode = cfg.sync_mode
    else:
        cfg_sync_mode = cfg.sync_mode

    ev.progress("sync", fraction=0.05)
    videos, audios, auto_sync, assumed_zero_auto = _apply_sync(
        activity, videos, audios, cfg_sync_mode
    )
    sync_results.extend(auto_sync)
    assumed_zero = assumed_zero and assumed_zero_auto

    selectors = build_selectors(cfg.select, manual_markers=cfg.manual_markers)
    videos_mode = is_videos_select(cfg.select)

    if videos_mode:
        # Video spine: full files ordered by creation/start_time. Videos that
        # do not overlap the FIT activity after clock sync are skipped.
        clips, gaps = resolve_all_videos(videos, audios, activity=activity)
        ranges = [(c.in_time, c.out_time, c.reason) for c in clips]
        skipped = sum(1 for g in gaps if "outside FIT activity" in g)
        ev.log(
            f"All-videos mode: {len(clips)} clip(s) ordered by creation time"
            + (f"; skipped {skipped} outside FIT" if skipped else ""),
        )
    else:
        raw_ranges: list[TimeRange] = []
        for sel in selectors:
            raw_ranges.extend(sel.select(activity))

        ranges = merge_ranges(
            raw_ranges,
            options=MergeOptions(
                pad_before=cfg.pad_before,
                pad_after=cfg.pad_after,
                min_duration=cfg.min_duration,
                max_duration=cfg.max_duration,
            ),
            session_start=activity.session_start,
            session_end=activity.session_end,
        )

        clips, gaps = resolve_clips(
            ranges, videos, audios, multi_camera=cfg.multi_camera
        )

    for start, end, reason in ranges:
        ev.emit(
            "range",
            start=start.isoformat(),
            end=end.isoformat(),
            reason=reason,
            duration_s=(end - start).total_seconds(),
        )

    for g in gaps:
        ev.log(g, level="warning")

    if cfg.dry_run:
        ev.emit(
            "done",
            dry_run=True,
            clips=len(clips),
            ranges=len(ranges),
            out=None,
            manifest=None,
            assumed_zero_drift=assumed_zero,
        )
        return CompileResult(
            out_path=None,
            ranges=ranges,
            clips=clips,
            gaps=gaps,
            sync_results=sync_results,
            manifest_path=None,
            assumed_zero_drift=assumed_zero,
            activity=activity,
        )

    if not clips:
        # Soft-skip uncovered ranges already happened in resolve_clips.
        # Only fail when nothing at all can be written.
        msg = (
            "No clips resolved — no videos to concatenate."
            if videos_mode
            else (
                "No clips resolved — no selected range overlaps any video. "
                "Skipped gaps: " + ("; ".join(gaps) if gaps else "(none)")
            )
        )
        ev.emit("error", message=msg)
        raise RuntimeError(msg)

    if gaps:
        ev.log(
            f"Skipping {len(gaps)} uncovered range(s); continuing with {len(clips)} clip(s)",
            level="warning",
        )

    out_path = Path(cfg.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="fitvid_compile_"))
    try:
        cut_paths: list[Path] = []
        total = len(clips)
        for i, clip in enumerate(clips):
            ev.progress("cut", clip=i, total=total, fraction=(i / max(total, 1)) * 0.7 + 0.1)
            cut_path = tmp / f"clip_{i:04d}.mp4"
            cut_clip(clip, cut_path, reencode=cfg.reencode)
            if overlay is not None and (overlay.text or overlay.map):
                ev.progress(
                    "overlay",
                    clip=i,
                    total=total,
                    fraction=(i / max(total, 1)) * 0.7 + 0.15,
                )
                burned = tmp / f"clip_{i:04d}_ov.mp4"
                burn_overlay(
                    clip,
                    cut_path,
                    activity,
                    overlay,
                    burned,
                    overlay_fps=cfg.overlay_fps,
                )
                cut_paths.append(burned)
            else:
                cut_paths.append(cut_path)

        ev.progress("concat", fraction=0.9)
        # Prefer stream-copy when intermediate clips match (already re-encoded
        # by overlay burn). Forcing another 4K libx264 pass often OOMs / fails.
        concat_clips(cut_paths, out_path, reencode=None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    manifest_path = out_path.with_suffix(out_path.suffix + ".manifest.json")
    write_manifest(
        clips,
        manifest_path,
        sync_results=sync_results,
        overlay_config=overlay.raw if overlay else None,
        gaps=gaps,
        assumed_zero_drift=assumed_zero,
    )

    ev.emit(
        "done",
        dry_run=False,
        clips=len(clips),
        ranges=len(ranges),
        out=str(out_path),
        manifest=str(manifest_path),
        assumed_zero_drift=assumed_zero,
    )

    return CompileResult(
        out_path=out_path,
        ranges=ranges,
        clips=clips,
        gaps=gaps,
        sync_results=sync_results,
        manifest_path=manifest_path,
        assumed_zero_drift=assumed_zero,
        activity=activity,
    )
