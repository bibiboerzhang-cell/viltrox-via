#!/usr/bin/env python3
"""Check whether V-KPI media cache can safely write to Cloudflare R2."""
from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_ENV = (
    "VKPI_MEDIA_CACHE_STORAGE",
    "R2_ENDPOINT",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
)
OPTIONAL_ENV = (
    "VKPI_MEDIA_CACHE_R2_PREFIX",
    "VKPI_MEDIA_CACHE_R2_PUBLIC_BASE_URL",
    "R2_PUBLIC_BASE_URL",
)
R2_STORAGE_MODES = {"r2", "hybrid", "cloud"}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:3]}...{text[-3:]}"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("'").strip('"')
        values[key] = value
    return values


def env_value(env: dict[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def collect(env: dict[str, str], *, source: str, env_file: str = "") -> dict[str, object]:
    storage_mode = env_value(env, "VKPI_MEDIA_CACHE_STORAGE").lower() or "local"
    missing = [key for key in REQUIRED_ENV if not env_value(env, key)]
    r2_enabled = storage_mode in R2_STORAGE_MODES and not missing
    endpoint = env_value(env, "R2_ENDPOINT")
    public_base = env_value(env, "VKPI_MEDIA_CACHE_R2_PUBLIC_BASE_URL") or env_value(env, "R2_PUBLIC_BASE_URL")
    parsed_endpoint = urlparse(endpoint)
    parsed_public = urlparse(public_base)
    return {
        "checked_at": utcnow(),
        "source": source,
        "env_file": env_file,
        "storage_mode": storage_mode,
        "r2_enabled": r2_enabled,
        "ready_for_new_uploads": r2_enabled,
        "public_playback_url_configured": bool(public_base),
        "required": {key: bool(env_value(env, key)) for key in REQUIRED_ENV},
        "optional": {key: bool(env_value(env, key)) for key in OPTIONAL_ENV},
        "missing_required": missing,
        "endpoint_host": parsed_endpoint.netloc or endpoint,
        "public_base_host": parsed_public.netloc or public_base,
        "bucket": mask(env_value(env, "R2_BUCKET_NAME")),
        "prefix": env_value(env, "VKPI_MEDIA_CACHE_R2_PREFIX") or "vkpi/media-cache",
    }


REMOTE_SNIPPET = r'''
import json, os
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone
REQUIRED_ENV = ("VKPI_MEDIA_CACHE_STORAGE","R2_ENDPOINT","R2_ACCESS_KEY_ID","R2_SECRET_ACCESS_KEY","R2_BUCKET_NAME")
OPTIONAL_ENV = ("VKPI_MEDIA_CACHE_R2_PREFIX","VKPI_MEDIA_CACHE_R2_PUBLIC_BASE_URL","R2_PUBLIC_BASE_URL")
R2_STORAGE_MODES = {"r2", "hybrid", "cloud"}
def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def mask(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:3]}...{text[-3:]}"
def load_env_file(path):
    values = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip("'").strip('"')
    return values
env_file = Path(".env")
env = dict(os.environ)
env.update(load_env_file(env_file))
storage_mode = str(env.get("VKPI_MEDIA_CACHE_STORAGE") or "local").strip().lower()
missing = [key for key in REQUIRED_ENV if not str(env.get(key) or "").strip()]
endpoint = str(env.get("R2_ENDPOINT") or "").strip()
public_base = str(env.get("VKPI_MEDIA_CACHE_R2_PUBLIC_BASE_URL") or env.get("R2_PUBLIC_BASE_URL") or "").strip()
parsed_endpoint = urlparse(endpoint)
parsed_public = urlparse(public_base)
print(json.dumps({
    "checked_at": utcnow(),
    "source": "remote",
    "env_file": str(env_file),
    "storage_mode": storage_mode,
    "r2_enabled": storage_mode in R2_STORAGE_MODES and not missing,
    "ready_for_new_uploads": storage_mode in R2_STORAGE_MODES and not missing,
    "public_playback_url_configured": bool(public_base),
    "required": {key: bool(str(env.get(key) or "").strip()) for key in REQUIRED_ENV},
    "optional": {key: bool(str(env.get(key) or "").strip()) for key in OPTIONAL_ENV},
    "missing_required": missing,
    "endpoint_host": parsed_endpoint.netloc or endpoint,
    "public_base_host": parsed_public.netloc or public_base,
    "bucket": mask(str(env.get("R2_BUCKET_NAME") or "")),
    "prefix": str(env.get("VKPI_MEDIA_CACHE_R2_PREFIX") or "vkpi/media-cache").strip(),
}, ensure_ascii=False))
'''


def check_remote(target: str, root: str) -> dict[str, object]:
    command = f"cd {shlex.quote(root)} && python3 - <<'PY'\n{REMOTE_SNIPPET}\nPY"
    completed = subprocess.run(["ssh", target, command], check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        return {
            "checked_at": utcnow(),
            "source": "remote",
            "target": target,
            "remote_root": root,
            "error": completed.stderr.strip() or completed.stdout.strip() or f"ssh exited {completed.returncode}",
        }
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {
            "checked_at": utcnow(),
            "source": "remote",
            "target": target,
            "remote_root": root,
            "error": f"invalid remote json: {exc}",
        }
    payload["target"] = target
    payload["remote_root"] = root
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check V-KPI R2 media-cache readiness without uploading files")
    parser.add_argument("--env-file", default=".env", help="Local env file to inspect")
    parser.add_argument("--remote", default="", help="Optional SSH target, e.g. viltrox")
    parser.add_argument("--remote-root", default="/opt/viltrox-2.0", help="Remote app root for --remote")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    local_env_file = Path(args.env_file)
    env = dict(os.environ)
    env.update(load_env_file(local_env_file))
    result: dict[str, object] = {
        "local": collect(env, source="local", env_file=str(local_env_file)),
    }
    if args.remote:
        result["remote"] = check_remote(args.remote, args.remote_root)
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    remote = result.get("remote") if isinstance(result.get("remote"), dict) else {}
    local_ready = bool(result["local"].get("ready_for_new_uploads"))  # type: ignore[index, union-attr]
    remote_ready = bool(remote.get("ready_for_new_uploads")) if remote else True
    return 0 if local_ready and remote_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
