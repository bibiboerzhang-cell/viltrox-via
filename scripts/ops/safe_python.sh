#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)
REAL_PYTHON=${VKPI_SAFE_PYTHON_REAL:-${PROJECT_ROOT}/.venv/bin/python}
ROUTER=${SCRIPT_DIR}/safe_python_router.py

case "${REAL_PYTHON}" in
  /*) ;;
  *)
    echo "safe Python requires an absolute physical interpreter path" >&2
    exit 126
    ;;
esac
if [ ! -x "${REAL_PYTHON}" ] || [ "${REAL_PYTHON}" = "$0" ]; then
  echo "safe Python physical interpreter is unavailable" >&2
  exit 126
fi

# Keep the venv launcher path (it supplies sys.prefix), but resolve it once for
# trust/recursion checks.  The resolved executable must be an immutable regular
# file outside the candidate tree; candidate packages are added later under -S.
RESOLVED_REAL_PYTHON=$(/bin/realpath "${REAL_PYTHON}") || {
  echo "safe Python physical interpreter cannot be resolved" >&2
  exit 126
}
RESOLVED_SELF=$(/bin/realpath "$0") || {
  echo "safe Python wrapper cannot be resolved" >&2
  exit 126
}
RESOLVED_ROUTER=$(/bin/realpath "${ROUTER}") || {
  echo "safe Python router cannot be resolved" >&2
  exit 126
}
if [ "${RESOLVED_SELF}" != "${PROJECT_ROOT}/scripts/ops/safe_python.sh" ] \
  || [ "${RESOLVED_ROUTER}" != "${ROUTER}" ] \
  || [ ! -f "${ROUTER}" ] \
  || [ -L "${ROUTER}" ]; then
  echo "safe Python wrapper/router path is untrusted" >&2
  exit 126
fi
if ROUTER_META=$(/usr/bin/stat -f '%u:%Lp:%l' "${ROUTER}" 2>/dev/null); then
  :
elif ROUTER_META=$(/usr/bin/stat -c '%u:%a:%h' "${ROUTER}" 2>/dev/null); then
  :
else
  echo "safe Python router metadata is unavailable" >&2
  exit 126
fi
ROUTER_OWNER=${ROUTER_META%%:*}
ROUTER_REST=${ROUTER_META#*:}
ROUTER_MODE=${ROUTER_REST%%:*}
ROUTER_NLINK=${ROUTER_REST##*:}
CURRENT_UID=$(/usr/bin/id -u)
case "${ROUTER_OWNER}" in
  0|"${CURRENT_UID}") ;;
  *)
    echo "safe Python router has an untrusted owner" >&2
    exit 126
    ;;
esac
if [ "${ROUTER_NLINK}" != "1" ] || [ $((0${ROUTER_MODE} & 022)) -ne 0 ]; then
  echo "safe Python router has an untrusted mode or link count" >&2
  exit 126
fi
case "${RESOLVED_REAL_PYTHON}" in
  "${PROJECT_ROOT}"|"${PROJECT_ROOT}"/*)
    echo "safe Python physical interpreter resolves inside the candidate tree" >&2
    exit 126
    ;;
esac
if [ "${RESOLVED_REAL_PYTHON}" = "${RESOLVED_SELF}" ] \
  || [ ! -f "${RESOLVED_REAL_PYTHON}" ] \
  || [ -L "${RESOLVED_REAL_PYTHON}" ]; then
  echo "safe Python physical interpreter is not a regular executable" >&2
  exit 126
fi
if PHYSICAL_META=$(/usr/bin/stat -f '%u %Lp' "${RESOLVED_REAL_PYTHON}" 2>/dev/null); then
  :
elif PHYSICAL_META=$(/usr/bin/stat -c '%u %a' "${RESOLVED_REAL_PYTHON}" 2>/dev/null); then
  :
else
  echo "safe Python physical interpreter metadata is unavailable" >&2
  exit 126
fi
PHYSICAL_OWNER=${PHYSICAL_META%% *}
PHYSICAL_MODE=${PHYSICAL_META#* }
case "${PHYSICAL_OWNER}" in
  0|"${CURRENT_UID}") ;;
  *)
    echo "safe Python physical interpreter has an untrusted owner" >&2
    exit 126
    ;;
esac
if [ $((0${PHYSICAL_MODE} & 022)) -ne 0 ]; then
  echo "safe Python physical interpreter is group/world writable" >&2
  exit 126
fi

exec "${REAL_PYTHON}" -I -S -B "${ROUTER}" "$@"
