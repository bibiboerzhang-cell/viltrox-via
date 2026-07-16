#!/usr/bin/env python3
"""Read-only post-sync audit for V-KPI official accounts and KOL pool state."""
from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

import argparse
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REMOTE_AUDIT = r'''
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def scalar(row, key, default=0):
    if not row:
        return default
    try:
        value = dict(row).get(key, default)
    except Exception:
        value = row[key] if key in row else default
    return value if value is not None else default

def rows(sql, params=()):
    try:
        return [dict(row) for row in get_conn().execute(sql, params).fetchall()]
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}", "sql": sql.strip()[:120]}]

def one(sql, params=()):
    try:
        row = get_conn().execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "sql": sql.strip()[:120]}

def count_table(table):
    result = one(f"SELECT COUNT(*) AS n FROM {table}")
    if result.get("error"):
        return {"exists": False, "count": 0, "error": result["error"]}
    return {"exists": True, "count": int(result.get("n") or 0)}

payload = {
    "checked_at": utcnow(),
    "official_channels": one("""
        SELECT
          COUNT(*) AS total_channels,
          SUM(CASE WHEN deleted_at IS NULL AND status='active' THEN 1 ELSE 0 END) AS active_channels,
          SUM(CASE WHEN deleted_at IS NULL AND status='active' AND last_sync_status='synced' THEN 1 ELSE 0 END) AS synced_channels,
          MAX(last_sync_at) AS latest_channel_sync_at
        FROM vkpi_employee_channels
    """),
    "platforms": rows("""
        SELECT
          c.platform,
          COUNT(*) AS channel_count,
          SUM(CASE WHEN c.last_sync_status='synced' THEN 1 ELSE 0 END) AS synced_count,
          COALESCE(SUM(m.followers), 0) AS followers,
          COALESCE(SUM(m.posts_count), 0) AS posts_count,
          COALESCE(SUM(m.total_views), 0) AS total_views,
          COALESCE(SUM(m.followers_delta), 0) AS followers_delta,
          COALESCE(SUM(m.posts_delta), 0) AS posts_delta,
          COALESCE(SUM(m.views_delta_24h), 0) AS views_delta
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
          SELECT mm.id
          FROM vkpi_channel_metrics mm
          WHERE mm.channel_id = c.id
          ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
          LIMIT 1
        )
        WHERE c.deleted_at IS NULL AND c.status='active'
        GROUP BY c.platform
        ORDER BY c.platform
    """),
    "today_metric_delta": one("""
        SELECT
          COUNT(*) AS metric_rows,
          COALESCE(SUM(followers_delta), 0) AS followers_delta,
          COALESCE(SUM(posts_delta), 0) AS posts_delta,
          COALESCE(SUM(views_delta_24h), 0) AS views_delta
        FROM vkpi_channel_metrics
        WHERE snapshot_date >= CURRENT_DATE
    """),
    "reddit": rows("""
        SELECT
          c.id,
          c.account_handle,
          c.last_sync_at,
          c.last_sync_status,
          COALESCE(m.followers, 0) AS followers,
          COALESCE(m.posts_count, 0) AS posts_count,
          COALESCE(m.total_views, 0) AS total_views,
          COALESCE(m.total_likes, 0) AS total_likes,
          COALESCE(m.total_comments, 0) AS total_comments,
          CASE WHEN COALESCE(m.total_views, 0) = 0 THEN 'views_unavailable_or_zero' ELSE 'views_present' END AS views_state
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
          SELECT mm.id
          FROM vkpi_channel_metrics mm
          WHERE mm.channel_id = c.id
          ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
          LIMIT 1
        )
        WHERE c.deleted_at IS NULL AND c.platform='reddit'
        ORDER BY c.id
    """),
    "kol_pool": one("""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN source_type='legacy_excel_p2d' THEN 1 ELSE 0 END) AS legacy_excel_p2d,
          SUM(CASE WHEN source_type='legacy_excel_p2d' AND updated_at >= CURRENT_DATE THEN 1 ELSE 0 END) AS legacy_updated_today,
          SUM(CASE WHEN raw_platform_data IS NOT NULL AND raw_platform_data NOT IN ('', '{}', '[]') THEN 1 ELSE 0 END) AS with_raw_platform_data
        FROM vkpi_kol_pool
    """),
    "kol_pool_by_platform": rows("""
        SELECT platform, COUNT(*) AS count
        FROM vkpi_kol_pool
        GROUP BY platform
        ORDER BY count DESC, platform ASC
        LIMIT 20
    """),
    "brand_signal": count_table("vkpi_brand_signal"),
    "brand_signal_summary": one("""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN is_new THEN 1 ELSE 0 END) AS new_count,
          SUM(CASE WHEN brand_role='competitor' THEN 1 ELSE 0 END) AS competitor_count,
          SUM(CASE WHEN analysis_scope='current_year' THEN 1 ELSE 0 END) AS current_year_count
        FROM vkpi_brand_signal
    """),
    "competitor_relation": count_table("vkpi_competitor_relation"),
    "competitor_relation_summary": one("""
        SELECT
          COUNT(*) AS total,
          COUNT(DISTINCT kol_pool_id) AS kol_count,
          SUM(CASE WHEN risk_tier='avoid' THEN 1 ELSE 0 END) AS avoid_count,
          SUM(CASE WHEN risk_tier='caution' THEN 1 ELSE 0 END) AS caution_count,
          SUM(CASE WHEN risk_tier='safe' THEN 1 ELSE 0 END) AS safe_count,
          SUM(CASE WHEN risk_tier='opportunity' THEN 1 ELSE 0 END) AS opportunity_count,
          MAX(computed_at) AS latest_computed_at
        FROM vkpi_competitor_relation
    """),
    "media_cache_assets": rows("""
        SELECT storage_backend, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM vkpi_media_cache_assets
        GROUP BY storage_backend
        ORDER BY storage_backend
    """),
}

official = payload.get("official_channels") or {}
kol_pool = payload.get("kol_pool") or {}
payload["acceptance"] = {
    "official_18_active": int(official.get("active_channels") or 0) >= 18,
    "official_all_synced": int(official.get("synced_channels") or 0) >= int(official.get("active_channels") or 0) >= 18,
    "legacy_1012_present": int(kol_pool.get("legacy_excel_p2d") or 0) >= 1012,
    "reddit_record_present": any(not row.get("error") for row in payload.get("reddit") or []),
    "brand_signal_table_ready": bool(payload.get("brand_signal", {}).get("exists")),
    "competitor_relation_table_ready": bool(payload.get("competitor_relation", {}).get("exists")),
}
print(json.dumps(payload, ensure_ascii=False, default=str, indent=2, sort_keys=True))

try:
    close_db_runtime()
except Exception:
    pass
'''


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, timeout=timeout)


