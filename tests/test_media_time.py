"""Tests for host-local timezone conversion."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import fitvid.media as media
from fitvid.timeutil import parse_wall_clock_as_local, to_local


def test_parse_creation_time_stays_host_local(monkeypatch):
    monkeypatch.setattr(
        "fitvid.timeutil.host_local_tz", lambda: ZoneInfo("America/Los_Angeles")
    )
    dt = parse_wall_clock_as_local("2026-09-05T13:38:40.000000Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.hour == 13 and dt.minute == 38
    # Same instant as 20:38 UTC
    assert dt.astimezone(timezone.utc) == datetime(
        2026, 9, 5, 20, 38, 40, tzinfo=timezone.utc
    )


def test_to_local_converts_fit_utc(monkeypatch):
    monkeypatch.setattr(
        "fitvid.timeutil.host_local_tz", lambda: ZoneInfo("America/Los_Angeles")
    )
    utc = datetime(2026, 9, 5, 20, 38, 40, tzinfo=timezone.utc)
    local = to_local(utc)
    assert local.hour == 13
    assert local.tzinfo == ZoneInfo("America/Los_Angeles")


def test_media_parse_timestamp_uses_timeutil(monkeypatch):
    monkeypatch.setattr(
        "fitvid.timeutil.host_local_tz", lambda: ZoneInfo("America/New_York")
    )
    dt = media._parse_timestamp("2026-01-15 09:00:00")
    assert dt is not None
    assert dt.hour == 9
    assert dt.astimezone(timezone.utc).hour == 14


def test_probe_prefers_apple_creationdate(monkeypatch, tmp_path):
    """iPhone MOVs: container creation_time is often transfer day; Apple tag is capture."""
    monkeypatch.setattr(
        "fitvid.timeutil.host_local_tz", lambda: ZoneInfo("America/Los_Angeles")
    )
    fake = tmp_path / "IMG_0001.MOV"
    fake.write_bytes(b"x")

    def fake_ffprobe(_path):
        return {
            "format": {
                "duration": "12.0",
                "tags": {
                    "creation_time": "2026-07-27T20:00:00.000000Z",
                    "com.apple.quicktime.creationdate": "2026-07-25T10:15:30-0700",
                    "com.apple.quicktime.make": "Apple",
                    "com.apple.quicktime.model": "iPhone 15",
                },
            },
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "tags": {"creation_time": "2026-07-27T20:00:00.000000Z"},
                }
            ],
        }

    monkeypatch.setattr(media, "ffprobe_json", fake_ffprobe)
    meta = media.probe_media(fake)
    assert meta["start_time"] is not None
    assert meta["start_time"].month == 7 and meta["start_time"].day == 25
    assert meta["start_time"].hour == 10
