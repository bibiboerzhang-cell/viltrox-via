#!/usr/bin/env bash
# Build a source-only V-KPI clean package with runtime build metadata.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$HOME/Downloads}"
PACKAGE_PREFIX="${PACKAGE_PREFIX:-vkpi-clean-dev-p3}"
PROJECT_DIR_NAME="${PROJECT_DIR_NAME:-V-KPI-marketing}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"

cd "$ROOT"

if [[ "$ALLOW_DIRTY" != "1" && -n "$(git status --short)" ]]; then
  echo "Refusing to package dirty worktree. Commit/stash first or set ALLOW_DIRTY=1." >&2
  git status --short >&2
  exit 1
fi

GIT_SHA="$(git rev-parse HEAD)"
GIT_SHORT="${GIT_SHA:0:8}"
BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$OUT_DIR/${PACKAGE_PREFIX}-${STAMP}-${GIT_SHORT}.zip"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$OUT_DIR"
git archive --format=tar --prefix="$PROJECT_DIR_NAME/" HEAD | tar -x -C "$TMP"

cat > "$TMP/$PROJECT_DIR_NAME/BUILD_GIT_SHA" <<EOF
$GIT_SHA
EOF
cat > "$TMP/$PROJECT_DIR_NAME/BUILD_TIME" <<EOF
$BUILD_TIME
EOF
cat > "$TMP/$PROJECT_DIR_NAME/BUILD_METADATA.json" <<EOF
{
  "git_sha": "$GIT_SHA",
  "git_short_sha": "$GIT_SHORT",
  "build_time": "$BUILD_TIME",
  "package_prefix": "$PACKAGE_PREFIX",
  "project_dir_name": "$PROJECT_DIR_NAME"
}
EOF

(cd "$TMP" && zip -qr "$OUT" "$PROJECT_DIR_NAME")
unzip -tq "$OUT" >/dev/null

FORBIDDEN_COUNT="$(
  {
    unzip -Z1 "$OUT" \
      | grep -E '(^|/)(\.git/|\.venv/|node_modules/|frontend/dist/|runtime/|uploads/|__pycache__/|\.pytest_cache/|\.mypy_cache/|\.ruff_cache/|\.DS_Store$)|\.(db|sqlite|sqlite3)$' \
      || true
  } | wc -l | tr -d ' '
)"

SECRET_COUNT="$(
  {
    grep -RIE \
      --exclude-dir=.git \
      --exclude='*.lock' \
      --exclude='BUILD_METADATA.json' \
      '(sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|apify_api_[A-Za-z0-9_-]{20,}|ANTHROPIC_API_KEY=.+|OPENAI_API_KEY=.+|GEMINI_API_KEY=.+|YOUTUBE_API_KEY=.+|APIFY_TOKEN=.+)' \
      "$TMP/$PROJECT_DIR_NAME" \
      | grep -vE '\.env\.example|example|placeholder|your_|CHANGE_ME|dummy|test_' \
      || true
  } | wc -l | tr -d ' '
)"

if [[ "$FORBIDDEN_COUNT" != "0" ]]; then
  echo "Forbidden package entries found: $FORBIDDEN_COUNT" >&2
  unzip -Z1 "$OUT" | grep -E '(^|/)(\.git/|\.venv/|node_modules/|frontend/dist/|runtime/|uploads/|__pycache__/|\.pytest_cache/|\.mypy_cache/|\.ruff_cache/|\.DS_Store$)|\.(db|sqlite|sqlite3)$' >&2
  exit 1
fi

if [[ "$SECRET_COUNT" != "0" ]]; then
  echo "Potential secret hits found: $SECRET_COUNT" >&2
  exit 1
fi

SIZE="$(du -h "$OUT" | awk '{print $1}')"
ENTRIES="$(unzip -Z1 "$OUT" | wc -l | tr -d ' ')"

cat <<EOF
Clean package ready:
  path: $OUT
  size: $SIZE
  entries: $ENTRIES
  git_sha: $GIT_SHA
  forbidden_entries: $FORBIDDEN_COUNT
  secret_hits: $SECRET_COUNT
EOF
