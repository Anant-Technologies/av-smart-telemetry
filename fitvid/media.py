"""Media metadata via ffprobe and MediaSource construction."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fitvid.models import AudioSource, TrustLevel, VideoSource
from fitvid.timeutil import host_local_tz, parse_wall_clock_as_local, to_local


class FFmpegError(RuntimeError):
    pass


def find_binary(name: str) -> str:
    """Locate ffmpeg/ffprobe, preferring bundled binaries when present."""
    # Bundled next to package (PyInstaller)
    import sys

    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        for candidate in (base / name, base / "ffmpeg" / name, base / f"{name}.exe"):
            if candidate.exists():
                return str(candidate)
    # Project-local bundled copy
    here = Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg"
    for candidate in (
        here / name,
        here / f"{name}.exe",
        here / "bin" / name,
    ):
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if not found:
        raise FFmpegError(
            f"'{name}' not found on PATH and no bundled binary in vendor/ffmpeg"
        )
    return found


def ffprobe_json(path: str | Path) -> dict:
    ffprobe = find_binary("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffprobe failed for {path}: {exc.stderr}") from exc
    return json.loads(result.stdout)


def _parse_timestamp(raw: str | None) -> datetime | None:
    """Parse media creation_time as host-local wall clock (local tz)."""
    return parse_wall_clock_as_local(raw)


def probe_media(path: str | Path) -> dict:
    """Return duration, start_time guess, has_audio, codec summary."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    info = ffprobe_json(path)
    fmt = info.get("format") or {}
    streams = info.get("streams") or []

    duration = float(fmt.get("duration") or 0.0)
    tags = dict(fmt.get("tags") or {})
    # Also check stream tags (do not overwrite format-level Apple capture date)
    for s in streams:
        for k, v in (s.get("tags") or {}).items():
            tags.setdefault(k, v)

    # Prefer Apple capture time over container creation_time. On iPhone MOV/MP4,
    # creation_time is often the mux/transfer/import instant (UTC), while
    # com.apple.quicktime.creationdate is when the clip was actually recorded.
    tags_l = {str(k).lower(): v for k, v in tags.items()}
    start_candidates = [
        tags_l.get("com.apple.quicktime.creationdate"),
        tags_l.get("creation_time"),
        tags_l.get("date"),
        fmt.get("start_time"),
    ]
    start_time = None
    for c in start_candidates:
        if isinstance(c, str):
            start_time = _parse_timestamp(c)
            if start_time:
                break
        elif isinstance(c, (int, float)) and float(c) > 1e9:
            # epoch seconds — absolute instant → host local
            start_time = to_local(
                datetime.fromtimestamp(float(c), tz=timezone.utc),
                assume_utc_if_naive=True,
            )
            break

    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    return {
        "path": str(path.resolve()),
        "duration": duration,
        "start_time": start_time,
        "has_video": has_video,
        "has_audio": has_audio,
        "width": int(video_stream["width"]) if video_stream and video_stream.get("width") else None,
        "height": int(video_stream["height"]) if video_stream and video_stream.get("height") else None,
        "fps": _parse_fps(video_stream.get("r_frame_rate") if video_stream else None),
        "codec": video_stream.get("codec_name") if video_stream else None,
        "pix_fmt": video_stream.get("pix_fmt") if video_stream else None,
        "sample_rate": int(audio_stream["sample_rate"])
        if audio_stream and audio_stream.get("sample_rate")
        else None,
        "tags": tags,
    }


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else None
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def probe_device_identity(path: str | Path) -> dict:
    """Return stable device id/label from ffprobe tags for grouping."""
    meta = probe_media(path)
    tags = {str(k).lower(): v for k, v in (meta.get("tags") or {}).items()}
    make = (
        tags.get("com.apple.quicktime.make")
        or tags.get("make")
        or tags.get("artist")
        or ""
    )
    model = (
        tags.get("com.apple.quicktime.model")
        or tags.get("model")
        or tags.get("device_model")
        or ""
    )
    device_id = (
        tags.get("device_id")
        or tags.get("com.apple.quicktime.cameraidentifier")
        or tags.get("serial_number")
        or ""
    )
    handler = tags.get("handler_name") or ""
    label_parts = [str(p).strip() for p in (make, model) if str(p).strip()]
    label = " ".join(label_parts) if label_parts else (handler or Path(path).parent.name or "Unknown device")
    slug_src = f"{make}|{model}|{device_id}".strip("|")
    if not slug_src or slug_src == "||":
        # Fallback: parent directory name
        slug_src = Path(path).resolve().parent.name or "unknown"
    import re

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug_src).strip("-").lower() or "unknown"
    return {
        "id": slug,
        "label": label,
        "make": str(make) if make else None,
        "model": str(model) if model else None,
        "device_id": str(device_id) if device_id else None,
        "path": meta["path"],
        "start_time": meta["start_time"].isoformat() if meta.get("start_time") else None,
        "duration": meta["duration"],
        "has_video": meta["has_video"],
        "has_audio": meta["has_audio"],
    }


