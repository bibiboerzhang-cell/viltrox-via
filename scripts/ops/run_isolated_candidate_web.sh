#!/usr/bin/env bash
set -euo pipefail

for required_name in \
  PROJECT_ROOT \
  CANDIDATE_ROOT \
  CANDIDATE_RUNTIME \
  CANDIDATE_LOCAL_ENV_FILE \
  CANDIDATE_PORT \
  APP_GIT_SHA \
  APP_GIT_BRANCH \
  APP_BUILD_TIME; do
  if [ -z "${!required_name:-}" ]; then
    echo "isolated candidate web is missing ${required_name}" >&2
    exit 64
  fi
done

case "${PROJECT_ROOT}" in /*) ;; *) exit 64 ;; esac
case "${CANDIDATE_ROOT}" in /*) ;; *) exit 64 ;; esac
case "${CANDIDATE_RUNTIME}" in /tmp/vkpi-candidate-browser-runtime.*/runtime) ;; *) exit 64 ;; esac
case "${CANDIDATE_LOCAL_ENV_FILE}" in /*) ;; *) exit 64 ;; esac
if ! [[ "${CANDIDATE_PORT}" =~ ^[1-9][0-9]*$ ]] \
  || [ "${CANDIDATE_PORT}" -lt 1024 ] \
  || [ "${CANDIDATE_PORT}" -gt 65535 ] \
  || ! [[ "${APP_GIT_SHA}" =~ ^[0-9a-f]{40}$ ]] \
  || [ ! -d "${PROJECT_ROOT}" ] \
  || [ -L "${PROJECT_ROOT}" ] \
  || [ ! -d "${CANDIDATE_ROOT}" ] \
  || [ -L "${CANDIDATE_ROOT}" ] \
  || [ ! -e "${CANDIDATE_LOCAL_ENV_FILE}" ] \
  || [ ! -x "${PROJECT_ROOT}/.venv/bin/python" ] \
  || [ ! -f "${CANDIDATE_ROOT}/scripts/runtime_env.sh" ] \
  || [ -L "${CANDIDATE_ROOT}/scripts/runtime_env.sh" ] \
  || [ ! -f "${CANDIDATE_ROOT}/deploy/gunicorn_config.py" ] \
  || [ -L "${CANDIDATE_ROOT}/deploy/gunicorn_config.py" ]; then
  echo "isolated candidate web inputs are unsafe" >&2
  exit 64
fi

umask 077
mkdir -p "${HOME}" "${XDG_CACHE_HOME}" "${TMPDIR}" "${CANDIDATE_RUNTIME}"
chmod 700 "${HOME}" "${XDG_CACHE_HOME}" "${TMPDIR}" "${CANDIDATE_RUNTIME}"
PRIVATE_LOCAL_ENV_FILE="${CANDIDATE_RUNTIME}/local.env"
cleanup_private_local_env() {
  chmod u+w "${PRIVATE_LOCAL_ENV_FILE}" >/dev/null 2>&1 || true
  rm -f -- "${PRIVATE_LOCAL_ENV_FILE}" >/dev/null 2>&1 || true
}
trap cleanup_private_local_env EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
"${PROJECT_ROOT}/.venv/bin/python" -I -B - \
  "${CANDIDATE_LOCAL_ENV_FILE}" "${PRIVATE_LOCAL_ENV_FILE}" <<'PY'
from pathlib import Path
import os
import stat
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
lexical = source.lstat()
if not (stat.S_ISREG(lexical.st_mode) or stat.S_ISLNK(lexical.st_mode)):
    raise SystemExit("candidate local environment path is unsafe")
flags = os.O_RDONLY | os.O_NONBLOCK
if hasattr(os, "O_CLOEXEC"):
    flags |= os.O_CLOEXEC
fd = os.open(source, flags)
try:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_size > 1024 * 1024
    ):
        raise SystemExit("candidate local environment file is unsafe")
    chunks: list[bytes] = []
    remaining = before.st_size + 1
    while remaining > 0:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    after = os.fstat(fd)
    if (
        len(payload) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise SystemExit("candidate local environment file changed while reading")
finally:
    os.close(fd)

write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_CLOEXEC"):
    write_flags |= os.O_CLOEXEC
if hasattr(os, "O_NOFOLLOW"):
    write_flags |= os.O_NOFOLLOW
out_fd = os.open(destination, write_flags, 0o600)
try:
    view = memoryview(payload)
    while view:
        written = os.write(out_fd, view)
        if written <= 0:
            raise SystemExit("candidate local environment copy failed")
        view = view[written:]
    os.fsync(out_fd)
finally:
    os.close(out_fd)
PY
export LOCAL_ENV_FILE="${PRIVATE_LOCAL_ENV_FILE}"
export ENVIRONMENT=local
export RUNTIME_ENV_QUIET=1
export RUNTIME_ROOT="${CANDIDATE_RUNTIME}"
export LOCAL_RUNTIME_FORCE_STACK=1
export RUNTIME_ENV_KEEP_DB_URL=0
export RUNTIME_ENV_KEEP_INHERITED_JWT=0
source "${CANDIDATE_ROOT}/scripts/runtime_env.sh"
cleanup_private_local_env
trap - EXIT HUP INT TERM
unset CANDIDATE_LOCAL_ENV_FILE LOCAL_ENV_FILE PRIVATE_LOCAL_ENV_FILE

# The candidate browser gate is a read-only release-validation runtime.  It
# may use the reviewed local database and Redis health state, but it must not
# inherit provider credentials, monitoring exporters, paid transports, or the
# operator's proxy.  The validation fence also blocks DB writes, queue work,
# and non-reviewed HTTP paths across the application layers.
export VKPI_SKIP_DOTENV=1
export VKPI_ASYNC_ENABLED=0
export VKPI_MEDIA_CACHE_STORAGE=local
export VKPI_RELEASE_VALIDATION_FENCE_PATH="${CANDIDATE_RUNTIME}/release-validation.fence"
printf 'vkpi-release-validation/v1\n' >"${VKPI_RELEASE_VALIDATION_FENCE_PATH}"
chmod 444 "${VKPI_RELEASE_VALIDATION_FENCE_PATH}"

unset \
  ANTHROPIC_API_KEY \
  APIFY_API_TOKEN \
  APIFY_TOKEN \
  APIFY_TOKEN_PREVIOUS \
  AWS_ACCESS_KEY_ID \
  AWS_SECRET_ACCESS_KEY \
  AWS_SESSION_TOKEN \
  CLOUDFLARE_API_TOKEN \
  GEMINI_API_KEY \
  GEMINI_API_KEYS \
  GOOGLE_API_KEY \
  GOOGLE_CSE_API_KEY \
  GOOGLE_GENERATIVE_AI_API_KEY \
  GOOGLE_SEARCH_API_KEY \
  GOOGLE_YOUTUBE_API_KEY \
  GOAFFPRO_ACCESS_TOKEN \
  OPENAI_API_KEY \
  R2_ACCESS_KEY_ID \
  R2_BUCKET_NAME \
  R2_ENDPOINT \
  R2_SECRET_ACCESS_KEY \
  RESEND_API_KEY \
  RESEND_API_KEY_PREVIOUS \
  SENTRY_DSN \
  SHOPIFY_ACCESS_TOKEN \
  VKPI_17TRACK_TOKEN \
  YOUTUBE_API_KEY \
  YOUTUBE_DATA_API_KEY \
  YTDLP_PROXY \
  OPENAI_PROXY \
  HTTP_PROXY \
  HTTPS_PROXY \
  ALL_PROXY \
  http_proxy \
  https_proxy \
  all_proxy
export NO_PROXY="127.0.0.1,localhost,::1"
export no_proxy="${NO_PROXY}"

export APP_ROLE=admin-web
export APP_GIT_SHA APP_GIT_BRANCH APP_BUILD_TIME
export ENABLE_LOCAL_ORCHESTRATOR=0
export ENABLE_SCHEDULER=0
export ENABLE_BROWSER=0
export ENABLE_UPLOAD_CLEANUP=0
export VKPI_ADVISOR_EXTERNAL_AI_ENABLED=0
export VKPI_EXTERNAL_SIGNAL_AUTOWRITE_ENABLED=0
export VKPI_LLM_LOCAL_EVALUATION_ENABLED=0
export RECALL_LLM_RERANK_ENABLED=0
export ADMIN_DAEMON=0
export WORKERS=1
export WEB_CONCURRENCY=1
export WORKER_CONNECTIONS=256
export BACKLOG=128
export POSTGRES_POOL_MIN_SIZE=1
export POSTGRES_POOL_MAX_SIZE=4
export POSTGRES_POOL_TIMEOUT_SEC=30
export DB_USE_PGBOUNCER=0
unset DATABASE_POOL_URL
export BIND="127.0.0.1:${CANDIDATE_PORT}"
export PIDFILE="${CANDIDATE_RUNTIME}/gunicorn.pid"
export LOG_LEVEL=warning
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${CANDIDATE_ROOT}/backend"

# Keep fallback `.env` readers and relative uploads/frames/profile writes away
# from the source worktree.  Python modules and frontend/dist still come only
# from the frozen candidate; runtime-only directories live under the private
# temporary root and disappear with the candidate process group.
cd "${CANDIDATE_RUNTIME}"
exec "${PROJECT_ROOT}/.venv/bin/python" -B -m gunicorn app.main:app \
  -c "${CANDIDATE_ROOT}/deploy/gunicorn_config.py" \
  --pythonpath "${CANDIDATE_ROOT}/backend" \
  --access-logfile - \
  --error-logfile -
