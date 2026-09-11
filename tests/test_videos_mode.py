"""Tests for all-videos concat mode (creation-time order)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fitvid.compile import is_videos_select
from fitvid.models import Activity, AudioSource, TelemetryPoint, VideoSource
from fitvid.resolve import resolve_all_videos


def _activity(start: datetime, duration_s: float = 3600.0) -> Activity:
    records = [
        TelemetryPoint(start + timedelta(seconds=i), speed=5.0, lat=37.0, lon=-122.0)
        for i in range(0, int(duration_s), 60)
    ]
    return Activity(
        sport="cycling",
        session_start=start,
        session_end=start + timedelta(seconds=duration_s),
        laps=[],
        pauses=[],
        records=records,
    )


def test_resolve_all_videos_orders_by_creation_start_time():
    t0 = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    act = _activity(t0, 3600)
    later = VideoSource("/tmp/b.mp4", t0 + timedelta(minutes=10), 60.0)
    earlier = VideoSource("/tmp/a.mp4", t0, 60.0)
    clips, gaps = resolve_all_videos([later, earlier], [], activity=act)
    assert len(clips) == 2
    assert clips[0].video.path == "/tmp/a.mp4"
    assert clips[1].video.path == "/tmp/b.mp4"
    assert clips[0].in_time == t0
    assert clips[0].out_time == t0 + timedelta(seconds=60)
    assert any("Gap between videos" in g for g in gaps)


def test_resolve_all_videos_skips_clip_outside_fit_window():
    t0 = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    act = _activity(t0, 3600)
    outside = VideoSource("/tmp/pre.mp4", t0 - timedelta(hours=2), 120.0)
    inside = VideoSource("/tmp/ride.mp4", t0 + timedelta(minutes=5), 120.0)
    clips, gaps = resolve_all_videos([outside, inside], [], activity=act)
    assert len(clips) == 1
    assert clips[0].video.path == "/tmp/ride.mp4"
    assert any("outside FIT activity" in g for g in gaps)


def test_resolve_all_videos_attaches_overlapping_audio():
    t0 = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    act = _activity(t0, 3600)
    video = VideoSource("/tmp/cam.mp4", t0, 100.0)
    audio = AudioSource("/tmp/mic.wav", t0 + timedelta(seconds=10), 50.0, priority=1)
    clips, _ = resolve_all_videos([video], [audio], activity=act)
    assert clips[0].audio is not None
    assert clips[0].audio.path == "/tmp/mic.wav"


def test_is_videos_select_string_and_yaml(tmp_path: Path):
    assert is_videos_select("videos")
    assert is_videos_select("all")
    assert not is_videos_select("laps")
    p = tmp_path / "sel.yaml"
    p.write_text("select:\n  - type: videos\n")
    assert is_videos_select(str(p))
    p.write_text("select:\n  - type: laps\n")
    assert not is_videos_select(str(p))
