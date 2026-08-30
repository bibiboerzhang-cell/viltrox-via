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
PRIVATE_LOCAL_IDENTITY_FILE="${CANDIDATE_RUNTIME}/local-identity.env"
cleanup_private_local_env() {
  chmod u+w "${PRIVATE_LOCAL_ENV_FILE}" >/dev/null 2>&1 || true
  chmod u+w "${PRIVATE_LOCAL_IDENTITY_FILE}" >/dev/null 2>&1 || true
  rm -f -- "${PRIVATE_LOCAL_ENV_FILE}" >/dev/null 2>&1 || true
  rm -f -- "${PRIVATE_LOCAL_IDENTITY_FILE}" >/dev/null 2>&1 || true
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

# runtime_env.sh computes its local connection defaults before loading
# LOCAL_ENV_FILE.  In an env -i candidate process that would make an explicit
# LOCAL_DATABASE_URL in the reviewed file impossible to apply, silently
# reconnecting the browser gate to the developer's default database instead.
# Read only the two local connection identities here, require loopback, and
# seed them before runtime_env.sh computes any defaults.  Values stay off argv
# and logs; the protected file remains the source of every other setting.
"${PROJECT_ROOT}/.venv/bin/python" -I -B - \
  "${PRIVATE_LOCAL_ENV_FILE}" "${PRIVATE_LOCAL_IDENTITY_FILE}" <<'PY'
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
import os
import re
import shlex
import sys

from psycopg.pq import Conninfo


safe_database_query_parameters = {
    "application_name",
    "channel_binding",
    "connect_timeout",
    "fallback_application_name",
    "gssencmode",
    "keepalives",
    "keepalives_count",
    "keepalives_idle",
    "keepalives_interval",
    "ssl_min_protocol_version",
    "ssl_max_protocol_version",
    "sslcrl",
    "sslcrldir",
    "sslmode",
    "sslrootcert",
    "sslsni",
    "tcp_user_timeout",
}
values: dict[str, str] = {}
for raw_line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key == "ENV_FILE" or re.fullmatch(r"PG[A-Z0-9_]+", key):
        raise SystemExit("candidate local environment contains forbidden connection controls")
    if key not in {"LOCAL_DATABASE_URL", "LOCAL_REDIS_URL", "REDIS_URL"}:
        continue
    if key in values:
        raise SystemExit("candidate local environment has duplicate connection identity")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value or any(
        ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ):
        raise SystemExit("candidate local environment has invalid connection identity")
    values[key] = value

database_url = values.get("LOCAL_DATABASE_URL", "")
redis_url = values.get("LOCAL_REDIS_URL") or values.get("REDIS_URL", "")
if (
    values.get("LOCAL_REDIS_URL")
    and values.get("REDIS_URL")
    and values["LOCAL_REDIS_URL"] != values["REDIS_URL"]
):
    raise SystemExit("candidate local redis identities are ambiguous")
try:
    database = urlsplit(database_url)
    database_port = database.port
    database_query = parse_qsl(
        database.query,
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=64,
    )
    redis = urlsplit(redis_url)
    redis_port = redis.port
except ValueError as exc:
    raise SystemExit("candidate local connection identity is invalid") from exc

database_name = unquote(database.path[1:]) if database.path.startswith("/") else ""
if (
    database.scheme.lower() not in {"postgres", "postgresql"}
    or database.hostname not in {"127.0.0.1", "localhost", "::1"}
    or database_port is None
    or database_port < 1
    or database.fragment
    or database.path.count("/") != 1
    or not database_name
    or "/" in database_name
    or "\x00" in database_name
    or any(
        key.lower() not in safe_database_query_parameters
        for key, _value in database_query
    )
):
    raise SystemExit("candidate local database identity is unsafe")
try:
    libpq_database = {
        option.keyword.decode("ascii"): (
            option.val.decode("utf-8") if option.val is not None else None
        )
        for option in Conninfo.parse(database_url.encode("utf-8"))
    }
except Exception as exc:
    raise SystemExit("candidate local database identity is invalid") from exc
expected_user = unquote(database.username) if database.username is not None else None
expected_password = (
    unquote(database.password) if database.password is not None else None
)
if (
    libpq_database.get("host") != database.hostname
    or libpq_database.get("hostaddr") not in {None, database.hostname}
    or libpq_database.get("port") != str(database_port)
    or libpq_database.get("dbname") != database_name
    or libpq_database.get("user") != expected_user
    or libpq_database.get("password") != expected_password
    or libpq_database.get("service") is not None
    or libpq_database.get("options") is not None
    or libpq_database.get("load_balance_hosts") is not None
):
    raise SystemExit("candidate local database identity disagrees with libpq")
if (
    redis.scheme.lower() not in {"redis", "rediss"}
    or redis.hostname not in {"127.0.0.1", "localhost", "::1"}
    or redis_port is None
    or redis_port < 1
    or redis.fragment
    or redis.query
    or not re.fullmatch(r"/[0-9]+", redis.path)
):
    raise SystemExit("candidate local redis identity is unsafe")

destination = Path(sys.argv[2])
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_CLOEXEC"):
    flags |= os.O_CLOEXEC
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
fd = os.open(destination, flags, 0o600)
try:
    payload = (
        f"LOCAL_DATABASE_URL={shlex.quote(database_url)}\n"
        f"LOCAL_REDIS_URL={shlex.quote(redis_url)}\n"
    ).encode("utf-8")
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise SystemExit("candidate local identity write failed")
        view = view[written:]
    os.fsync(fd)
finally:
    os.close(fd)
PY
# shellcheck disable=SC1090 -- generated from the reviewed protected env above.
source "${PRIVATE_LOCAL_IDENTITY_FILE}"
export LOCAL_DATABASE_URL LOCAL_REDIS_URL
chmod u+w "${PRIVATE_LOCAL_IDENTITY_FILE}"
rm -f -- "${PRIVATE_LOCAL_IDENTITY_FILE}"
if [ -e "${CANDIDATE_ROOT}/.env.local" ] \
  || [ -L "${CANDIDATE_ROOT}/.env.local" ] \
  || [ -e "${CANDIDATE_ROOT}/runtime/local_operator_env.sh" ] \
  || [ -L "${CANDIDATE_ROOT}/runtime/local_operator_env.sh" ]; then
  echo "candidate contains an unreviewed runtime environment override" >&2
  exit 64
fi
export ENV_FILE=""
source "${CANDIDATE_ROOT}/scripts/runtime_env.sh"
cleanup_private_local_env
trap - EXIT HUP INT TERM
unset \
  CANDIDATE_LOCAL_ENV_FILE \
  LOCAL_ENV_FILE \
  PRIVATE_LOCAL_ENV_FILE \
  PRIVATE_LOCAL_IDENTITY_FILE

# Do not let libpq's ambient environment override the reviewed URL identity.
# The launcher began with env -i; these unsets also cover values loaded from
# the private local environment by runtime_env.sh.
unset \
  PGAPPNAME \
  PGCHANNELBINDING \
  PGCLIENTENCODING \
  PGCONNECT_TIMEOUT \
  PGDATABASE \
  PGGSSENCMODE \
  PGHOST \
  PGHOSTADDR \
  PGKEEPALIVES \
  PGKEEPALIVESCOUNT \
  PGKEEPALIVESIDLE \
  PGKEEPALIVESINTERVAL \
  PGOPTIONS \
  PGPASSFILE \
  PGPASSWORD \
  PGPORT \
  PGSERVICE \
  PGSERVICEFILE \
  PGSSLCERT \
  PGSSLCRL \
  PGSSLCRLDIR \
  PGSSLKEY \
  PGSSLMAXPROTOCOLVERSION \
  PGSSLMINPROTOCOLVERSION \
  PGSSLMODE \
  PGSSLNEGOTIATION \
  PGSSLROOTCERT \
  PGTARGETSESSIONATTRS \
  PGTCPUSER_TIMEOUT \
  PGUSER

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
exec "${CANDIDATE_PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}" -B -m gunicorn app.main:app \
  -c "${CANDIDATE_ROOT}/deploy/gunicorn_config.py" \
  --pythonpath "${CANDIDATE_ROOT}/backend" \
  --access-logfile - \
  --error-logfile -
