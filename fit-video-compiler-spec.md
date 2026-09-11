# FIT-Driven Video Compiler — Build Spec

## 1. Goal

Given a `.fit` activity file, one or more video files, and (optionally) one or more standalone audio files, automatically select and stitch together the video segments that correspond to interesting or specified moments in the telemetry (laps, threshold crossings, GPS locations, or manual markers), producing a single output video. Where a standalone audio recording covers a used segment, it replaces that video's native audio track. Optionally, a configurable telemetry overlay (text fields + a route map with a live position marker) is burned into the output.

This is a **local tool**, not a browser app — real video/audio decode/cut/concat, FIT binary parsing, audio cross-correlation, and per-frame overlay rendering all need native libraries that don't run reliably in-browser at this scale.

---

## 2. Open decisions (confirm before/while building — defaults shown)

| Decision | Options | Default for v1 |
|---|---|---|
| **Selection logic** | (a) lap/split boundaries, (b) threshold crossings, (c) manual timestamps, (d) GPS proximity | Support **(a) and (c)** first; add (b) and (d) as pluggable selectors afterward (§7). |
| **Video relationship** | Sequential clips vs. synced multi-camera angles vs. mix | Assume **sequential by default**, but the data model (§4) and sync layer (§5) support multi-camera without rework. |
| **Runtime** | Local script/CLI vs. browser app | **Local CLI (Python + ffmpeg)**. A GUI can wrap the CLI later. |
| **Device clock sync method** | (a) physical sync event, (b) manual clock alignment, (c) automatic audio cross-correlation | **(c)** for audio↔video (automatic), **(a)** for tying audio/video to the FIT clock, falling back to **(b)**. See §5. |
| **Map rendering style** | (a) route-only line drawn on a blank canvas (no external dependency), (b) real basemap tiles (Mapbox/OSM, needs an API key and network access) | **(a) route-only** for v1 — no API key, no network dependency, no cost, and it's the harder-to-get-wrong option to ship first. (b) as an opt-in upgrade later. See §10. |
| **App shell** | (a) CLI-only native binaries per platform, (b) thin native GUI wrapping the same CLI/library | **(b)** — "app" implies double-click launch, not a terminal. Recommend **PySide6** (Qt for Python): one codebase across macOS/Windows/Linux, LGPL-licensed. See §14. |

---

## 3. Inputs

- One `.fit` file (single or multi-session).
- One or more **video** files, each with a resolvable absolute start timestamp (§5).
- One or more **standalone audio** files, each similarly needing a resolvable absolute start timestamp; where one covers a used clip, it replaces that clip's native audio.
- Optional **overlay config** (§10): which telemetry fields to show as text, where, and whether/how to show the route map.
- Optional per-file "trust" flag (`metadata` / `manual` / `sync-derived`) on every video and audio source.

---

## 4. Core data model

```python
@dataclass
class TelemetryPoint:
    timestamp: datetime
    lat: float | None
    lon: float | None
    altitude: float | None
    speed: float | None
    heart_rate: int | None
    cadence: int | None
    power: int | None
    distance: float | None

@dataclass
class Lap:
    index: int
    start_time: datetime
    end_time: datetime
    total_timer_time: float
    avg_speed: float | None
    avg_power: float | None
    avg_heart_rate: float | None
    max_speed: float | None
    trigger: str

@dataclass
class PauseEvent:
    start_time: datetime
    end_time: datetime

@dataclass
class Activity:
    sport: str
    session_start: datetime
    session_end: datetime
    laps: list[Lap]
    pauses: list[PauseEvent]
    records: list[TelemetryPoint]

    def interpolate(self, t: datetime) -> TelemetryPoint:
        """Linearly interpolate all numeric fields between the two bracketing
        records at time t. Used to drive per-frame overlay rendering (§10)
        independent of the FIT file's own record rate (often 1 Hz)."""

@dataclass
class MediaSource:
    path: str
    start_time: datetime
    duration: float
    trust: Literal["metadata", "manual", "sync-derived"]
    clock_drift_ppm: float = 0.0

@dataclass
class VideoSource(MediaSource):
    has_audio: bool = True

@dataclass
class AudioSource(MediaSource):
    priority: int = 0

@dataclass
class ClipSpec:
    video: VideoSource
    audio: AudioSource | None
    in_time: datetime
    out_time: datetime
    reason: str
```

