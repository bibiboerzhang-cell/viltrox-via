#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Backward-compatible CI entrypoint only. All checks live in verify.sh so this
# wrapper cannot silently become a second, weaker release gate.
exec bash "$ROOT/scripts/verify.sh" "$@"
