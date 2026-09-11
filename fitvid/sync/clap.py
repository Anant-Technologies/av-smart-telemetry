"""Clap / sync-event detection for audio↔FIT alignment (§5.2)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from fitvid.models import Activity, SyncResult
from fitvid.sync.crosscorr import DEFAULT_SAMPLE_RATE, extract_audio_waveform


def find_transient_peaks(
    samples: np.ndarray,
    sample_rate: int,
    *,
    min_gap_seconds: float = 0.5,
    top_n: int = 5,
) -> list[tuple[float, float]]:
    """Return list of (time_seconds, strength) for sharp energy transients."""
    if samples.size == 0:
        return []
    # Envelope via abs + short moving max
    env = np.abs(samples.astype(np.float64))
    win = max(1, int(0.01 * sample_rate))
    kernel = np.ones(win) / win
    smooth = np.convolve(env, kernel, mode="same")
    # Derivative of envelope — clap = sharp rise
    deriv = np.diff(smooth, prepend=smooth[0])
    threshold = np.percentile(deriv, 99.5)
    candidates = np.where(deriv >= threshold)[0]
    if candidates.size == 0:
        # fallback: global max
        idx = int(np.argmax(env))
        return [(idx / sample_rate, float(env[idx]))]

    peaks: list[tuple[float, float]] = []
    min_gap = int(min_gap_seconds * sample_rate)
    last = -min_gap
    # Sort by strength
    strengths = [(int(i), float(deriv[i])) for i in candidates]
    strengths.sort(key=lambda x: x[1], reverse=True)
    taken: list[int] = []
    for idx, strength in strengths:
        if any(abs(idx - t) < min_gap for t in taken):
            continue
        taken.append(idx)
        peaks.append((idx / sample_rate, strength))
        if len(peaks) >= top_n:
            break
    peaks.sort(key=lambda x: x[0])
    return peaks


def detect_clap_offset(
    media_path: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_seconds: float | None = 120.0,
) -> tuple[float, float]:
    """Detect primary clap transient; return (time_in_media, strength)."""
    wav, sr = extract_audio_waveform(
        media_path, sample_rate=sample_rate, max_seconds=max_seconds
    )
    peaks = find_transient_peaks(wav, sr)
    if not peaks:
        return 0.0, 0.0
    # Prefer the strongest early peak (typical sync clap at start)
    early = [p for p in peaks if p[0] < (max_seconds or 120.0)]
    pool = early or peaks
    best = max(pool, key=lambda p: p[1])
    return best[0], best[1]


def match_clap_to_fit_event(
    activity: Activity,
    media_path: str | Path,
    media_start: datetime,
    *,
    event_names: tuple[str, ...] = ("lap", "timer", "start"),
) -> SyncResult | None:
    """Align media clap time with a nearby FIT lap/start event.

    ``offset_seconds`` is (fit_absolute - media_absolute_at_clap), i.e. how
    much to shift media_start so media clock matches FIT clock:
    ``corrected_media_start = media_start + offset``.
    """
    clap_t, strength = detect_clap_offset(media_path)
    if strength <= 0:
        return None

    media_clap_abs = media_start + timedelta(seconds=clap_t)

    # Candidate FIT events: lap starts + start/timer events
    candidates: list[datetime] = []
    for lap in activity.laps:
        candidates.append(lap.start_time)
    for ev in activity.events:
        blob = f"{ev.event} {ev.event_type}".lower()
        if any(n in blob for n in event_names):
            candidates.append(ev.timestamp)
    candidates.append(activity.session_start)

    if not candidates:
        return None

    best = min(candidates, key=lambda t: abs((t - media_clap_abs).total_seconds()))
    offset = (best - media_clap_abs).total_seconds()
    # Confidence decays with mismatch magnitude
    mismatch = abs(offset)
    confidence = max(0.0, 1.0 - mismatch / 30.0) * min(1.0, strength / (strength + 1e-6))

    return SyncResult(
        source_a=str(activity.source_path or "fit"),
        source_b=str(media_path),
        offset_seconds=offset,
        drift_ppm=0.0,
        method="clap-event",
        confidence=confidence,
    )
