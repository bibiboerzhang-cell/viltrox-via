from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
# The frozen release candidate intentionally excludes the workspace virtualenv.
# Reuse the virtualenv that is already running pytest, while preserving its
# non-resolved path because the extracted production function derives Python
# from PROJECT_ROOT/.venv/bin/python.
VENV_PYTHON = Path(sys.executable)
RUNTIME_PROJECT_ROOT = VENV_PYTHON.parents[2]

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="These probes assert Darwin TCP errno and Bash 3.2 process-group behavior.",
)


def _extract_shell_function(name: str, next_name: str) -> str:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start_marker = f"{name}() {{"
    end_marker = f"\n}}\n\n{next_name}() {{"
    start = source.index(start_marker)
    end = source.index(end_marker, start) + 2
    return source[start:end]


def _run_bash(script: str, *, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _failure_details(result: subprocess.CompletedProcess[str]) -> str:
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_candidate_cleanup_escalates_from_term_to_kill_for_entire_setsid_group() -> None:
    cleanup_function = _extract_shell_function(
        "cleanup_local_candidate_browser_runtime",
        "cleanup_initial_deploy_resources",
    )
    child_code = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(300)"
    )
    leader_code = (
        "import os,signal,subprocess,sys,time; "
        "os.setsid(); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "code=os.environ['PROBE_CHILD_CODE']; "
        "subprocess.Popen([sys.executable,'-I','-B','-c',code]); "
        "subprocess.Popen([sys.executable,'-I','-B','-c',code]); "
        "time.sleep(300)"
    )
    script = (
        "set -euo pipefail\n"
        f"PROJECT_ROOT={shlex.quote(str(RUNTIME_PROJECT_ROOT))}\n"
        f"PROBE_PY={shlex.quote(str(VENV_PYTHON))}\n"
        + cleanup_function
        + "\n"
        + f"PROBE_CHILD_CODE={shlex.quote(child_code)}\n"
        + f"PROBE_LEADER_CODE={shlex.quote(leader_code)}\n"
        + r'''
leader=""
runtime_root=""
physical_tmp="$(cd /tmp && pwd -P)"
emergency_cleanup() {
  set +e
  trap - EXIT
  if [[ "${leader}" =~ ^[1-9][0-9]*$ ]]; then
    builtin kill -KILL -- "-${leader}" >/dev/null 2>&1 || true
    wait "${leader}" >/dev/null 2>&1 || true
  fi
  if [[ "${runtime_root}" == "${physical_tmp%/}"/vkpi-candidate-browser-runtime.pytest.* ]] \
    && [ -d "${runtime_root}" ]; then
    /bin/rmdir -- "${runtime_root}" >/dev/null 2>&1 || true
  fi
}
trap emergency_cleanup EXIT

runtime_root="$(mktemp -d "${physical_tmp%/}/vkpi-candidate-browser-runtime.pytest.XXXXXX")"
PROBE_CHILD_CODE="${PROBE_CHILD_CODE}" \
  "${PROBE_PY}" -I -B -c "${PROBE_LEADER_CODE}" &
leader=$!
members=0
pgid=""
for _ in $(seq 1 100); do
  pgid="$(ps -p "${leader}" -o pgid= 2>/dev/null | tr -d '[:space:]')"
  members="$(
    ps -axo pgid=,stat= 2>/dev/null \
      | awk -v expected="${leader}" \
        '$1 == expected && $2 !~ /^Z/ { count++ } END { print count + 0 }'
  )"
  if [ "${pgid}" = "${leader}" ] && [ "${members}" -ge 3 ]; then
    break
  fi
  /bin/sleep 0.01
done
test "${pgid}" = "${leader}"
test "${members}" -ge 3
/bin/sleep 0.05

LOCAL_CANDIDATE_WEB_PID="${leader}"
LOCAL_CANDIDATE_WEB_PGID="${leader}"
LOCAL_CANDIDATE_WEB_PORT=""
LOCAL_CANDIDATE_WEB_RUNTIME="${runtime_root}"
term_calls=0
kill_calls=0
kill() {
  case "${1:-}" in
    -TERM) term_calls=$((term_calls + 1)) ;;
    -KILL) kill_calls=$((kill_calls + 1)) ;;
  esac
  builtin kill "$@"
}
# Preserve the production retry shape while keeping the ignored-TERM branch fast.
sleep() { /bin/sleep 0.001; }

cleanup_local_candidate_browser_runtime
remaining="$(
  ps -axo pgid=,stat= 2>/dev/null \
    | awk -v expected="${leader}" \
      '$1 == expected && $2 !~ /^Z/ { count++ } END { print count + 0 }'
)"
test "${term_calls}" -ge 1
test "${kill_calls}" -ge 1
test "${remaining}" -eq 0
test ! -e "${runtime_root}"
test -z "${LOCAL_CANDIDATE_WEB_PID}"
test -z "${LOCAL_CANDIDATE_WEB_PGID}"
test -z "${LOCAL_CANDIDATE_WEB_RUNTIME}"
leader=""
runtime_root=""
trap - EXIT
'''
    )

    result = _run_bash(script)

    assert result.returncode == 0, _failure_details(result)


