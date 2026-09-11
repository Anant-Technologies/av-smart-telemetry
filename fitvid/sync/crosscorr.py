"""Audio↔video cross-correlation sync (§5.1)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

from fitvid.media import FFmpegError, find_binary
from fitvid.models import SyncResult

DEFAULT_SAMPLE_RATE = 8000
CONFIDENCE_FLOOR = 0.15


def extract_audio_waveform(
    path: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_seconds: float | None = None,
) -> tuple[np.ndarray, int]:
    """Extract mono PCM float32 waveform via ffmpeg."""
    ffmpeg = find_binary("ffmpeg")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
    ]
    if max_seconds is not None:
        cmd.extend(["-t", str(max_seconds)])
    cmd.append("pipe:1")
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"Failed to extract audio from {path}: {exc.stderr.decode(errors='replace')}"
        ) from exc
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    if audio.size == 0:
        raise FFmpegError(f"No audio samples extracted from {path}")
    return audio, sample_rate


def correlate_waveforms(
    reference: np.ndarray,
    target: np.ndarray,
    sample_rate: int,
) -> tuple[float, float]:
    """Return (offset_seconds, confidence).

    Positive offset means ``target`` starts later than ``reference``
    (i.e. target should be shifted left / starts further into the file
    relative to reference's t=0).
    """
    a = reference.astype(np.float64)
    b = target.astype(np.float64)
    a = a - a.mean()
    b = b - b.mean()
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom < 1e-12:
        return 0.0, 0.0

    # Correlate: lag where b aligns with a
    corr = fftconvolve(a, b[::-1], mode="full")
    peak_idx = int(np.argmax(np.abs(corr)))
    peak = float(corr[peak_idx])
    # Confidence: peak vs RMS of correlation floor
    floor = float(np.sqrt(np.mean(corr**2))) + 1e-12
    confidence = min(1.0, abs(peak) / (floor * 10.0))  # heuristic scale
    # Also normalize by energy
    confidence = min(1.0, max(confidence, abs(peak) / denom))

    # Lag relative to zero (when signals are same length aligned at start)
    lag_samples = peak_idx - (len(b) - 1)
    offset_seconds = lag_samples / float(sample_rate)
    return offset_seconds, float(confidence)


def correlate_audio_files(
    video_path: str | Path,
    audio_path: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_seconds: float | None = 600.0,
    min_confidence: float = CONFIDENCE_FLOOR,
) -> SyncResult:
    """Cross-correlate standalone audio against video's embedded track."""
    video_wav, sr = extract_audio_waveform(
        video_path, sample_rate=sample_rate, max_seconds=max_seconds
    )
    audio_wav, _ = extract_audio_waveform(
        audio_path, sample_rate=sample_rate, max_seconds=max_seconds
    )
    offset, confidence = correlate_waveforms(video_wav, audio_wav, sr)
    method: SyncResult.__annotations__  # silence
    result = SyncResult(
        source_a=str(video_path),
        source_b=str(audio_path),
        offset_seconds=offset,
        drift_ppm=0.0,
        method="cross-correlation",
        confidence=confidence,
    )
    if confidence < min_confidence:
        # Still return, caller should fall back; confidence flags low trust
        pass
    return result


def write_temp_wav(path: str | Path, sample_rate: int = DEFAULT_SAMPLE_RATE) -> Path:
    """Extract audio to a temp wav file (for clap detection pipelines)."""
    ffmpeg = find_binary("ffmpeg")
    out = Path(tempfile.mkstemp(suffix=".wav")[1])
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out
