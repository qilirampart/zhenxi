#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ "${1:-}" == "--clean" ]]; then
  rm -rf build dist
fi

mkdir -p runtime/ffmpeg

if command -v ffmpeg >/dev/null 2>&1; then
  cp "$(command -v ffmpeg)" runtime/ffmpeg/ffmpeg
fi

if command -v ffprobe >/dev/null 2>&1; then
  cp "$(command -v ffprobe)" runtime/ffmpeg/ffprobe
fi

python -m PyInstaller --noconfirm zhenxi.spec

if [[ -d "dist/zhenxi.app" ]]; then
  ditto -c -k --sequesterRsrc --keepParent "dist/zhenxi.app" "dist/zhenxi-macos-app.zip"
  echo "Build finished: dist/zhenxi.app"
  echo "Archive ready: dist/zhenxi-macos-app.zip"
else
  echo "Build finished, but dist/zhenxi.app was not generated." >&2
  exit 1
fi