def test_candidate_cleanup_accepts_refused_connect_during_time_wait() -> None:
    cleanup_function = _extract_shell_function(
        "cleanup_local_candidate_browser_runtime",
        "cleanup_initial_deploy_resources",
    )
    script = (
        "set -euo pipefail\n"
        f"PROJECT_ROOT={shlex.quote(str(RUNTIME_PROJECT_ROOT))}\n"
        f"PROBE_PY={shlex.quote(str(VENV_PYTHON))}\n"
        + cleanup_function
        + "\n"
        + r'''
state="$("${PROBE_PY}" -I -B - <<'PY'
import errno
import socket
import threading
import time

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
port = listener.getsockname()[1]
listener.listen(1)

def close_server_side_first() -> None:
    connection, _ = listener.accept()
    connection.shutdown(socket.SHUT_WR)
    connection.close()

thread = threading.Thread(target=close_server_side_first)
thread.start()
client = socket.create_connection(("127.0.0.1", port), timeout=1)
assert client.recv(1) == b""
client.close()
thread.join(timeout=1)
assert not thread.is_alive()
listener.close()
time.sleep(0.02)

bind_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    bind_probe.bind(("127.0.0.1", port))
except OSError as exc:
    bind_errno = exc.errno
else:
    bind_errno = 0
finally:
    bind_probe.close()

connect_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
connect_probe.settimeout(0.5)
connect_errno = connect_probe.connect_ex(("127.0.0.1", port))
connect_probe.close()

assert bind_errno == errno.EADDRINUSE
assert connect_errno == errno.ECONNREFUSED
print(port, bind_errno, connect_errno)
PY
)"
read -r port bind_errno connect_errno <<<"${state}"
test "${bind_errno}" -eq 48
test "${connect_errno}" -eq 61

LOCAL_CANDIDATE_WEB_PID=""
LOCAL_CANDIDATE_WEB_PGID=""
LOCAL_CANDIDATE_WEB_PORT="${port}"
LOCAL_CANDIDATE_WEB_RUNTIME=""
cleanup_local_candidate_browser_runtime
test -z "${LOCAL_CANDIDATE_WEB_PORT}"
'''
    )

    result = _run_bash(script)

    assert result.returncode == 0, _failure_details(result)


def test_initial_exit_trap_promotes_cleanup_failure_to_exit_one() -> None:
    cleanup_function = _extract_shell_function(
        "cleanup_initial_deploy_resources",
        "bind_rescue_rollback_candidate",
    )
    script = (
        "set -euo pipefail\n"
        + cleanup_function
        + r'''
cleanup_local_candidate_browser_runtime() { return 1; }
cleanup_deploy_verifier_bundle() { return 0; }
trap cleanup_initial_deploy_resources EXIT
true
'''
    )

    result = _run_bash(script, timeout=5)

    assert result.returncode == 1, _failure_details(result)
