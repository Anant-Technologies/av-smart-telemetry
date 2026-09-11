"""ffmpeg cut, mux, concat, and overlay burn-in helpers."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fitvid.media import FFmpegError, find_binary, probe_media
from fitvid.models import Activity, ClipSpec, SyncResult

log = logging.getLogger(__name__)


def _ffmpeg_error_message(result: subprocess.CompletedProcess) -> str:
    """Prefer the tail of stderr — ffmpeg prints the real failure last."""
    err = (result.stderr or "").strip() or (result.stdout or "").strip() or "(no output)"
    lines = err.splitlines()
    if len(lines) > 50:
        head = "\n".join(lines[:8])
        tail = "\n".join(lines[-40:])
        body = f"{head}\n… ({len(lines) - 48} lines omitted) …\n{tail}"
    else:
        body = err
    low = err.lower()
    hint = ""
    if "no space left on device" in low or "enospc" in low or result.returncode == 28:
        hint = (
            "\n\nDisk is full while writing the output. Free space on the output "
            "volume (and delete any partial highlight.mp4), then retry. "
            "Final concat now prefers stream-copy when clips already match, "
            "which uses far less space than a second 4K encode."
        )
    return f"ffmpeg failed (exit {result.returncode}):\n{body}{hint}"


def run_ffmpeg(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    ffmpeg = find_binary("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-y", *args]
    log.debug("ffmpeg %s", " ".join(cmd[1:]))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise FFmpegError(_ffmpeg_error_message(result))
    return result


def _ensure_output_space(out_path: Path, *, min_free_bytes: int = 2 * 1024**3) -> None:
    """Raise a clear error if the output volume is nearly full."""
    try:
        usage = shutil.disk_usage(out_path.parent if out_path.parent.exists() else out_path.anchor)
    except OSError:
        return
    if usage.free < min_free_bytes:
        free_gib = usage.free / (1024**3)
        raise FFmpegError(
            f"Not enough free disk space to write {out_path} "
            f"({free_gib:.1f} GiB free; need ~{min_free_bytes / (1024**3):.0f} GiB). "
            "Free space or choose another output path, then retry."
        )

def cut_clip(
    clip: ClipSpec,
    out_path: str | Path,
    *,
    reencode: bool = False,
    activity: Activity | None = None,
    overlay_filter: str | None = None,
    overlay_inputs: list[str] | None = None,
) -> Path:
    """Cut a single clip; optionally mux override audio and burn overlays."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    video = clip.video
    ss = max(0.0, video.media_offset(clip.in_time))
    duration = (clip.out_time - clip.in_time).total_seconds()
    if duration <= 0:
        raise ValueError(f"Non-positive clip duration for {clip.reason}")

    # Clamp to media bounds
    if ss + duration > video.duration:
        duration = max(0.01, video.duration - ss)

    args: list[str] = ["-ss", f"{ss:.3f}", "-i", video.path]

    audio_ss = None
    if clip.audio is not None:
        audio_ss = max(0.0, clip.audio.media_offset(clip.in_time))
        args.extend(["-ss", f"{audio_ss:.3f}", "-i", clip.audio.path])

    if overlay_inputs:
        for p in overlay_inputs:
            args.extend(["-i", p])

    args.extend(["-t", f"{duration:.3f}"])

    if overlay_filter:
        # Complex filter path — always re-encode video
        args.extend(["-filter_complex", overlay_filter])
        if clip.audio is not None:
            args.extend(["-map", "[vout]", "-map", "1:a:0"])
        else:
            args.extend(["-map", "[vout]", "-map", "0:a?"])
        args.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
            ]
        )
    elif reencode or clip.audio is not None:
        if clip.audio is not None:
            args.extend(
                ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest"]
            )
            if reencode:
                # replace -c:v copy
                args = [a for a in args if a not in ("-c:v", "copy")]
                args.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "18",
                        "-pix_fmt",
                        "yuv420p",
                    ]
                )
        else:
            args.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                ]
            )
    else:
        # Stream copy when possible (keyframe-aligned best-effort)
        args.extend(["-c", "copy"])

    args.append(str(out_path))
    run_ffmpeg(args)
    return out_path