**Absolute timestamps remain the sync spine.** Every clip, and every overlay frame, resolves to an absolute time; each media source's own in/out points are derived independently as `(absolute_time - source.start_time)`.

**Pauses**: use `total_timer_time` for anything matching "camera was still rolling," elapsed/wall-clock time for anything that must line up with real timestamps.

---

## 5. Multi-device clock synchronization

Three independent clocks are in play — FIT device, camera(s), audio recorder(s) — and consumer clocks drift (typically low tens of ppm) even when initially aligned.

### 5.1 Audio ↔ video: automatic cross-correlation (default)

Every video has its own embedded scratch audio track. Extract it (`ffmpeg -i video.mp4 -vn ...`), resample both it and the standalone audio to a common low sample rate, cross-correlate (FFT-based, e.g. `scipy.signal.fftconvolve`), and take the lag at the correlation peak as the offset. Report a confidence score (peak height vs. correlation floor); low confidence (silence, wind noise) should fall back to §5.2 rather than trusting a bad offset silently.

### 5.2 Audio/video ↔ FIT: physical sync event (default) or manual alignment (fallback)

The FIT device has no audio to correlate against, so tying it to the audio/video timeline needs a discrete matched event:

- **Preferred: clap + simultaneous FIT event.** Clap at the exact moment you press "lap"/"start" on the GPS device. This gives `fit_time` (from the resulting FIT `event`/`lap` message), and `audio_time`/`video_time` (peak-picking the clap transient in each waveform). `offset = fit_time - audio_time`, cross-checked against the §5.1 offset — a mismatch flags a problem.
- **Fallback: manual clock alignment.** Set all device clocks from the same reference before recording, record the FIT device's clock at that moment, store the estimated offset in config. Expect single-digit-second error.

Repeat at the **end** of a long session if practical — see §5.3.

### 5.3 Drift correction for long sessions

If sync events exist at both start and end of a session, interpolate offset linearly:

```
offset(t) = offset_start + (offset_end - offset_start) * (t - t_start) / (t_end - t_start)
```

rather than one fixed offset for the whole activity. Store `clock_drift_ppm` per `MediaSource` explicitly. With only one sync event, assume zero drift and flag that assumption in the manifest (§9).

### 5.4 Output of this stage

```python
@dataclass
class SyncResult:
    source_a: str
    source_b: str
    offset_seconds: float
    drift_ppm: float
    method: Literal["cross-correlation", "clap-event", "manual"]
    confidence: float | None
```

All downstream `start_time` values are resolved using these results, so the rest of the pipeline works in one shared absolute timeline.

---

## 6. FIT parsing

