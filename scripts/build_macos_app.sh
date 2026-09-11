#!/usr/bin/env bash
# Build Fitvid.app for macOS (Apple Silicon) and optionally a DMG.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAC="$ROOT/apps/macos"
DIST="$ROOT/dist/macos"
APP="$DIST/Fitvid.app"
CLI_DEST="$APP/Contents/Resources/fitvid"

echo "==> Building Swift package"
cd "$MAC"
swift build -c release

BIN="$(swift build -c release --show-bin-path)/Fitvid"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$CLI_DEST"
rm -rf "$CLI_DEST"/*
mkdir -p "$CLI_DEST"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>fitvid</string>
  <key>CFBundleDisplayName</key><string>fitvid</string>
  <key>CFBundleIdentifier</key><string>com.fitvid.app</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleExecutable</key><string>Fitvid</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cp "$BIN" "$APP/Contents/MacOS/Fitvid"
chmod +x "$APP/Contents/MacOS/Fitvid"

echo "==> Bundling fitvid CLI (venv or PyInstaller dist)"
if [[ -x "$ROOT/dist/fitvid" ]]; then
  cp "$ROOT/dist/fitvid" "$CLI_DEST/fitvid"
elif [[ -x "$ROOT/.venv/bin/fitvid" ]]; then
  # Dev bundle: wrapper script that calls venv (not for distribution)
  cat > "$CLI_DEST/fitvid" <<EOF
#!/bin/bash
exec "$ROOT/.venv/bin/fitvid" "\$@"
EOF
  chmod +x "$CLI_DEST/fitvid"
else
  echo "warning: no fitvid CLI found; UI will search PATH" >&2
fi

for bin in ffmpeg ffprobe; do
  if [[ -x "$ROOT/vendor/ffmpeg/$bin" ]]; then
    cp "$ROOT/vendor/ffmpeg/$bin" "$CLI_DEST/"
  elif command -v "$bin" >/dev/null; then
    cp "$(command -v "$bin")" "$CLI_DEST/" || true
  fi
done

if [[ "${1:-}" == "--dmg" ]]; then
  DMG="$DIST/fitvid-macos-arm64.dmg"
  rm -f "$DMG"
  hdiutil create -volname fitvid -srcfolder "$APP" -ov -format UDZO "$DMG"
  echo "Wrote $DMG"
fi

echo "Wrote $APP"
echo "Run: open $APP"
