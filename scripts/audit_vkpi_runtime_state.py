#!/usr/bin/env python3
"""Audit V-KPI runtime version and data-state hygiene.

Read-only P3.10D checks:
- backend health git sha vs current repo sha
- DB and table state related to Daily Top100 and account sync
- handle/status inconsistencies that can make the UI look contradictory
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.db.connection import get_conn  # noqa: E402


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return ""


def _health(client_sha: str) -> dict[str, Any]:
    url = f"http://127.0.0.1:8102/health?client_build={client_sha}" if client_sha else "http://127.0.0.1:8102/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int | str | None:
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row[0]
    except Exception as exc:
        return f"ERROR: {exc}"


def _rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        return [{"error": str(exc)}]


def _truthy_count(rows: list[dict[str, Any]], column: str) -> int | str:
    if rows and "error" in rows[0]:
        return f"ERROR: {rows[0]['error']}"
    total = 0
    for row in rows:
        value = row.get(column)
        if isinstance(value, bool):
            total += 1 if value else 0
        elif value is None:
            total += 0
        else:
            try:
                total += 1 if int(value) != 0 else 0
            except (TypeError, ValueError):
                total += 1 if str(value).strip().lower() in {"true", "yes", "active"} else 0
    return total


def audit() -> dict[str, Any]:
    repo_sha = _git_sha()
    health = _health(repo_sha)
    conn = get_conn()

    platform_rows = _rows(
        conn,
        """
        SELECT platform, crawl_enabled, daily_account_limit, posts_per_account,
               monthly_budget_usd, last_test_status
        FROM vkpi_platform_crawl_settings
        ORDER BY platform
        """,
    )
    account_rows = _rows(
        conn,
        """
        SELECT id, platform, handle, display_name, sync_status,
               last_crawled_at, last_successful_at
        FROM vkpi_industry_accounts
        ORDER BY id
        """,
    )
    staff_active_rows = _rows(conn, "SELECT active FROM staff")
    platform_status = {str(row.get("platform")): row for row in platform_rows if "error" not in row}
    status_conflicts: list[dict[str, Any]] = []
    for account in account_rows:
        if "error" in account:
            continue
        setting = platform_status.get(str(account.get("platform")))
        if not setting:
            status_conflicts.append({"type": "missing_platform_setting", "account": account})
            continue
        account_status = str(account.get("sync_status") or "")
        platform_test = str(setting.get("last_test_status") or "")
        if account_status in {"synced", "done"} and platform_test in {"not_configured", "failed", ""}:
            status_conflicts.append(
                {
                    "type": "account_synced_but_platform_not_ready",
                    "account": account,
                    "platform_setting": setting,
                }
            )
        if int(setting.get("crawl_enabled") or 0) == 0 and account_status in {"synced", "done"}:
            status_conflicts.append(
                {
                    "type": "account_synced_but_platform_crawl_off",
                    "account": account,
                    "platform_setting": setting,
                }
            )

    backend_sha = str((health.get("build") or {}).get("git_sha") or health.get("git_sha") or "")
    result = {
        "repo": {
            "cwd": str(ROOT),
            "git_sha": repo_sha,
            "git_short_sha": repo_sha[:7] if repo_sha else "",
            "dirty_count": int(subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).count("\n")),
        },
        "health": health,
        "version": {
            "server_git_sha": backend_sha,
            "server_matches_repo": bool(backend_sha and repo_sha and backend_sha.startswith(repo_sha[:12])),
            "client_matches_server": bool(health.get("client_matches_server")) if isinstance(health, dict) else False,
            "app_git_sha_env_present": bool(os.environ.get("APP_GIT_SHA")),
        },
        "database": {
            "current_database": _scalar(conn, "SELECT current_database()"),
            "staff_total": _scalar(conn, "SELECT COUNT(*) FROM staff"),
            "staff_active": _truthy_count(staff_active_rows, "active"),
            "monitored_products": _scalar(conn, "SELECT COUNT(*) FROM vkpi_monitored_products"),
            "outreach_suggestions_total": _scalar(conn, "SELECT COUNT(*) FROM vkpi_outreach_suggestions"),
            "outreach_suggestions_new": _scalar(conn, "SELECT COUNT(*) FROM vkpi_outreach_suggestions WHERE status='new'"),
            "daily_digest_runs": _scalar(conn, "SELECT COUNT(*) FROM vkpi_staff_outreach_digests"),
            "daily_digest_items": _scalar(conn, "SELECT COUNT(*) FROM vkpi_staff_outreach_digest_items"),
            "industry_accounts": _scalar(conn, "SELECT COUNT(*) FROM vkpi_industry_accounts"),
            "empty_industry_handles": _scalar(conn, "SELECT COUNT(*) FROM vkpi_industry_accounts WHERE COALESCE(handle, '')=''"),
        },
        "platform_crawl_settings": platform_rows,
        "industry_accounts": account_rows,
        "status_conflicts": status_conflicts,
        "warnings": [],
    }

    warnings = result["warnings"]
    if not result["version"]["server_matches_repo"]:
        warnings.append("backend health git_sha does not match current repo HEAD")
    if result["database"]["monitored_products"] == 0:
        warnings.append("vkpi_monitored_products is empty; Daily Top100 upstream monitor will not generate new product candidates")
    if result["database"]["empty_industry_handles"]:
        warnings.append("some industry accounts have empty handle")
    if status_conflicts:
        warnings.append("account sync status conflicts with platform crawl settings")
    return result


def main() -> int:
    result = audit()
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"cwd={result['repo']['cwd']}")
    print(f"git_sha={result['repo']['git_short_sha']}")
    print(f"dirty_count={result['repo']['dirty_count']}")
    print(f"database={result['database']['current_database']}")
    print(f"server_matches_repo={result['version']['server_matches_repo']}")
    print(f"client_matches_server={result['version']['client_matches_server']}")
    print(f"staff_active={result['database']['staff_active']}/{result['database']['staff_total']}")
    print(f"monitored_products={result['database']['monitored_products']}")
    print(f"outreach_suggestions_new={result['database']['outreach_suggestions_new']}")
    print(f"status_conflicts={len(result['status_conflicts'])}")
    if result["warnings"]:
        print("warnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
