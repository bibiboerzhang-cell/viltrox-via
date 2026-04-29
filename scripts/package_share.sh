#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="$ROOT/.share-stage"
ARCHIVE="$ROOT/viltrox-2.0-share.tar.gz"

rm -rf "$STAGE"
mkdir -p "$STAGE"

rsync -a \
  --exclude-from="$ROOT/.shareignore" \
  "$ROOT/" "$STAGE/viltrox-2.0/"

if [ -d "$ROOT/frontend/dist" ]; then
  rsync -a "$ROOT/frontend/dist/" "$STAGE/viltrox-2.0/frontend/dist/"
fi

tar -czf "$ARCHIVE" -C "$STAGE" viltrox-2.0
du -sh "$ARCHIVE"