def group_media_by_device(paths: list[str | Path]) -> dict[str, dict]:
    """Group paths by device id. Returns {id: {label, files: [...]}}."""
    groups: dict[str, dict] = {}
    for path in paths:
        info = probe_device_identity(path)
        g = groups.setdefault(
            info["id"],
            {"id": info["id"], "label": info["label"], "files": [], "probes": []},
        )
        g["files"].append(info["path"])
        g["probes"].append(info)
    return groups


def load_video_source(
    path: str | Path,
    *,
    start_time: datetime | None = None,
    duration: float | None = None,
    trust: TrustLevel | None = None,
    clock_drift_ppm: float = 0.0,
) -> VideoSource:
    meta = probe_media(path)
    resolved_start = start_time or meta["start_time"]
    if resolved_start is None:
        raise ValueError(
            f"No start timestamp for video {path}. Pass --video-start or set creation_time metadata."
        )
    if resolved_start.tzinfo is None:
        resolved_start = resolved_start.replace(tzinfo=host_local_tz())
    else:
        resolved_start = to_local(resolved_start, assume_utc_if_naive=False)
    resolved_trust: TrustLevel
    if trust is not None:
        resolved_trust = trust
    elif start_time is not None:
        resolved_trust = "manual"
    else:
        resolved_trust = "metadata"
    resolved_duration = float(duration) if duration is not None else float(meta["duration"])
    return VideoSource(
        path=meta["path"],
        start_time=resolved_start,
        duration=resolved_duration,
        trust=resolved_trust,
        clock_drift_ppm=clock_drift_ppm,
        has_audio=bool(meta["has_audio"]),
    )


def load_audio_source(
    path: str | Path,
    *,
    start_time: datetime | None = None,
    duration: float | None = None,
    trust: TrustLevel | None = None,
    priority: int = 0,
    clock_drift_ppm: float = 0.0,
) -> AudioSource:
    meta = probe_media(path)
    resolved_start = start_time or meta["start_time"]
    if resolved_start is None:
        raise ValueError(
            f"No start timestamp for audio {path}. Pass --audio-start or set creation_time metadata."
        )
    if resolved_start.tzinfo is None:
        resolved_start = resolved_start.replace(tzinfo=host_local_tz())
    else:
        resolved_start = to_local(resolved_start, assume_utc_if_naive=False)
    resolved_trust: TrustLevel
    if trust is not None:
        resolved_trust = trust
    elif start_time is not None:
        resolved_trust = "manual"
    else:
        resolved_trust = "metadata"
    resolved_duration = float(duration) if duration is not None else float(meta["duration"])
    return AudioSource(
        path=meta["path"],
        start_time=resolved_start,
        duration=resolved_duration,
        trust=resolved_trust,
        clock_drift_ppm=clock_drift_ppm,
        priority=priority,
    )