def _clips_copy_compatible(metas: list[dict]) -> bool:
    """True when concat demuxer + stream copy is safe.

    Same pixel size is not enough — GoPro vs iPhone (or any mixed camera)
    often differ in timebase / SPS and freeze video while audio continues
    when stream-copied through the concat demuxer.
    """
    if not metas:
        return False
    widths = {m.get("width") for m in metas}
    heights = {m.get("height") for m in metas}
    if None in widths or None in heights or len(widths) > 1 or len(heights) > 1:
        return False
    audio = {bool(m.get("has_audio")) for m in metas}
    if len(audio) > 1:
        return False
    # Require matching fps (within 0.1) when reported
    fpss = [m.get("fps") for m in metas if m.get("fps")]
    if fpss and max(fpss) - min(fpss) > 0.1:
        return False
    codecs = {m.get("codec") for m in metas if m.get("codec")}
    if len(codecs) > 1:
        return False
    pix = {m.get("pix_fmt") for m in metas if m.get("pix_fmt")}
    if len(pix) > 1:
        return False
    return True


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def _concat_reencode_normalized(
    clip_paths: list[Path],
    metas: list[dict],
    out_path: Path,
) -> None:
    """Scale/pad/fps-normalize then concat via filter_complex.

    Required when mixing cameras (e.g. GoPro 4K + iPhone 1080p). The concat
    demuxer cannot change resolution mid-stream — audio keeps playing while
    video freezes on the previous clip's last frame.
    """
    target_w = _even(max((m.get("width") or 1920) for m in metas))
    target_h = _even(max((m.get("height") or 1080) for m in metas))
    fps_vals = [m["fps"] for m in metas if m.get("fps")]
    fps = fps_vals[0] if fps_vals else 30.0

    args: list[str] = []
    for p in clip_paths:
        args.extend(["-i", str(p)])

    n = len(clip_paths)
    fc_parts: list[str] = []
    for i, meta in enumerate(metas):
        fc_parts.append(
            f"[{i}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps:.3f},format=yuv420p[v{i}];"
        )
        if meta.get("has_audio"):
            fc_parts.append(
                f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                f"aresample=48000[a{i}];"
            )
        else:
            dur = max(0.05, float(meta.get("duration") or 0.05))
            fc_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},asetpts=N/SR/TB[a{i}];"
            )

    concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    fc_parts.append(f"{concat_in}concat=n={n}:v=1:a=1[vout][aout]")
    filter_complex = "".join(fc_parts)

    run_ffmpeg(
        [
            *args,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )


def concat_clips(
    clip_paths: list[Path],
    out_path: str | Path,
    *,
    reencode: bool | None = None,
) -> Path:
    """Concatenate clips chronologically.

    When ``reencode`` is None, stream-copy if clips match; otherwise normalize
    and re-encode via filter_complex (safe for mixed GoPro / iPhone sizes).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not clip_paths:
        raise ValueError("No clips to concatenate")
    _ensure_output_space(out_path)
    if len(clip_paths) == 1:
        shutil.copy2(clip_paths[0], out_path)
        return out_path

    metas = [probe_media(p) for p in clip_paths]
    if reencode is None:
        reencode = not _clips_copy_compatible(metas)
    elif not reencode and not _clips_copy_compatible(metas):
        reencode = True

    if reencode:
        _concat_reencode_normalized(clip_paths, metas, out_path)
        return out_path

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in clip_paths:
            escaped = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        list_path = f.name

    try:
        run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out_path),
            ]
        )
    finally:
        Path(list_path).unlink(missing_ok=True)
    return out_path


def write_manifest(
    clips: list[ClipSpec],
    out_path: str | Path,
    *,
    sync_results: list[SyncResult] | None = None,
    overlay_config: dict[str, Any] | None = None,
    gaps: list[str] | None = None,
    assumed_zero_drift: bool = False,
    fmt: str = "json",
) -> Path:
    out_path = Path(out_path)
    entries = []
    for i, clip in enumerate(clips):
        entries.append(
            {
                "index": i,
                "reason": clip.reason,
                "in_time": clip.in_time.isoformat(),
                "out_time": clip.out_time.isoformat(),
                "video": clip.video.path,
                "video_trust": clip.video.trust,
                "video_drift_ppm": clip.video.clock_drift_ppm,
                "audio": clip.audio.path if clip.audio else "native",
                "audio_trust": clip.audio.trust if clip.audio else None,
            }
        )
    payload = {
        "clips": entries,
        "sync": [asdict(s) for s in (sync_results or [])],
        "overlay": overlay_config,
        "gaps": gaps or [],
        "assumed_zero_drift": assumed_zero_drift,
    }

    if fmt == "csv":
        import csv

        with out_path.open("w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "index",
                    "reason",
                    "in_time",
                    "out_time",
                    "video",
                    "audio",
                    "video_trust",
                    "video_drift_ppm",
                ],
            )
            writer.writeheader()
            for e in entries:
                writer.writerow(
                    {
                        "index": e["index"],
                        "reason": e["reason"],
                        "in_time": e["in_time"],
                        "out_time": e["out_time"],
                        "video": e["video"],
                        "audio": e["audio"],
                        "video_trust": e["video_trust"],
                        "video_drift_ppm": e["video_drift_ppm"],
                    }
                )
    else:
        out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path
