# -*- mode: python ; coding: utf-8 -*-
"""CLI-only PyInstaller bundle for native UI shells."""

from pathlib import Path

block_cipher = None
root = Path(SPECPATH)

datas = [
    (str(root / "overlay.example.yaml"), "."),
    (str(root / "select.example.yaml"), "."),
]

ffmpeg_dir = root / "vendor" / "ffmpeg"
binaries = []
if ffmpeg_dir.exists():
    for name in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"):
        p = ffmpeg_dir / name
        if p.exists():
            binaries.append((str(p), "."))

hiddenimports = [
    "fitdecode",
    "numpy",
    "scipy",
    "scipy.signal",
    "PIL",
    "yaml",
    "typer",
    "rich",
    "fitvid",
    "fitvid.cli",
    "fitvid.compile",
    "fitvid.events",
    "fitvid.inspect",
]

a = Analysis(
    [str(root / "fitvid" / "cli.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "gui"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="fitvid",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
