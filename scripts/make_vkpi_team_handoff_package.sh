#!/usr/bin/env bash
# Build a current-worktree V-KPI team handoff package.
#
# Unlike make_vkpi_clean_package.sh, this script packages the current working
# tree so P3 QA fixes that are not committed yet can still be reviewed by the
# team. It is intentionally strict about secrets, caches, logs, backups, and
# oversized local artifacts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$HOME/Downloads}"
PACKAGE_PREFIX="${PACKAGE_PREFIX:-vkpi-team-handoff-p3}"
PROJECT_DIR_NAME="${PROJECT_DIR_NAME:-V-KPI-marketing}"
RELEASE_NOTES="${RELEASE_NOTES:-$ROOT/docs/VKPI_P3_16_TEAM_HANDOFF_RELEASE_NOTES.md}"
MAX_FILE_MB="${MAX_FILE_MB:-50}"

cd "$ROOT"

STAMP="$(date +%Y%m%d-%H%M%S)"
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_SHORT="${GIT_SHA:0:8}"
BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
DIRTY_COUNT="$(git status --short 2>/dev/null | wc -l | tr -d ' ')"
OUT="$OUT_DIR/${PACKAGE_PREFIX}-${STAMP}-${GIT_SHORT}.zip"
TMP="$(mktemp -d)"
STAGE="$TMP/$PROJECT_DIR_NAME"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$OUT_DIR" "$STAGE"

rsync -a --delete \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='node_modules' \
  --exclude='frontend/node_modules' \
  --exclude='frontend/dist' \
  --exclude='dist' \
  --exclude='build' \
  --exclude='runtime/logs' \
  --exclude='runtime/backups' \
  --exclude='runtime/tmp' \
  --exclude='uploads' \
  --exclude='frames' \
  --exclude='logs' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='.share-stage' \
  --exclude='.debug-stage' \
  --exclude='*backup*/' \
  --exclude='snapshot-*/' \
  --exclude='*.backup' \
  --exclude='*.bak' \
  --exclude='*.env' \
  --exclude='*.env.*' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='backend/.env' \
  --exclude='*.pem' \
  --exclude='*.key' \
  --exclude='*secret*' \
  --exclude='*.zip' \
  --exclude='*.tar' \
  --exclude='*.tar.gz' \
  --exclude='*.tgz' \
  --exclude='*.dump' \
  --exclude='*.db' \
  --exclude='*.sqlite' \
  --exclude='*.sqlite3' \
  --exclude='*.db-wal' \
  --exclude='*.db-shm' \
  --exclude='*.xlsx' \
  --exclude='*.xls' \
  --exclude='~$*' \
  "$ROOT/" "$STAGE/"

cat > "$STAGE/BUILD_GIT_SHA" <<EOF
$GIT_SHA
EOF
cat > "$STAGE/BUILD_TIME" <<EOF
$BUILD_TIME
EOF
cat > "$STAGE/BUILD_METADATA.json" <<EOF
{
  "git_sha": "$GIT_SHA",
  "git_short_sha": "$GIT_SHORT",
  "build_time": "$BUILD_TIME",
  "package_prefix": "$PACKAGE_PREFIX",
  "project_dir_name": "$PROJECT_DIR_NAME",
  "package_mode": "current_worktree",
  "dirty_count": $DIRTY_COUNT
}
EOF

if [[ -f "$RELEASE_NOTES" ]]; then
  cp "$RELEASE_NOTES" "$STAGE/RELEASE_NOTES.md"
fi

LARGE_COUNT="$(
  find "$STAGE" -type f -size +"${MAX_FILE_MB}"M -print | wc -l | tr -d ' '
)"
if [[ "$LARGE_COUNT" != "0" ]]; then
  echo "Refusing to package oversized files (> ${MAX_FILE_MB}MB):" >&2
  find "$STAGE" -type f -size +"${MAX_FILE_MB}"M -print >&2
  exit 1
fi

SECRET_COUNT="$(
  {
    grep -RIE \
      --exclude-dir=.git \
      --exclude-dir=.venv \
      --exclude-dir=node_modules \
      --exclude='*.lock' \
      --exclude='BUILD_METADATA.json' \
      --exclude='make_vkpi_clean_package.sh' \
      --exclude='make_vkpi_team_handoff_package.sh' \
      --exclude='make_debug_zip.sh' \
      '(sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|apify_api_[A-Za-z0-9_-]{20,}|ANTHROPIC_API_KEY=.+|OPENAI_API_KEY=.+|GEMINI_API_KEY=.+|YOUTUBE_API_KEY=.+|APIFY_TOKEN=.+|GEMINI_API_KEY=.+)' \
      "$STAGE" \
      | grep -vE '\.env\.example|example|placeholder|your_|CHANGE_ME|dummy|test_|REDACTED|not_set' \
      || true
  } | wc -l | tr -d ' '
)"
if [[ "$SECRET_COUNT" != "0" ]]; then
  echo "Refusing to package possible secrets. Hits: $SECRET_COUNT" >&2
  exit 1
fi

(cd "$TMP" && zip -qr "$OUT" "$PROJECT_DIR_NAME")
unzip -tq "$OUT" >/dev/null

FORBIDDEN_COUNT="$(
  {
    unzip -Z1 "$OUT" \
      | grep -E '(^|/)(\.git/|\.venv/|node_modules/|frontend/dist/|runtime/logs/|runtime/backups/|uploads/|__pycache__/|\.pytest_cache/|\.mypy_cache/|\.ruff_cache/|\.DS_Store$)|\.(db|sqlite|sqlite3|dump|pem|key|xlsx|xls)$' \
      || true
  } | wc -l | tr -d ' '
)"
if [[ "$FORBIDDEN_COUNT" != "0" ]]; then
  echo "Forbidden package entries found: $FORBIDDEN_COUNT" >&2
  unzip -Z1 "$OUT" | grep -E '(^|/)(\.git/|\.venv/|node_modules/|frontend/dist/|runtime/logs/|runtime/backups/|uploads/|__pycache__/|\.pytest_cache/|\.mypy_cache/|\.ruff_cache/|\.DS_Store$)|\.(db|sqlite|sqlite3|dump|pem|key|xlsx|xls)$' >&2
  exit 1
fi

SIZE="$(du -h "$OUT" | awk '{print $1}')"
ENTRIES="$(unzip -Z1 "$OUT" | wc -l | tr -d ' ')"

cat <<EOF
Team handoff package ready:
  path: $OUT
  size: $SIZE
  entries: $ENTRIES
  git_sha: $GIT_SHA
  dirty_count: $DIRTY_COUNT
  forbidden_entries: $FORBIDDEN_COUNT
  secret_hits: $SECRET_COUNT
  oversized_files: $LARGE_COUNT
EOF
