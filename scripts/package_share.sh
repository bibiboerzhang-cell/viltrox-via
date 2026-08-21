#!/usr/bin/env bash
# Build a source-only handoff archive from Git-tracked files.
#
# Security contract:
#   - untracked files are never considered;
#   - environment files, keys/certificates, databases/backups, runtime output,
#     caches, dependency trees, and report output are rejected before copying;
#   - existing archives and sidecars are never overwritten;
#   - excluded files are not opened and their paths are not printed.
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$SCRIPT_ROOT"
ARCHIVE=""
DRY_RUN=0
LIST_FILES=0
MAX_FILE_BYTES=$((25 * 1024 * 1024))
PAYLOAD_NAME="viltrox-2.0"

usage() {
  cat <<'EOF'
Usage: scripts/package_share.sh [options]

Options:
  --root PATH      Package this Git checkout (default: script repository root).
  --output PATH    Archive path (default: <root>/viltrox-2.0-share.tar.gz).
  --dry-run        Validate and summarize the tracked allowlist; write nothing.
  --list           Print the validated payload file list.
  -h, --help       Show this help.

Successful builds create three new files and refuse to overwrite any of them:
  <archive>
  <archive>.sha256
  <archive>.files.txt
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      [[ $# -ge 2 ]] || { echo "package-share: --root requires a path" >&2; exit 2; }
      ROOT="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || { echo "package-share: --output requires a path" >&2; exit 2; }
      ARCHIVE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --list)
      LIST_FILES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "package-share: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

command -v git >/dev/null 2>&1 || {
  echo "package-share: git is required" >&2
  exit 2
}
command -v python3 >/dev/null 2>&1 || {
  echo "package-share: python3 is required for path-only safety validation" >&2
  exit 2
}
command -v rsync >/dev/null 2>&1 || {
  echo "package-share: rsync is required" >&2
  exit 2
}
command -v tar >/dev/null 2>&1 || {
  echo "package-share: tar is required" >&2
  exit 2
}

ROOT="$(cd "$ROOT" 2>/dev/null && pwd -P)" || {
  echo "package-share: repository root does not exist" >&2
  exit 2
}
GIT_ROOT="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "package-share: --root must be inside a Git checkout" >&2
  exit 2
}
GIT_ROOT="$(cd "$GIT_ROOT" && pwd -P)"
if [[ "$ROOT" != "$GIT_ROOT" ]]; then
  echo "package-share: --root must be the Git checkout root" >&2
  exit 2
fi

IGNORE_FILE="$ROOT/.shareignore"
[[ -f "$IGNORE_FILE" && ! -L "$IGNORE_FILE" ]] || {
  echo "package-share: required regular file is missing: .shareignore" >&2
  exit 2
}

if [[ -z "$ARCHIVE" ]]; then
  ARCHIVE="$ROOT/viltrox-2.0-share.tar.gz"
elif [[ "$ARCHIVE" != /* ]]; then
  ARCHIVE="$PWD/$ARCHIVE"
fi
ARCHIVE_DIR="$(dirname "$ARCHIVE")"
ARCHIVE_BASENAME="$(basename "$ARCHIVE")"
SHA_FILE="${ARCHIVE}.sha256"
FILES_FILE="${ARCHIVE}.files.txt"

if [[ "$DRY_RUN" != "1" ]]; then
  for destination in "$ARCHIVE" "$SHA_FILE" "$FILES_FILE"; do
    if [[ -e "$destination" || -L "$destination" ]]; then
      echo "package-share: refusing to overwrite an existing archive or sidecar" >&2
      exit 2
    fi
  done
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vkpi-package-share.XXXXXX")"
cleanup() {
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

TRACKED_NUL="$WORK_DIR/tracked-safe.nul"
TRACKED_MANIFEST="$WORK_DIR/tracked-safe.files.txt"
STATS_FILE="$WORK_DIR/stats.txt"

# This helper inspects Git path metadata, file type, symlink target, and size.
# It deliberately never opens a candidate source file. Excluded paths are
# counted only in aggregate and are never printed.
VKPI_SHARE_ROOT="$ROOT" \
VKPI_SHARE_LIST_NUL="$TRACKED_NUL" \
VKPI_SHARE_MANIFEST="$TRACKED_MANIFEST" \
VKPI_SHARE_STATS="$STATS_FILE" \
VKPI_SHARE_PAYLOAD="$PAYLOAD_NAME" \
VKPI_SHARE_MAX_FILE_BYTES="$MAX_FILE_BYTES" \
python3 - <<'PY'
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import subprocess

root = Path(os.environ["VKPI_SHARE_ROOT"])
list_path = Path(os.environ["VKPI_SHARE_LIST_NUL"])
manifest_path = Path(os.environ["VKPI_SHARE_MANIFEST"])
stats_path = Path(os.environ["VKPI_SHARE_STATS"])
payload = os.environ["VKPI_SHARE_PAYLOAD"]
max_file_bytes = int(os.environ["VKPI_SHARE_MAX_FILE_BYTES"])

blocked_components = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".cache",
    "runtime",
    "reports",
    "artifacts",
    "exports",
    "output",
    "outputs",
    "tmp",
    "uploads",
    "frames",
    "creator_profiles",
    "backups",
    "backup",
    "snapshots",
}
blocked_suffixes = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".der",
    ".jks",
    ".keystore",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".dump",
    ".pgdump",
    ".backup",
    ".bak",
    ".db-wal",
    ".db-shm",
    ".tar",
    ".tgz",
    ".zip",
    ".gz",
    ".7z",
}
blocked_names = {
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "client_secret.json",
    "service-account.json",
    "service_account.json",
    "submissions.db",
}


def is_blocked(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path:
        return True
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return True
    pure = PurePosixPath(path)
    if any(part in {"", ".", ".."} for part in pure.parts):
        return True
    lowered_parts = tuple(part.lower() for part in pure.parts)
    if any(part in blocked_components for part in lowered_parts):
        return True
    if any(
        lowered_parts[index : index + 2] == ("frontend", "dist")
        for index in range(len(lowered_parts) - 1)
    ):
        return True
    name = lowered_parts[-1]
    # The root example is a reviewed, empty-value startup contract. Nested or
    # environment-specific variants remain blocked because they can carry secrets.
    if (name == ".env" or name.startswith(".env.")) and path != ".env.example":
        return True
    if name in blocked_names:
        return True
    if any(name.endswith(suffix) for suffix in blocked_suffixes):
        return True
    # SQL is executable schema source only under reviewed migration/runtime paths.
    # migrations_v5.py loads this exact bootstrap asset at runtime.
    reviewed_runtime_sql = path == "backend/app/db/sql/001_v5_admin_schema.sql"
    if name.endswith(".sql") and lowered_parts[0] != "migrations" and not reviewed_runtime_sql:
        return True
    return False


raw = subprocess.check_output(
    ["git", "-C", str(root), "ls-files", "--cached", "--stage", "-z"],
)
safe: list[tuple[str, int]] = []
excluded_count = 0

for record in raw.split(b"\0"):
    if not record:
        continue
    metadata, separator, encoded_path = record.partition(b"\t")
    if not separator:
        raise SystemExit("package-share: malformed Git index entry")
    try:
        path = encoded_path.decode("utf-8", "strict")
    except UnicodeDecodeError:
        excluded_count += 1
        continue
    mode = metadata.split(b" ", 1)[0]
    if mode not in {b"100644", b"100755", b"120000"} or is_blocked(path):
        excluded_count += 1
        continue
    source = root / path
    if not os.path.lexists(source):
        # A tracked deletion is not part of the current handoff payload.
        excluded_count += 1
        continue
    if source.is_symlink():
        target = os.readlink(source)
        target_parts = PurePosixPath(target).parts
        if os.path.isabs(target) or ".." in target_parts or is_blocked(target):
            excluded_count += 1
            continue
        size = 0
    elif source.is_file():
        size = source.stat().st_size
        if size > max_file_bytes:
            excluded_count += 1
            continue
    else:
        excluded_count += 1
        continue
    safe.append((path, size))

safe.sort(key=lambda item: item[0])
if not safe:
    raise SystemExit("package-share: tracked allowlist is empty")

with list_path.open("wb") as handle:
    for path, _size in safe:
        handle.write(path.encode("utf-8") + b"\0")
with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
    for path, _size in safe:
        handle.write(f"{payload}/{path}\n")
stats_path.write_text(
    f"files={len(safe)}\nbytes={sum(size for _path, size in safe)}\n"
    f"excluded={excluded_count}\n",
    encoding="utf-8",
)
PY

# shellcheck disable=SC1090
source "$STATS_FILE"
echo "package-share: tracked allowlist validated (files=$files bytes=$bytes excluded=$excluded)"

if [[ "$LIST_FILES" == "1" ]]; then
  cat "$TRACKED_MANIFEST"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "package-share: dry-run complete; no archive or sidecar was written"
  exit 0
fi

STAGE="$WORK_DIR/stage"
PAYLOAD_ROOT="$STAGE/$PAYLOAD_NAME"
mkdir -p "$PAYLOAD_ROOT"

# --files-from is the Git-derived allowlist. --exclude-from is a second,
# independent defense in depth against future filtering regressions.
rsync -a \
  --from0 \
  --files-from="$TRACKED_NUL" \
  --exclude-from="$IGNORE_FILE" \
  --no-owner \
  --no-group \
  "$ROOT/" "$PAYLOAD_ROOT/"

ACTUAL_MANIFEST="$WORK_DIR/$ARCHIVE_BASENAME.files.txt"
VKPI_SHARE_STAGE="$PAYLOAD_ROOT" \
VKPI_SHARE_EXPECTED="$TRACKED_MANIFEST" \
VKPI_SHARE_ACTUAL="$ACTUAL_MANIFEST" \
VKPI_SHARE_PAYLOAD="$PAYLOAD_NAME" \
python3 - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

stage = Path(os.environ["VKPI_SHARE_STAGE"])
expected_path = Path(os.environ["VKPI_SHARE_EXPECTED"])
actual_path = Path(os.environ["VKPI_SHARE_ACTUAL"])
payload = os.environ["VKPI_SHARE_PAYLOAD"]

actual: list[str] = []
for candidate in stage.rglob("*"):
    if candidate.is_file() or candidate.is_symlink():
        actual.append(f"{payload}/{candidate.relative_to(stage).as_posix()}")
actual.sort()
expected = expected_path.read_text(encoding="utf-8").splitlines()
if actual != expected:
    raise SystemExit("package-share: staged payload differs from the validated tracked allowlist")
actual_path.write_text("".join(f"{path}\n" for path in actual), encoding="utf-8")
PY

TMP_ARCHIVE="$WORK_DIR/$ARCHIVE_BASENAME"
COPYFILE_DISABLE=1 tar -czf "$TMP_ARCHIVE" -C "$STAGE" "$PAYLOAD_NAME"

# Validate the archive headers against the staged manifest without extraction.
VKPI_SHARE_ARCHIVE="$TMP_ARCHIVE" \
VKPI_SHARE_MANIFEST="$ACTUAL_MANIFEST" \
python3 - <<'PY'
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import tarfile

archive = Path(os.environ["VKPI_SHARE_ARCHIVE"])
manifest = Path(os.environ["VKPI_SHARE_MANIFEST"])
expected = manifest.read_text(encoding="utf-8").splitlines()

with tarfile.open(archive, mode="r:gz") as handle:
    members = handle.getmembers()

actual: list[str] = []
for member in members:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("package-share: archive contains an unsafe member path")
    if member.ischr() or member.isblk() or member.isfifo():
        raise SystemExit("package-share: archive contains an unsafe special file")
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname)
        if target.is_absolute() or ".." in target.parts:
            raise SystemExit("package-share: archive contains an unsafe link target")
    if not member.isdir():
        actual.append(member.name.rstrip("/"))
actual.sort()
if actual != expected:
    raise SystemExit("package-share: archive file list differs from the staged manifest")
PY

sha256_file() {
  local path="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  else
    echo "package-share: shasum or sha256sum is required" >&2
    return 2
  fi
}

DIGEST="$(sha256_file "$TMP_ARCHIVE")"
TMP_SHA="$WORK_DIR/$ARCHIVE_BASENAME.sha256"
printf '%s  %s\n' "$DIGEST" "$ARCHIVE_BASENAME" >"$TMP_SHA"

mkdir -p "$ARCHIVE_DIR"
for destination in "$ARCHIVE" "$SHA_FILE" "$FILES_FILE"; do
  if [[ -e "$destination" || -L "$destination" ]]; then
    echo "package-share: refusing to overwrite an existing archive or sidecar" >&2
    exit 2
  fi
done

mv "$TMP_ARCHIVE" "$ARCHIVE"
mv "$TMP_SHA" "$SHA_FILE"
mv "$ACTUAL_MANIFEST" "$FILES_FILE"

echo "package-share: archive=$ARCHIVE"
echo "package-share: sha256=$SHA_FILE"
echo "package-share: files=$FILES_FILE"
