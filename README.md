# fitvid — FIT-Driven Video Compiler

Local tool that turns a Garmin/Wahoo `.fit` activity, video clip(s), and optional standalone audio into a highlight reel — with telemetry overlays and clock sync.

## Native apps (primary UI)

Platform-native shells drive the bundled `fitvid` CLI ([docs/ui-protocol.md](docs/ui-protocol.md)):

| Platform | UI | Build |
|---|---|---|
| macOS (Apple Silicon) | SwiftUI | `scripts/build_macos_app.sh [--dmg]` |
| Windows x64 | WinUI 3 | `scripts/build_windows_app.sh` |
| Linux x64 | GTK4 + libadwaita | `scripts/build_linux_app.sh` |

**Onboarding:** pick FIT → videos → optional audio (Skip).  
**Main layout:** field picker (left) from `fitvid inspect --format json`; work area (center); media dock (bottom) with video icons, then audio icons, then the FIT chip.

## CLI

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

fitvid inspect activity.fit --format json
fitvid compile --fit activity.fit --video ride.mp4 --select laps --dry-run --json-events
```

Bundle a standalone CLI for native apps:

```bash
pyinstaller fitvid-cli.spec --noconfirm
```

## Layout

- `fitvid/` — Python engine
- `apps/macos`, `apps/windows`, `apps/linux` — native UIs
- `docs/ui-protocol.md` — inspect JSON + compile NDJSON contract
- Design notes: `fit-video-compiler-spec.md`
