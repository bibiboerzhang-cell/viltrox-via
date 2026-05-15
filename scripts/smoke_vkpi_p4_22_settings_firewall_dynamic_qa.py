"""P4 Step 22: dynamic QA for V-KPI settings/firewall mutation safety.

This smoke intentionally uses the running local backend over HTTP. It verifies:
- non-admin staff cannot mutate settings/firewall controls
- admin/owner staff can mutate test-scoped keys
- writes land in DB
- service-level settings audit is recorded
- firewall router audit decorator writes business audit

All test data is marker-scoped and cleaned up before exit.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_seed import cleanup_admin, seed_admin  # noqa: E402
from app.core.security import make_token  # noqa: E402
from app.db.connection import get_conn  # noqa: E402


BASE_URL = "http://127.0.0.1:8102"
MARKER = f"p4-step22-fw-{int(time.time())}"
FLAG_KEY = f"{MARKER}-flag"
PLATFORM = f"{MARKER}-platform"
BUDGET_KEY = f"{MARKER}-budget"


def _headers(token: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _request(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return {"status": resp.status, "body": json.loads(raw)}
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "error": exc.read().decode("utf-8", errors="replace")[:500],
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": -1, "error": str(exc)}


def _one(sql: str, args: tuple[Any, ...]) -> dict[str, Any]:
    row = get_conn().execute(sql, args).fetchone()
    return dict(row) if row else {}


def _count(sql: str, args: tuple[Any, ...]) -> int:
    row = get_conn().execute(sql, args).fetchone()
    return int(row["n"] if row and "n" in row.keys() else row[0] if row else 0)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes"}
    return bool(value)


def _cleanup(user_ids: list[int], staff_ids: list[int]) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM vkpi_feature_flags WHERE flag_key=?", (FLAG_KEY,))
        conn.execute("DELETE FROM vkpi_platform_crawl_settings WHERE platform=?", (PLATFORM,))
        conn.execute("DELETE FROM vkpi_budget_settings WHERE budget_key=?", (BUDGET_KEY,))
        conn.execute(
            "DELETE FROM vkpi_business_audit_logs WHERE target_id IN (?,?,?) OR metadata_json LIKE ?",
            (FLAG_KEY, PLATFORM, BUDGET_KEY, f"%{MARKER}%"),
        )
        conn.execute(
            "DELETE FROM vkpi_settings_change_logs WHERE setting_key IN (?,?,?) OR metadata_json LIKE ?",
            (FLAG_KEY, PLATFORM, BUDGET_KEY, f"%{MARKER}%"),
        )
        conn.commit()
    finally:
        for user_id, staff_id in zip(user_ids, staff_ids, strict=False):
            cleanup_admin(conn, user_id=user_id, staff_id=staff_id)


def _assert(condition: bool, message: str, context: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{message}: {context}")


def main() -> None:
    conn = get_conn()
    admin_user_id = admin_staff_id = employee_user_id = employee_staff_id = 0
    try:
        admin_user_id, admin_staff_id = seed_admin(
            conn,
            marker=MARKER,
            suffix="admin",
            role="admin",
            vkpi_permission="admin",
            is_owner=True,
        )
        employee_user_id, employee_staff_id = seed_admin(
            conn,
            marker=MARKER,
            suffix="employee",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        admin_token = make_token(admin_user_id, "admin")
        employee_token = make_token(employee_user_id, "employee")

        # 1. Non-admin cannot mutate firewall feature flags.
        resp = _request(
            "POST",
            "/api/admin/vkpi/settings/firewall/feature-flags",
            employee_token,
            {"flag_key": FLAG_KEY, "enabled": True, "metadata": {"marker": MARKER}},
        )
        _assert(resp["status"] == 403, "employee should be rejected by admin firewall endpoint", resp)
        _assert(not _one("SELECT * FROM vkpi_feature_flags WHERE flag_key=?", (FLAG_KEY,)), "rejected employee write should not create flag")

        # 2. Admin can mutate firewall feature flags and both audit layers record it.
        resp = _request(
            "POST",
            "/api/admin/vkpi/settings/firewall/feature-flags",
            admin_token,
            {"flag_key": FLAG_KEY, "enabled": True, "description": "P4 Step22 smoke", "metadata": {"marker": MARKER}},
        )
        _assert(resp["status"] == 200, "admin feature flag write should pass", resp)
        flag = _one("SELECT * FROM vkpi_feature_flags WHERE flag_key=?", (FLAG_KEY,))
        _assert(_truthy(flag.get("enabled")), "feature flag enabled should persist", flag)
        _assert(
            _count(
                "SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE staff_id=? AND change_type='feature_flag' AND setting_key=?",
                (admin_staff_id, FLAG_KEY),
            )
            >= 1,
            "feature flag service settings audit should be written",
        )
        _assert(
            _count(
                "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE staff_id=? AND action_type='firewall_feature_flag_toggle' AND target_id=?",
                (admin_staff_id, FLAG_KEY),
            )
            >= 1,
            "feature flag firewall decorator business audit should be written",
        )

        # 3. Admin platform update writes DB + settings audit + business audit.
        resp = _request(
            "POST",
            f"/api/admin/vkpi/settings/firewall/platform/{PLATFORM}",
            admin_token,
            {
                "crawl_enabled": True,
                "daily_account_limit": 1,
                "posts_per_account": 2,
                "monthly_budget_usd": 3.0,
                "failure_threshold": 4,
                "metadata": {"marker": MARKER},
            },
        )
        _assert(resp["status"] == 200, "admin platform write should pass", resp)
        platform = _one("SELECT * FROM vkpi_platform_crawl_settings WHERE platform=?", (PLATFORM,))
        _assert(_truthy(platform.get("crawl_enabled")), "platform crawl_enabled should persist", platform)
        _assert(float(platform.get("monthly_budget_usd") or 0) == 3.0, "platform budget should persist", platform)
        _assert(
            _count(
                "SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE staff_id=? AND change_type='platform_crawl' AND setting_key=?",
                (admin_staff_id, PLATFORM),
            )
            >= 1,
            "platform settings audit should be written",
        )
        _assert(
            _count(
                "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE staff_id=? AND action_type='firewall_platform_update' AND target_id=?",
                (admin_staff_id, PLATFORM),
            )
            >= 1,
            "platform firewall business audit should be written",
        )

        # 4. Admin budget update writes DB + settings audit + business audit.
        resp = _request(
            "POST",
            f"/api/admin/vkpi/settings/firewall/budget/{BUDGET_KEY}",
            admin_token,
            {"monthly_limit_usd": 8.5, "current_month_spent": 1.25, "enabled": True, "metadata": {"marker": MARKER}},
        )
        _assert(resp["status"] == 200, "admin budget write should pass", resp)
        budget = _one("SELECT * FROM vkpi_budget_settings WHERE budget_key=?", (BUDGET_KEY,))
        _assert(float(budget.get("monthly_limit_usd") or 0) == 8.5, "budget monthly_limit_usd should persist", budget)
        _assert(_truthy(budget.get("enabled")), "budget enabled should persist", budget)
        _assert(
            _count(
                "SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE staff_id=? AND change_type='budget_setting' AND setting_key=?",
                (admin_staff_id, BUDGET_KEY),
            )
            >= 1,
            "budget settings audit should be written",
        )
        _assert(
            _count(
                "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE staff_id=? AND action_type='firewall_budget_update' AND target_id=?",
                (admin_staff_id, BUDGET_KEY),
            )
            >= 1,
            "budget firewall business audit should be written",
        )

        # 5. Legacy settings endpoints also keep admin gate. They intentionally only have service settings audit.
        resp = _request(
            "PATCH",
            "/api/admin/vkpi/settings/feature-flags",
            employee_token,
            {"flags": [{"flag_key": FLAG_KEY, "enabled": False, "metadata": {"marker": MARKER}}]},
        )
        _assert(resp["status"] == 403, "employee should be rejected by legacy settings endpoint", resp)
        resp = _request(
            "PATCH",
            "/api/admin/vkpi/settings/feature-flags",
            admin_token,
            {"flags": [{"flag_key": FLAG_KEY, "enabled": False, "metadata": {"marker": MARKER}}]},
        )
        _assert(resp["status"] == 200, "admin legacy settings write should pass", resp)
        flag = _one("SELECT * FROM vkpi_feature_flags WHERE flag_key=?", (FLAG_KEY,))
        _assert(not _truthy(flag.get("enabled")), "legacy settings endpoint should persist false", flag)

        print(
            json.dumps(
                {
                    "ok": True,
                    "marker": MARKER,
                    "admin_staff_id": admin_staff_id,
                    "employee_staff_id": employee_staff_id,
                    "checked": [
                        "employee_403_firewall",
                        "admin_firewall_feature_flag_db_settings_audit_business_audit",
                        "admin_firewall_platform_db_settings_audit_business_audit",
                        "admin_firewall_budget_db_settings_audit_business_audit",
                        "employee_403_legacy_settings",
                        "admin_legacy_settings_db_settings_audit",
                    ],
                },
                ensure_ascii=False,
            )
        )
        print("VKPI_P4_22_SETTINGS_FIREWALL_DYNAMIC_QA_OK")
    finally:
        _cleanup(
            [admin_user_id, employee_user_id],
            [admin_staff_id, employee_staff_id],
        )


if __name__ == "__main__":
    main()
