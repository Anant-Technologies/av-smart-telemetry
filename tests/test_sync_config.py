"""Tests for sync config, soft-skip, and offsets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fitvid.models import Activity, Lap, TelemetryPoint, VideoSource
from fitvid.resolve import resolve_clips
from fitvid.sync.config import (
    FitGeneratorSync,
    RecordingDevice,
    SyncConfig,
    apply_media_offsets,
    load_sync_config,
    shift_activity,
)


def test_fit_offset_computation_wall_vs_watch():
    fit_ref = datetime(2026, 9, 5, 20, 38, 35, tzinfo=timezone.utc)
    watch = datetime(2026, 9, 5, 20, 38, 35, tzinfo=timezone.utc)
    wall = datetime(2026, 9, 5, 20, 38, 40, tzinfo=timezone.utc)
    off = FitGeneratorSync.compute_offset(
        fit_reference=fit_ref, watch_clock=watch, wall_clock=wall
    )
    assert off == pytest.approx(5.0)


def test_media_offset_computation():
    spine = datetime(2026, 9, 5, 20, 38, 35, tzinfo=timezone.utc)
    device = datetime(2026, 9, 5, 20, 38, 30, tzinfo=timezone.utc)
    off = RecordingDevice.compute_offset(spine_time=spine, device_clock=device)
    assert off == pytest.approx(5.0)


def test_shift_activity_moves_laps():
    start = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    act = Activity(
        sport="cycling",
        session_start=start,
        session_end=start + timedelta(hours=1),
        laps=[Lap(0, start, start + timedelta(minutes=10), 600.0)],
        pauses=[],
        records=[TelemetryPoint(start, speed=1.0)],
    )
    shifted = shift_activity(act, 5.0)
    assert shifted.session_start == start + timedelta(seconds=5)
    assert shifted.laps[0].start_time == start + timedelta(seconds=5)


def test_load_sync_config_yaml(tmp_path: Path):
    p = tmp_path / "sync.yaml"
    p.write_text(
        """
sync:
  fit_generator:
    label: Watch
    fit_reference: "2026-09-05T20:38:35+00:00"
    watch_clock: "2026-09-05T20:38:35+00:00"
    wall_clock: null
  devices:
    - id: cam1
      kind: video
      label: Cam
      device_clock: "2026-09-05T20:38:30+00:00"
      files: [/tmp/a.mp4]
    - id: mic1
      kind: audio
      label: Mic
      device_clock: "2026-09-05T20:38:28+00:00"
      files: [/tmp/a.wav]
"""
    )
    cfg = load_sync_config(p)
    assert cfg.fit_generator is not None
    assert cfg.fit_generator.offset_seconds == pytest.approx(0.0)
    assert len(cfg.devices) == 2
    cam = next(d for d in cfg.devices if d.kind == "video")
    mic = next(d for d in cfg.devices if d.kind == "audio")
    assert cam.offset_seconds == pytest.approx(5.0)
    assert mic.offset_seconds == pytest.approx(7.0)


def test_load_sync_config_strips_vertical_tab(tmp_path: Path):
    p = tmp_path / "sync.yaml"
    # U+000B often appears when Tab is pressed in macOS SwiftUI TextFields
    p.write_text(
        "sync:\n"
        "  fit_generator:\n"
        '    label: "Watch"\n'
        '    fit_reference: "2026-09-05T20:38:35+00:00"\n'
        '    watch_clock: "2026-09-05T20:38:35+00:00\x0b"\n'
        "    wall_clock: null\n"
        "  devices: []\n"
    )
    cfg = load_sync_config(p)
    assert cfg.fit_generator is not None
    assert cfg.fit_generator.offset_seconds == pytest.approx(0.0)


def test_apply_media_offsets_shared_device():
    start = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    v1 = VideoSource("/tmp/a.mp4", start, 100.0)
    v2 = VideoSource("/tmp/b.mp4", start + timedelta(seconds=50), 100.0)
    cfg = SyncConfig(
        devices=[
            RecordingDevice(
                id="cam",
                label="Cam",
                kind="video",
                offset_seconds=10.0,
                files=["/tmp/a.mp4", "/tmp/b.mp4"],
            )
        ]
    )
    videos, _, results = apply_media_offsets([v1, v2], [], cfg)
    assert videos[0].start_time == start + timedelta(seconds=10)
    assert videos[1].start_time == start + timedelta(seconds=60)
    assert len(results) == 2


def test_audio_recorder_offset_independent_of_camera():
    from fitvid.models import AudioSource

    start = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    video = VideoSource("/tmp/cam.mp4", start, 100.0)
    audio = AudioSource("/tmp/mic.wav", start, 100.0)
    cfg = SyncConfig(
        devices=[
            RecordingDevice(
                id="cam",
                label="Cam",
                kind="video",
                offset_seconds=3.0,
                files=["/tmp/cam.mp4"],
            ),
            RecordingDevice(
                id="mic",
                label="Mic",
                kind="audio",
                offset_seconds=11.0,
                files=["/tmp/mic.wav"],
            ),
        ]
    )
    videos, audios, _ = apply_media_offsets([video], [audio], cfg)
    assert videos[0].start_time == start + timedelta(seconds=3)
    assert audios[0].start_time == start + timedelta(seconds=11)


def test_soft_skip_uncovered_range_keeps_other_clips():
    start = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    video = VideoSource(
        "/tmp/cam.mp4",
        start + timedelta(minutes=10),
        duration=600.0,
    )
    ranges = [
        (start, start + timedelta(minutes=5), "lap:0"),  # uncovered
        (
            start + timedelta(minutes=10),
            start + timedelta(minutes=15),
            "lap:1",
        ),  # covered
    ]
    clips, gaps = resolve_clips(ranges, [video], [])
    assert len(gaps) == 1
    assert "lap:0" in gaps[0]
    assert len(clips) == 1
    assert clips[0].reason == "lap:1"
