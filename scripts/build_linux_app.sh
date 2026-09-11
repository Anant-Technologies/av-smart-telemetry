#!/usr/bin/env bash
# Build Linux GTK UI and wrap as a portable directory / AppImage-friendly layout.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIN="$ROOT/apps/linux"
DIST="$ROOT/dist/linux"
APPDIR="$DIST/Fitvid.AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/fitvid"

cd "$LIN"
cargo build --release
cp target/release/fitvid-ui "$APPDIR/usr/bin/Fitvid"

if [[ -x "$ROOT/dist/fitvid" ]]; then
  cp "$ROOT/dist/fitvid" "$APPDIR/usr/lib/fitvid/fitvid"
elif [[ -x "$ROOT/.venv/bin/fitvid" ]]; then
  cat > "$APPDIR/usr/lib/fitvid/fitvid" <<EOF
#!/bin/bash
exec "$ROOT/.venv/bin/fitvid" "\$@"
EOF
  chmod +x "$APPDIR/usr/lib/fitvid/fitvid"
fi

for bin in ffmpeg ffprobe; do
  if [[ -x "$ROOT/vendor/ffmpeg/$bin" ]]; then
    cp "$ROOT/vendor/ffmpeg/$bin" "$APPDIR/usr/lib/fitvid/"
  elif command -v "$bin" >/dev/null; then
    cp "$(command -v "$bin")" "$APPDIR/usr/lib/fitvid/" || true
  fi
done

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/lib/fitvid:$PATH"
exec "$HERE/usr/bin/Fitvid" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/fitvid.desktop" <<'EOF'
[Desktop Entry]
Name=fitvid
Exec=Fitvid
Icon=fitvid
Type=Application
Categories=AudioVideo;Video;
EOF

if command -v appimagetool >/dev/null; then
  ARCH=x86_64 appimagetool "$APPDIR" "$DIST/fitvid-linux-x64.AppImage"
  echo "Wrote $DIST/fitvid-linux-x64.AppImage"
else
  (
    cd "$DIST"
    tar -czf fitvid-linux-x64.tar.gz Fitvid.AppDir
  )
  echo "Wrote $DIST/fitvid-linux-x64.tar.gz (install appimagetool for AppImage)"
fi
