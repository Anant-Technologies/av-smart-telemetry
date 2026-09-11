#!/usr/bin/env bash
# Package Windows Fitvid UI + bundled CLI into dist/windows zip.
# Run on Windows (or CI windows-latest) with .NET 8 + WinAppSDK workloads.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIN="$ROOT/apps/windows/Fitvid"
DIST="$ROOT/dist/windows"
mkdir -p "$DIST/Fitvid/resources/fitvid"

dotnet publish "$WIN/Fitvid.csproj" -c Release -r win-x64 --self-contained true -o "$DIST/Fitvid"

if [[ -f "$ROOT/dist/fitvid.exe" ]]; then
  cp "$ROOT/dist/fitvid.exe" "$DIST/Fitvid/resources/fitvid/fitvid.exe"
elif [[ -f "$ROOT/dist/fitvid" ]]; then
  cp "$ROOT/dist/fitvid" "$DIST/Fitvid/resources/fitvid/fitvid.exe"
fi

for bin in ffmpeg.exe ffprobe.exe; do
  if [[ -f "$ROOT/vendor/ffmpeg/$bin" ]]; then
    cp "$ROOT/vendor/ffmpeg/$bin" "$DIST/Fitvid/resources/fitvid/"
  fi
done

(
  cd "$DIST"
  if command -v 7z >/dev/null; then
    7z a fitvid-windows-x64.zip Fitvid
  else
    zip -r fitvid-windows-x64.zip Fitvid
  fi
)
echo "Wrote $DIST/fitvid-windows-x64.zip"
