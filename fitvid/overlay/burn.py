"""Burn telemetry overlays onto a cut clip via PNG sequence + ffmpeg overlay."""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from PIL import Image

from fitvid.ffmpeg_ops import run_ffmpeg
from fitvid.media import probe_media
from fitvid.models import Activity, ClipSpec
from fitvid.overlay.composite import OverlayConfig
from fitvid.overlay.map_route import RouteMapRenderer
from fitvid.overlay.text import TextOverlayRenderer

log = logging.getLogger(__name__)


def burn_overlay(
    clip: ClipSpec,
    base_video: Path,
    activity: Activity,
    overlay: OverlayConfig,
    out_path: Path,
    *,
    overlay_fps: float = 10.0,
) -> Path:
    """Composite text + map overlays onto ``base_video`` → ``out_path``."""
    meta = probe_media(base_video)
    width = meta["width"] or 1920
    height = meta["height"] or 1080
    # Prefer actual cut media duration so overlay length matches playback
    media_duration = float(meta.get("duration") or 0.0)
    clip_duration = (clip.out_time - clip.in_time).total_seconds()
    duration = media_duration if media_duration > 0.05 else clip_duration
    duration = max(0.01, duration)
    fps = float(overlay_fps) if overlay_fps > 0 else 10.0
    n_frames = max(1, int(round(duration * fps)))

    if not activity.overlaps_window(clip.in_time, clip.out_time):
        hint = 0.0
        if activity.records:
            hint = (
                activity.records[0].timestamp - clip.in_time
            ).total_seconds()
        log.warning(
            "Clip %s (%s–%s) does not overlap FIT records (%s–%s). "
            "Overlay values will be blank. Adjust device clock sync "
            "(suggested video start shift ≈ %+.1fs).",
            clip.reason,
            clip.in_time.isoformat(),
            clip.out_time.isoformat(),
            activity.records[0].timestamp.isoformat() if activity.records else "?",
            activity.records[-1].timestamp.isoformat() if activity.records else "?",
            hint,
        )

    text_renderer = (
        TextOverlayRenderer(overlay.text, activity) if overlay.text else None
    )
    map_renderer = (
        RouteMapRenderer(overlay.map, activity) if overlay.map is not None else None
    )

    tmp = Path(tempfile.mkdtemp(prefix="fitvid_overlay_"))
    try:
        for i in range(n_frames):
            # Map frame index onto absolute clip timeline (creation-time based)
            elapsed = i / fps
            if elapsed > clip_duration:
                elapsed = clip_duration
            t = clip.in_time + timedelta(seconds=elapsed)
            frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            if map_renderer is not None:
                frame = Image.alpha_composite(
                    frame, map_renderer.render_full_frame((width, height), t)
                )
            if text_renderer is not None:
                frame = Image.alpha_composite(
                    frame, text_renderer.render_frame((width, height), t)
                )
            frame.save(tmp / f"ov_{i:06d}.png")

        # Feed the PNG sequence directly — intermediate PNG-in-MOV often freezes
        # on the first frame with overlay filters on some ffmpeg builds.
        run_ffmpeg(
            [
                "-i",
                str(base_video),
                "-framerate",
                str(fps),
                "-i",
                str(tmp / "ov_%06d.png"),
                "-filter_complex",
                f"[1:v]fps={fps},format=rgba[ov];[0:v][ov]overlay=0:0:eof_action=repeat:shortest=1[v]",
                "-map",
                "[v]",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-t",
                f"{duration:.3f}",
                str(out_path),
            ]
        )
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
