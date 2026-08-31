#!/usr/bin/env python3
"""Fail-closed daily-sync dead-man and systemd failure notifier."""
from __future__ import annotations
import sys as _runtime_sys
from pathlib import Path as _RuntimePath

_runtime_sys.dont_write_bytecode = True
ROOT = _RuntimePath(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in _runtime_sys.path:
    _runtime_sys.path.insert(0, str(BACKEND))

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core import stateless_alert


DEFAULT_AUDIT = ROOT / "scripts" / "ops" / "audit_vkpi_post_sync_state.py"
DEFAULT_BASELINE = ROOT / "scripts" / "ops" / "baselines" / "vkpi-post-sync-baseline.json"


def _write(payload: dict[str, Any]) -> None:
    _runtime_sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True) + "\n")
    _runtime_sys.stdout.flush()


def _today_log() -> str:
    return f"/var/log/vkpi/sync_daily_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"


def _parse_json_blob(output: str) -> dict[str, Any]:
    text = str(output or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("audit_output_missing_json")
    payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("audit_output_not_object")
    return payload


def _safe_audit_summary(payload: dict[str, Any], returncode: int) -> dict[str, Any]:
    sync_log = payload.get("sync_log") if isinstance(payload.get("sync_log"), dict) else {}
    return {
        "returncode": int(returncode),
        "acceptance_ready": payload.get("acceptance_ready") is True,
        "failed_checks": [str(item)[:80] for item in (payload.get("failed_checks") or [])[:30]],
        "batch_id": str(sync_log.get("batch_id") or "")[:120],
        "service_state": str(payload.get("service_state") or "unknown")[:40],
        "configuration_error": bool(payload.get("configuration_error")),
    }


def _run_audit(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    command = [
        _runtime_sys.executable,
        "-B",
        str(args.audit_script),
        "--local",
        "--remote-root",
        str(args.remote_root),
        "--service",
        str(args.service),
        "--baseline-file",
        str(args.baseline_file),
        "--sync-log-path",
        str(args.sync_log_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=max(30, min(600, int(args.audit_timeout_seconds))),
        )
    except subprocess.TimeoutExpired:
        return {
            "acceptance_ready": False,
            "failed_checks": ["audit_timeout"],
            "service_state": "unknown",
        }, 124
    try:
        return _parse_json_blob(result.stdout), int(result.returncode)
    except (ValueError, json.JSONDecodeError):
        return {
            "acceptance_ready": False,
            "failed_checks": ["invalid_audit_output"],
            "service_state": "unknown",
        }, int(result.returncode or 2)


def _notify(*, key: str, title: str, body: str) -> dict[str, Any]:
    return stateless_alert.notify_stateless(
        key=key,
        title=title,
        body=body,
        severity="danger",
        rule_key="ops.daily_sync_watchdog",
    )


def run_unit_failure(unit: str) -> int:
    safe_unit = str(unit or "unknown")[:160]
    delivery = _notify(
        key=f"systemd-failure:{safe_unit}",
        title="V-KPI scheduled sync systemd failure",
        body=f"unit={safe_unit}; inspect journalctl -u {safe_unit}",
    )
    _write({"event": "vkpi_sync_systemd_failure_alert", "unit": safe_unit, "delivery": delivery})
    if delivery.get("sent"):
        return 0
    return 2 if delivery.get("reason") in {"not_configured", "silenced"} else 3


def run_deadman(args: argparse.Namespace) -> int:
    audit, audit_code = _run_audit(args)
    summary = _safe_audit_summary(audit, audit_code)
    channel = dict(stateless_alert.outbound_status())
    channel["deadman_silenced"] = "daily-sync-deadman" in stateless_alert.silenced_keys()
    if audit_code == 0 and audit.get("acceptance_ready") is True:
        result = {
            "event": "vkpi_sync_deadman_ok",
            "audit": summary,
            "alert_channel": channel,
        }
        if not channel.get("configured") or channel["deadman_silenced"]:
            reason = "alert_channel_silenced" if channel["deadman_silenced"] else "alert_channel_not_configured"
            result.update({"status": "failed", "reason": reason})
            _write(result)
            return 2
        result["status"] = "ok"
        _write(result)
        return 0

    body = (
        f"audit_returncode={summary['returncode']}; "
        f"service_state={summary['service_state']}; "
        f"batch_id={summary['batch_id'] or 'missing'}; "
        f"failed_checks={','.join(summary['failed_checks']) or 'unknown'}"
    )
    delivery = _notify(
        key="daily-sync-deadman",
        title="V-KPI daily sync missed strict post-sync acceptance",
        body=body,
    )
    _write({
        "event": "vkpi_sync_deadman_failed",
        "status": "failed",
        "audit": summary,
        "alert_channel": channel,
        "delivery": delivery,
    })
    return 1 if delivery.get("sent") else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V-KPI scheduled-sync watchdog")
    subparsers = parser.add_subparsers(dest="command", required=True)
    failure = subparsers.add_parser("unit-failure", help="Notify for a failed systemd unit")
    failure.add_argument("--unit", required=True)
    deadman = subparsers.add_parser("deadman", help="Require today's strict post-sync acceptance")
    deadman.add_argument("--audit-script", type=Path, default=DEFAULT_AUDIT)
    deadman.add_argument("--baseline-file", type=Path, default=DEFAULT_BASELINE)
    deadman.add_argument("--remote-root", default="/opt/viltrox-2.0")
    deadman.add_argument("--service", default="vkpi-sync-daily.service")
    deadman.add_argument("--sync-log-path", default=_today_log())
    deadman.add_argument("--audit-timeout-seconds", type=int, default=180)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "unit-failure":
        return run_unit_failure(args.unit)
    return run_deadman(args)


if __name__ == "__main__":
    raise SystemExit(main())
