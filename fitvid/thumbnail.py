"""Extract a single-frame video thumbnail via ffmpeg."""

from __future__ import annotations

from pathlib import Path

from fitvid.ffmpeg_ops import run_ffmpeg
from fitvid.media import probe_media


def extract_thumbnail(
    video: str | Path,
    out_path: str | Path,
    *,
    time_s: float | None = None,
    width: int = 320,
) -> Path:
    """Write a JPEG/PNG thumbnail near ``time_s`` (default ~10% into the clip)."""
    video = Path(video)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not video.exists():
        raise FileNotFoundError(video)

    if time_s is None:
        try:
            meta = probe_media(video)
            dur = float(meta.get("duration") or 0.0)
            time_s = max(0.0, min(dur * 0.1, max(0.0, dur - 0.05))) if dur > 0 else 0.5
        except Exception:
            time_s = 0.5

    # Even width for encoders; keep aspect via scale
    w = max(32, int(width))
    if w % 2:
        w += 1
    run_ffmpeg(
        [
            "-ss",
            f"{time_s:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={w}:-2",
            "-q:v",
            "3",
            str(out_path),
        ]
    )
    return out_path
