#!/usr/bin/env bash
# Copies an existing renderer export into relay/public/app/ (builds nothing itself).
# Usage: scripts/build-remote-web.sh [export_dir]   (default Frontend/frontend/renderer/out)
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
src="${1:-$root/Frontend/frontend/renderer/out}"
dest="$root/relay/public/app"

if [ ! -f "$src/index.html" ]; then
  echo "error: $src has no index.html; run the renderer export first" >&2
  exit 1
fi

rm -rf "$dest"
mkdir -p "$dest"
cp -R "$src"/. "$dest"/

echo "copied $(find "$dest" -type f | wc -l | tr -d ' ') files, $(du -sh "$dest" | cut -f1) to $dest"
