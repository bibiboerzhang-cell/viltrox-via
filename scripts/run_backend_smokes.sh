#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

source "$ROOT/scripts/runtime_env.sh"

echo "[smoke] checking local stack"
"$ROOT/scripts/check_local_stack.sh"

echo "[smoke] auth / social / student"
"$PYTHON_BIN" "$ROOT/scripts/smoke_auth_social_student.py"

echo "[smoke] upload / audit / video factory"
"$PYTHON_BIN" "$ROOT/scripts/smoke_upload_audit_video_factory.py"

echo "[smoke] via runtime"
"$PYTHON_BIN" "$ROOT/scripts/smoke_via_runtime.py"

echo "[smoke] all backend smoke flows passed"