def parse_json_blob(output: str) -> dict[str, Any]:
    text = output.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object in output")
    payload = json.loads(text[start:end + 1])
    return payload if isinstance(payload, dict) else {"value": payload}


def remote_service_state(target: str, service: str) -> str:
    result = run(["ssh", target, f"systemctl is-active {shlex.quote(service)} 2>/dev/null || true"], timeout=20)
    return result.stdout.strip() or "unknown"


def local_service_state(service: str) -> str:
    result = run(["bash", "-lc", f"systemctl is-active {shlex.quote(service)} 2>/dev/null || true"], timeout=20)
    return result.stdout.strip() or "unknown"


def audit_remote(target: str, root: str) -> dict[str, Any]:
    command = f"cd {shlex.quote(root)} && .venv/bin/python - <<'PY'\n{REMOTE_AUDIT}\nPY"
    result = run(["ssh", target, command], timeout=90)
    if result.returncode != 0:
        return {
            "checked_at": utcnow(),
            "target": target,
            "remote_root": root,
            "error": result.stderr.strip() or result.stdout.strip() or f"ssh exited {result.returncode}",
        }
    payload = parse_json_blob(result.stdout)
    payload["target"] = target
    payload["remote_root"] = root
    return payload


def audit_local(root: str) -> dict[str, Any]:
    command = f"cd {shlex.quote(root)} && .venv/bin/python - <<'PY'\n{REMOTE_AUDIT}\nPY"
    result = run(["bash", "-lc", command], timeout=90)
    if result.returncode != 0:
        return {
            "checked_at": utcnow(),
            "target": "local",
            "remote_root": root,
            "error": result.stderr.strip() or result.stdout.strip() or f"local audit exited {result.returncode}",
        }
    payload = parse_json_blob(result.stdout)
    payload["target"] = "local"
    payload["remote_root"] = root
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only post-sync V-KPI data audit")
    parser.add_argument("--remote", default="viltrox", help="SSH target")
    parser.add_argument("--remote-root", default="/opt/viltrox-2.0", help="Remote app root")
    parser.add_argument("--service", default="vkpi-sync-daily.service", help="Sync systemd service name")
    parser.add_argument("--allow-during-sync", action="store_true", help="Run DB audit even if sync service is active/activating")
    parser.add_argument("--local", action="store_true", help="Audit the current machine/root directly instead of SSH")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service_state = local_service_state(args.service) if args.local else remote_service_state(args.remote, args.service)
    if service_state in {"active", "activating"} and not args.allow_during_sync:
        stdout_out(json.dumps({
            "checked_at": utcnow(),
            "skipped": True,
            "reason": f"{args.service} is {service_state}; rerun after completion or pass --allow-during-sync for read-only inspection",
            "service": args.service,
            "service_state": service_state,
            "target": "local" if args.local else args.remote,
            "remote_root": args.remote_root,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    payload = audit_local(args.remote_root) if args.local else audit_remote(args.remote, args.remote_root)
    payload["service"] = args.service
    payload["service_state"] = service_state
    stdout_out(json.dumps(payload, ensure_ascii=False, default=str, indent=2, sort_keys=True))
    if payload.get("error"):
        return 2
    acceptance = payload.get("acceptance") if isinstance(payload.get("acceptance"), dict) else {}
    required = ["official_18_active", "legacy_1012_present"]
    return 0 if all(bool(acceptance.get(key)) for key in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
