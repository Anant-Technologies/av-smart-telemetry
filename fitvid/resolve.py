"""Resolve absolute time ranges to ClipSpecs against video/audio sources."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fitvid.models import Activity, AudioSource, ClipSpec, VideoSource
from fitvid.selectors.base import TimeRange

log = logging.getLogger(__name__)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _overlap(
    a_start: datetime,
    a_end: datetime,
    b_start: datetime,
    b_end: datetime,
) -> tuple[datetime, datetime] | None:
    start = max(_aware(a_start), _aware(b_start))
    end = min(_aware(a_end), _aware(b_end))
    if end > start:
        return start, end
    return None


def resolve_clips(
    ranges: list[TimeRange],
    videos: list[VideoSource],
    audios: list[AudioSource] | None = None,
    *,
    multi_camera: bool = False,
    video_priority: list[str] | None = None,
) -> tuple[list[ClipSpec], list[str]]:
    """Map merged ranges onto covering video (and optional audio) sources.

    Returns (clips, gap_messages).
    """
    audios = audios or []
    gaps: list[str] = []
    clips: list[ClipSpec] = []

    # Order videos by priority list if provided, else by start_time
    ordered_videos = list(videos)
    if video_priority:
        rank = {p: i for i, p in enumerate(video_priority)}

        def sort_key(v: VideoSource) -> tuple:
            return (rank.get(v.path, len(rank)), _aware(v.start_time))

        ordered_videos.sort(key=sort_key)
    else:
        ordered_videos.sort(key=lambda v: _aware(v.start_time))

    for start, end, reason in ranges:
        start, end = _aware(start), _aware(end)
        covering = []
        for video in ordered_videos:
            ov = _overlap(start, end, video.start_time, video.end_time)
            if ov:
                covering.append((video, ov[0], ov[1]))

        if not covering:
            msg = (
                f"No video covers {start.isoformat()}–{end.isoformat()} "
                f"({reason})"
            )
            log.warning(msg)
            gaps.append(msg)
            continue

        if multi_camera:
            chosen = covering
        else:
            # Sequential / single: prefer first by priority, but if range spans
            # multiple sequential files, emit one clip per overlapping file.
            chosen = covering

        for video, clip_in, clip_out in chosen:
            audio = _pick_audio(audios, clip_in, clip_out)
            clips.append(
                ClipSpec(
                    video=video,
                    audio=audio,
                    in_time=clip_in,
                    out_time=clip_out,
                    reason=reason,
                )
            )

        # Detect uncovered sub-ranges within the requested window
        covered_spans = sorted((c[1], c[2]) for c in covering)
        cursor = start
        for c_start, c_end in covered_spans:
            if c_start > cursor:
                msg = (
                    f"Video gap {cursor.isoformat()}–{c_start.isoformat()} "
                    f"within {reason}"
                )
                log.warning(msg)
                gaps.append(msg)
            cursor = max(cursor, c_end)
        if cursor < end:
            msg = (
                f"Video gap {cursor.isoformat()}–{end.isoformat()} "
                f"within {reason}"
            )
            log.warning(msg)
            gaps.append(msg)

    clips.sort(key=lambda c: _aware(c.in_time))
    return clips, gaps


def resolve_all_videos(
    videos: list[VideoSource],
    audios: list[AudioSource] | None = None,
    *,
    activity: Activity | None = None,
) -> tuple[list[ClipSpec], list[str]]:
    """Build one full-file clip per video that overlaps the FIT activity.

    Videos are ordered by creation/start_time. After device-clock offsets, any
    video with no overlap against the activity record/session window is skipped
    (logged as a gap). Override audio is attached when it overlaps each clip.
    """
    audios = audios or []
    gaps: list[str] = []
    if not videos:
        return [], ["No videos provided"]

    ordered = sorted(videos, key=lambda v: _aware(v.start_time))
    clips: list[ClipSpec] = []
    prev_end: datetime | None = None
    kept = 0
    for video in ordered:
        start = _aware(video.start_time)
        end = _aware(video.end_time)
        if end <= start:
            msg = f"Skipping zero-length video {video.path}"
            log.warning(msg)
            gaps.append(msg)
            continue
        if activity is not None and not activity.overlaps_window(start, end):
            msg = (
                f"Skipping video outside FIT activity after clock sync: "
                f"{Path(video.path).name} ({start.isoformat()}–{end.isoformat()})"
            )
            log.warning(msg)
            gaps.append(msg)
            continue
        if prev_end is not None and start > prev_end:
            gaps.append(
                f"Gap between videos {prev_end.isoformat()}–{start.isoformat()}"
            )
        audio = _pick_audio(audios, start, end)
        clips.append(
            ClipSpec(
                video=video,
                audio=audio,
                in_time=start,
                out_time=end,
                reason=f"video:{kept}:{Path(video.path).name}",
            )
        )
        kept += 1
        prev_end = end
    return clips, gaps


def _pick_audio(
    audios: list[AudioSource],
    start: datetime,
    end: datetime,
) -> AudioSource | None:
    candidates: list[tuple[AudioSource, float]] = []
    for audio in audios:
        ov = _overlap(start, end, audio.start_time, audio.end_time)
        if ov:
            coverage = (ov[1] - ov[0]).total_seconds()
            candidates.append((audio, coverage))
    if not candidates:
        return None
    # Highest priority, then most coverage
    candidates.sort(key=lambda x: (x[0].priority, x[1]), reverse=True)
    return candidates[0][0]