- Use **`fitdecode`** or **`python-fitparse`** rather than hand-rolling a binary parser.
- Extract at minimum: `record`, `lap`, `session`, `event` message types.
- Handle multi-session files as separate `Activity` objects.
- Validate: warn (don't crash) if a field a selector or overlay needs is absent.
- **Field introspection**: expose `fitvid inspect activity.fit` listing every field actually populated (with observed min/max/units), since availability depends entirely on which sensors were paired when recording. This same list is what the overlay config (§10) and threshold selectors (§7) should be checked against.

  | Availability | Fields |
  |---|---|
  | Common (most GPS watches/bike computers) | `speed`/`enhanced_speed`, `altitude`/`enhanced_altitude`, `grade`, `distance`, `heart_rate`, `lat`/`lon` |
  | Cycling (needs a power meter) | `power`, `cadence`, `left_right_balance`, `torque_effectiveness`, `pedal_smoothness` |
  | Running (needs a dynamics pod / capable watch) | `cadence`, `vertical_oscillation`, `stance_time`, `vertical_ratio`, `step_length` |
  | Newer/less common devices | `respiration_rate`, `core_temperature`, `SpO2` (separate periodic message), `temperature` |

---

## 7. Selection logic (pluggable)

```python
class ClipSelector(Protocol):
    def select(self, activity: Activity) -> list[tuple[datetime, datetime, str]]:
        """Returns (start, end, reason) absolute time ranges."""
```

Implement first:
- `LapSelector` — one range per lap (or per N laps).
- `ManualTimestampSelector` — user-supplied `(time_or_offset, label, duration_before, duration_after)`, cross-checked against the activity's time range.

Add later:
- `ThresholdSelector(field, op, value, min_duration)` — generic over any field found by `fitvid inspect` (§6), not a hardcoded list. `op` is one of `>`, `>=`, `<`, `<=`, `between`. Fails fast with a clear message if `field` isn't present. Supports compound AND conditions and named presets (`climbing`, `descending-fast`, `sprint`).
- `ProximitySelector(lat, lon, radius_m)` — haversine-distance grouping, tolerant of GPS dropouts.

A merge step (dedupe/merge overlapping ranges, min/max clip length, padding) runs before clip resolution.

---

## 8. Media resolution & cutting

For each merged `(start, end, reason)` range:

1. **Video**: find candidate `VideoSource`s overlapping the range. Single candidate → trim. Multiple (multi-camera) → priority order from config, or emit one clip per camera. None → cut what exists, log a gap.
2. **Audio override**: independently find candidate `AudioSource`s overlapping the range; highest `priority` wins. Partial coverage falls back to native audio for the uncovered portion (or silence, per config).
3. Cut video with `ffmpeg` (`-c copy` on keyframe-aligned cuts; re-encode otherwise).
4. Mux the resolved audio in place of the video's native track, time-aligned per §5.
5. **Overlay burn-in** (§10), if configured, applied per clip using that clip's own absolute time range — keeps sync exact without needing to overlay across an already-concatenated multi-source timeline.
6. Concatenate chronologically via ffmpeg's concat demuxer, re-encoding if source clips have inconsistent codecs/resolutions/frame rates/audio formats.

---

## 9. Output

- Single output video, clips in chronological order, audio per the override rule, overlay burned in if configured.
- A sidecar JSON/CSV manifest per clip: source video, source audio (or "native"), in/out times, reason, sync method/confidence/drift used, and which overlay config was applied.

---

## 10. Telemetry overlay rendering

Two overlay element types, both driven by `Activity.interpolate(t)` (§4) so they update smoothly regardless of the FIT file's native record rate:

### 10.1 Text fields

```python
@dataclass
class TextOverlayElement:
    field: str                    # e.g. "speed", "heart_rate", "power" — checked against fitvid inspect
    label: str | None             # e.g. "Speed" — None = no label, value only
    format: str                   # e.g. "{value:.1f} km/h"
    unit_system: Literal["metric", "imperial"] = "metric"
    anchor: Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"] | None = None
    position: tuple[int, int] | tuple[float, float] | None = None   # explicit px or 0–1 fraction, overrides anchor
    margin: int = 24              # px from the edge when using an anchor
    font_size: int = 32
    color: str = "#FFFFFF"
    outline_color: str | None = "#000000"   # for legibility over variable video content
```

Any field surfaced by `fitvid inspect` can be used. Multiple text elements can be placed independently — e.g. speed bottom-left, heart rate bottom-right, elapsed lap time top-center.

### 10.2 Map + live position

```python
@dataclass
class MapOverlayElement:
    style: Literal["route-only", "tiles"] = "route-only"   # see §2 decision
    anchor: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "bottom-right"
    position: tuple[int, int] | tuple[float, float] | None = None
    margin: int = 24
    width_px: int = 320
    height_px: int = 320
    route_color: str = "#FF5A36"
    route_width: int = 3
    marker_color: str = "#FFFFFF"
    marker_radius: int = 6
    padding_pct: float = 0.1      # blank margin around the route bounding box
    tile_provider: str | None = None   # required if style == "tiles"; e.g. "mapbox"
```

**Rendering approach (route-only, default):**
1. Project the full activity's `lat`/`lon` points to a 2D canvas once per clip's video (simple linear scaling of the lat/lon bounding box into `width_px × height_px`, with `padding_pct` margin — a full Mercator projection is unnecessary at the scale of a single activity's route and adds complexity for no visible benefit).
2. Render the static route line once per output clip (it doesn't change frame to frame) — cache it as a background layer.
3. For each output frame, interpolate the current lat/lon via `Activity.interpolate(t)`, project it onto the same canvas, and draw the marker on top of the cached route layer.
4. Composite result (with alpha channel) onto the base video frame at the configured position using ffmpeg's `overlay` filter (or do the compositing directly in Python/OpenCV before handing frames to ffmpeg — either works; the ffmpeg-filter route keeps the video encode as a single pass).

**Tiles style (later upgrade):** same pipeline, but the cached background layer is a fetched basemap image (Mapbox Static Images API, or self-hosted OSM tiles) instead of a blank canvas. Needs an API key and network access at render time — flagged as an explicit opt-in given the added dependency and per-request cost of most tile providers.

### 10.3 Placement mechanism (shared by both element types)

- **Anchor + margin** (simplest, recommended default): pick a corner or center, offset by `margin` px — overlay elements reflow correctly if output resolution changes.
- **Explicit position**: either absolute pixels (tied to a specific output resolution) or a `0.0–1.0` fraction of frame width/height (resolution-independent) — support both, inferring which from whether values are `> 1`.
- Config validates that elements don't overlap unless the user explicitly allows it (a simple bounding-box check at config-load time), catching layout mistakes before a long render rather than after.

### 10.4 Config shape

```yaml
overlay:
  text:
    - field: speed
      format: "{value:.1f} km/h"
      anchor: bottom-left
    - field: heart_rate
      label: "HR"
      format: "{value:.0f} bpm"
      anchor: bottom-right
      color: "#FF5A36"
  map:
    style: route-only
    anchor: top-right
    width_px: 300
    height_px: 300
```

---

## 11. CLI shape (suggested)

```
fitvid inspect activity.fit
# → lists every populated record field with observed min/max and units.

fitvid compile \
  --fit activity.fit \
  --video cam1.mp4 --video cam2.mp4 \
  --audio lav-mic.wav \
  --select laps \
  --overlay overlay.yaml \
  --pad-before 2 --pad-after 2 \
  --out highlight.mp4
```

Threshold rules and overlay layout both live in the same kind of generic config file (`.yaml`/`.toml`), e.g.:

```yaml
select:
  - type: threshold
    field: power
    op: ">"
    value: 300
    min_duration: 5
  - type: threshold
    preset: climbing
```

---

## 12. Suggested build order

1. FIT parser wrapper → dump `Activity` to JSON. Include `inspect` from day one.
2. Video/audio metadata resolver (`ffprobe` start-time extraction + manual offset override).
3. Audio↔video cross-correlation sync (§5.1) → verify against a known clap in a real recording.
4. Clap-event detection in FIT + audio/video transient detection (§5.2) → verify all three clocks agree.
5. `LapSelector` + `ManualTimestampSelector` → print merged ranges only, no media ops yet.
6. Single-video, native-audio cut + concat, end to end.
7. Audio override muxing (§8 steps 2 & 4).
8. **Overlay: text fields only**, route-only map deferred — validates the interpolation + placement + burn-in pipeline on the simpler element type first.
9. **Overlay: route-only map** — static route + moving marker.
10. Multi-camera overlap handling.
11. `ThresholdSelector` / `ProximitySelector`.
12. Drift correction (§5.3) once start/end sync events are available in test data.
13. Optional: tiles-style map upgrade.
14. Package as native apps for macOS (Apple Silicon), Windows, and Linux via PyInstaller + a CI build matrix (§14).

---

## 13. Edge cases to handle explicitly, not silently

- Video/audio timestamp metadata missing, wrong timezone, or implausible relative to the FIT session window.
- No clap/sync event captured for a session — falls back to manual/metadata alignment, flagged in the manifest.
- Cross-correlation confidence too low — flag rather than trust a bad offset.
- FIT file with paused/resumed segments — timer time vs elapsed time divergence.
- Multi-session FIT files.
- Selected range with no covering video, or no covering audio override.
- Selected range spanning a boundary between two sequential files (video or audio split across multiple files).
- GPS dropouts for proximity selection **and** for the map overlay — the moving marker should hold at the last known position (or fade out) rather than snapping to `(0, 0)` or crashing on a missing coordinate.
- Overlay field requested that isn't present in this file (e.g. `power` with no power meter) — fail fast at config-load time via the same check used by `ThresholdSelector`, not partway through a render.
- Long sessions where clock drift visibly desyncs by the end — needs §5.3 interpolation.

---

## 14. Packaging as native apps (macOS Apple Silicon, Windows, Linux)

### 14.1 Shell: CLI core + thin GUI wrapper

- Keep all logic in the `fitvid` library/CLI (§11) — the GUI is a thin wrapper around it, not a reimplementation, so the CLI stays scriptable and the GUI stays simple.
- Use **PySide6** (Qt for Python) for the GUI: one codebase renders native widgets on macOS, Windows, and Linux; LGPL-licensed, so it doesn't force a GPL/commercial choice on the rest of the app the way PyQt would.
- Minimal v1 GUI surface: file pickers for the FIT file, videos, and audio; a selection-rule and overlay config editor (a raw YAML view is fine for v1 — a friendlier builder UI can come later); an output path field; a "Run" button; and a log panel that streams the CLI's own stdout, so the GUI doesn't need separate progress-tracking logic.

### 14.2 Bundling

- Use **PyInstaller** to produce a bundle per platform, built **on that platform** — PyInstaller doesn't cross-compile, so this needs actual (or CI-hosted) Mac, Windows, and Linux machines, e.g. a GitHub Actions matrix (§14.6).
- Bundle a static **ffmpeg** binary per platform inside the app so users never install it separately:
  - macOS arm64: a static arm64 build.
  - Windows x64: a static build.
  - Linux x64: a static build — safer than depending on the distro's own `ffmpeg` package, which varies in codec support and version across distros.
  - Check the license of whichever ffmpeg build is chosen: builds including `libx264`/`libx265` are GPL, which affects how the whole bundled app can be distributed. Either accept GPL for the distributed bundle, or use an LGPL-only ffmpeg build (fewer encoders) if that matters.
- Bundle the Python dependencies (`fitdecode`/`python-fitparse`, `numpy`, `scipy`, `Pillow`/`opencv-python-headless`, `PySide6`) via PyInstaller's dependency analysis, and **test the built bundle on a clean machine/VM** without the dev environment present — PyInstaller can silently miss dynamically-imported modules.

### 14.3 macOS (Apple Silicon)

- Build natively on arm64 hardware or an arm64 CI runner — don't ship a Rosetta-translated x86_64 build for something explicitly "for Apple Silicon."
- Sign with a Developer ID Application certificate and **notarize** via `notarytool`. Without this, Gatekeeper blocks or heavily warns on first launch of an unsigned `.app`.
- Package as a `.dmg` (drag-to-Applications) — the standard macOS distribution UX.
- The bundled ffmpeg binary needs to be signed too (or Gatekeeper flags it as a separate unsigned executable even inside an otherwise-signed `.app`).

### 14.4 Windows

- Build on a Windows x64 machine/runner.
- Code-sign the `.exe`/installer with an Authenticode certificate if available — unsigned installers trigger SmartScreen warnings; not required for the app to function, but a rough first impression for non-technical users.
- Package with **Inno Setup** or **NSIS** for a standard installer (Start Menu entry, uninstaller), or ship a portable `.zip` if an installer is more than v1 needs.

### 14.5 Linux

- Build an **AppImage** as the primary format — a single portable executable that works across distros without root or a package manager, the closest Linux equivalent to the Mac/Windows single-app experience.
- Flatpak (via Flathub) is a reasonable secondary channel later for discoverability, at the cost of extra sandboxing/permissions work — not needed for v1.
- Distro-native `.deb`/`.rpm` packages are a further later option, not required for launch.

### 14.6 CI/CD

- Use a GitHub Actions build matrix (`macos-14` for arm64, `windows-latest`, `ubuntu-latest`) to build all three artifacts from a single tagged release, rather than building manually per platform — keeps dependency versions from silently drifting apart between platforms.
- Each platform job: install Python deps → run PyInstaller → bundle the matching static ffmpeg → sign/notarize (macOS) or sign (Windows) if certificates are available as CI secrets → package (`.dmg` / installer / AppImage) → upload as a release asset.
