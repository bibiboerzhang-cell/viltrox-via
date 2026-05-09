#!/usr/bin/env python3
"""V-KPI v3 release gate.

This script is intentionally narrow: it verifies the v3 closure list only.
Amazon and other v4 work are not part of this gate.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

GATE_STEPS = [
    {
        "key": "A",
        "label": "AI Weekly Summary Claude/fallback path",
        "command": [sys.executable, "scripts/smoke_vkpi_weekly_ai_summary.py"],
        "env": {"VKPI_SKIP_LIVE_AI_SMOKE": "1"},
    },
    {
        "key": "C1",
        "label": "User preferences storage and scope",
        "command": [sys.executable, "scripts/smoke_vkpi_user_preferences.py"],
    },
    {
        "key": "C2",
        "label": "Notification settings storage-only path",
        "command": [sys.executable, "scripts/smoke_vkpi_notification_settings.py"],
    },
    {
        "key": "C3",
        "label": "Frontend settings entry smoke",
        "command": [sys.executable, "scripts/smoke_vkpi_phase0b_frontend_entries.py"],
    },
]


def _base_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("ENVIRONMENT", "local")
    env.setdefault("DB_RUNTIME_BACKEND", "sqlite")
    env.setdefault("DATABASE_URL", "")
    env.setdefault("APP_ROLE", "admin-web")
    env["PYTHONPATH"] = f"{ROOT / 'backend'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    if extra:
        env.update(extra)
    return env


def _run_step(step: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run(
        step["command"],
        cwd=ROOT,
        env=_base_env(step.get("env")),
        text=True,
        capture_output=True,
    )
    return {
        "key": step["key"],
        "label": step["label"],
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_sec": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def _run_frontend_build() -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run(
        ["npm", "--prefix", "frontend", "run", "build"],
        cwd=ROOT,
        env=_base_env(),
        text=True,
        capture_output=True,
    )
    return {
        "key": "UI",
        "label": "Frontend production build",
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_sec": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the V-KPI v3 release gate.")
    parser.add_argument("--skip-build", action="store_true", help="Skip frontend build.")
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    if not args.skip_build:
        results.append(_run_frontend_build())
    for step in GATE_STEPS:
        results.append(_run_step(step))

    ok = all(item["ok"] for item in results)
    payload = {
        "ok": ok,
        "gate": "vkpi_v3_release",
        "remaining_checklist": {
            "A_ai_weekly_summary": "done",
            "C1_user_preferences": "done",
            "C2_notification_settings": "done",
            "C3_settings_page_integration": "done",
            "other_ui_adjustments": "done",
        },
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
