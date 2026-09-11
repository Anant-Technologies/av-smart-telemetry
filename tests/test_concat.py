"""Tests for mixed-camera concat behavior."""

from __future__ import annotations

from pathlib import Path

import fitvid.ffmpeg_ops as ops


def test_copy_incompatible_when_resolutions_differ():
    metas = [
        {"width": 3840, "height": 2160, "has_audio": True, "fps": 29.97, "codec": "h264", "pix_fmt": "yuv420p"},
        {"width": 1920, "height": 1080, "has_audio": True, "fps": 30.0, "codec": "h264", "pix_fmt": "yuv420p"},
    ]
    assert not ops._clips_copy_compatible(metas)


def test_copy_compatible_matching_burns():
    metas = [
        {"width": 1920, "height": 1080, "has_audio": True, "fps": 30.0, "codec": "h264", "pix_fmt": "yuv420p"},
        {"width": 1920, "height": 1080, "has_audio": True, "fps": 30.0, "codec": "h264", "pix_fmt": "yuv420p"},
    ]
    assert ops._clips_copy_compatible(metas)


def test_concat_mixed_resolution_uses_filter_complex(monkeypatch, tmp_path):
    a = tmp_path / "gopro.mp4"
    b = tmp_path / "iphone.mp4"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    out = tmp_path / "out.mp4"

    metas = [
        {
            "width": 3840,
            "height": 2160,
            "has_audio": True,
            "fps": 29.97,
            "duration": 2.0,
            "codec": "h264",
            "pix_fmt": "yuv420p",
        },
        {
            "width": 1920,
            "height": 1080,
            "has_audio": True,
            "fps": 30.0,
            "duration": 1.0,
            "codec": "h264",
            "pix_fmt": "yuv420p",
        },
    ]
    monkeypatch.setattr(ops, "probe_media", lambda p: metas[0] if Path(p).name.startswith("gopro") else metas[1])
    monkeypatch.setattr(ops, "_ensure_output_space", lambda *a, **k: None)

    captured: list[list[str]] = []

    def fake_run(args, *, check=True):
        captured.append(list(args))

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    monkeypatch.setattr(ops, "run_ffmpeg", fake_run)
    ops.concat_clips([a, b], out, reencode=None)
    assert captured, "expected ffmpeg invocation"
    args = captured[0]
    assert "-filter_complex" in args
    fc = args[args.index("-filter_complex") + 1]
    assert "scale=3840:2160" in fc
    assert "concat=n=2:v=1:a=1" in fc
    assert "-f" not in args or args[args.index("-f") + 1] != "concat"
