#!/usr/bin/env bash
# Helper: copy system ffmpeg/ffprobe into vendor/ for local PyInstaller bundles.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/vendor/ffmpeg"
mkdir -p "$DEST"
for bin in ffmpeg ffprobe; do
  src="$(command -v "$bin" || true)"
  if [[ -z "$src" ]]; then
    echo "warning: $bin not found on PATH" >&2
    continue
  fi
  cp -f "$src" "$DEST/$bin"
  echo "bundled $src -> $DEST/$bin"
done
echo "Note: Homebrew ffmpeg is dynamically linked; for distribution use a static build."
