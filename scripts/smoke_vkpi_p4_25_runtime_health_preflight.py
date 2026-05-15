"""P4 Step 25: runtime health preflight for V-KPI browser/dynamic QA.

This smoke is intentionally read-only. It verifies the local backend exposes a
versioned /health payload and that the frontend build hash matches the backend
hash before browser QA or mutation QA starts.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_URL = "http://127.0.0.1:8102"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _request_json(path: str) -> dict[str, Any]:
    req = urllib.request.Request(f"{BASE_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode("utf-8") or "{}"
            return {"status_code": resp.status, "json": json.loads(payload)}
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"GET {path} returned HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:500]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"GET {path} failed: {exc}") from exc


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True).strip()


def _assert(condition: bool, message: str, context: Any = None) -> None:
    if not condition:
        suffix = f": {context}" if context is not None else ""
        raise AssertionError(f"{message}{suffix}")


def main() -> None:
    resp = _request_json("/health")
    _assert(resp["status_code"] == 200, "/health should return HTTP 200", resp)
    health = resp["json"]
    build = health.get("build") or {}

    repo_head = _git(["rev-parse", "HEAD"])
    repo_branch = _git(["branch", "--show-current"])

    _assert(health.get("status") == "ok", "/health status should be ok", health)
    _assert(health.get("service") == "admin-web", "/health service should be admin-web", health)
    _assert(build.get("git_sha"), "build.git_sha should be present", build)
    _assert(build.get("git_short_sha"), "build.git_short_sha should be present", build)
    _assert(build.get("git_branch"), "build.git_branch should be present", build)
    _assert(build.get("build_time"), "build.build_time should be present", build)
    _assert(build.get("client_build"), "build.client_build should be present", build)
    _assert(build.get("client_build_source"), "build.client_build_source should be present", build)
    _assert(build.get("client_matches_server") is True, "frontend bundle should match backend git sha", build)
    _assert(build.get("git_sha") == repo_head, "running backend git_sha should match current repository HEAD", {"health": build.get("git_sha"), "repo": repo_head})
    _assert(build.get("git_branch") == repo_branch, "running backend git_branch should match current repository branch", {"health": build.get("git_branch"), "repo": repo_branch})

    try:
        datetime.fromisoformat(str(build["build_time"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssertionError(f"build_time should be ISO timestamp: {build.get('build_time')}") from exc

    print(
        json.dumps(
            {
                "ok": True,
                "marker": "VKPI_P4_RUNTIME_HEALTH_PREFLIGHT_OK",
                "git_sha": build.get("git_short_sha"),
                "branch": build.get("git_branch"),
                "client_build_source": build.get("client_build_source"),
                "client_matches_server": build.get("client_matches_server"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
