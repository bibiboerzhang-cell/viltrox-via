#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d)"
NAME="${1:-viltrox-debug-${STAMP}}"
OUTPUT="$ROOT/${NAME}.zip"
STAGE="$ROOT/.debug-stage"

rm -rf "$STAGE"
rm -f "$OUTPUT"
mkdir -p "$STAGE/project"
trap 'rm -rf "$STAGE"' EXIT

rsync -a --delete \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='project' \
  --exclude='frontend/node_modules' \
  --exclude='frontend/dist' \
  --exclude='build' \
  --exclude='uploads' \
  --exclude='frames' \
  --exclude='data/via_qdrant' \
  --exclude='logs' \
  --exclude='runtime' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='.share-stage' \
  --exclude='.debug-stage' \
  --exclude='*backup*/' \
  --exclude='snapshot-*/' \
  --exclude='.env.backup' \
  --exclude='*.backup' \
  --exclude='*.env' \
  --exclude='*.env.*' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='backend/.env' \
  --exclude='*.pem' \
  --exclude='*.key' \
  --exclude='*secret*' \
  --exclude='*.zip' \
  --exclude='*.db' \
  --exclude='*.db-wal' \
  --exclude='*.db-shm' \
  "$ROOT/" "$STAGE/project/"

(
  cd "$STAGE"
  zip -qry "$OUTPUT" project
)

if unzip -l "$OUTPUT" | grep -iE '\.env($|[^A-Za-z0-9])|\.backup|secret|\.pem|\.key|\.db($|[-._])|runtime/|uploads/|node_modules/|\.venv/' >/dev/null; then
  echo "Refusing to keep archive with possible secret material: $OUTPUT" >&2
  rm -f "$OUTPUT"
  exit 1
fi

echo "Built: $OUTPUT"
