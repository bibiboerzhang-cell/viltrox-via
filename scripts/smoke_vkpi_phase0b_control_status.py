#!/usr/bin/env python3
"""Smoke test for V-KPI Phase 0B management control status.

Verifies high-cost feature flags, platform crawl limits, budget controls,
08:00 sync policy, and YouTube KPI reserved slot without triggering external
API calls or leaving changed settings behind.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.core.security import make_token  # noqa: E402
from app.db.connection import get_conn, is_postgres_runtime  # noqa: E402
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402
from app.services.vkpi import platform_crawl_settings  # noqa: E402

BASE = os.environ.get("VKPI_BASE_URL", "http://127.0.0.1:8102")
ADMIN_USER_ID = int(os.environ.get("VKPI_SMOKE_ADMIN_USER_ID", "1"))
PREFIX = "vkpi-control-status-smoke-"


def _db_bool(value: Any) -> bool | int:
    if isinstance(value, str):
        value = value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    value = bool(value)
    return value if is_postgres_runtime() else (1 if value else 0)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.token = make_token(ADMIN_USER_ID, "admin")
        self.conn = get_conn()
        self.old_flags: dict[str, dict[str, Any]] = {}
        self.old_platforms: dict[str, dict[str, Any]] = {}
        self.old_budgets: dict[str, dict[str, Any]] = {}

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:500]}") from exc

    def remember(self) -> None:
        platform_crawl_settings.ensure_defaults()
        for key in ("audience_graph_l3", "ml_scoring", "youtube_kpi_reserved"):
            row = self.conn.execute("SELECT * FROM vkpi_feature_flags WHERE flag_key=?", (key,)).fetchone()
            if row:
                self.old_flags[key] = dict(row)
        row = self.conn.execute("SELECT * FROM vkpi_platform_crawl_settings WHERE platform='youtube'").fetchone()
        if row:
            self.old_platforms["youtube"] = dict(row)
        for key in ("audience_graph", "crawl_total"):
            row = self.conn.execute("SELECT * FROM vkpi_budget_settings WHERE budget_key=?", (key,)).fetchone()
            if row:
                self.old_budgets[key] = dict(row)

    def restore(self) -> dict[str, int]:
        for key, row in self.old_flags.items():
            self.conn.execute(
                "UPDATE vkpi_feature_flags SET enabled=?, description=?, updated_by_staff_id=?, updated_at=?, metadata_json=? WHERE flag_key=?",
                (_db_bool(row.get("enabled")), row.get("description"), row.get("updated_by_staff_id"), row.get("updated_at"), row.get("metadata_json") or "{}", key),
            )
        for platform, row in self.old_platforms.items():
            self.conn.execute(
                """
                UPDATE vkpi_platform_crawl_settings
                SET crawl_enabled=?, daily_account_limit=?, posts_per_account=?, crawl_comments=?, crawl_followers=?,
                    crawl_audience_graph=?, only_uncontacted_kols=?, include_company_accounts=?, include_competitor_accounts=?,
                    include_candidate_kols=?, monthly_budget_usd=?, failure_threshold=?, last_test_status=?, last_test_at=?,
                    updated_by_staff_id=?, updated_at=?, metadata_json=?
                WHERE platform=?
                """,
                (
                    _db_bool(row.get("crawl_enabled")), row.get("daily_account_limit"), row.get("posts_per_account"), _db_bool(row.get("crawl_comments")), _db_bool(row.get("crawl_followers")),
                    _db_bool(row.get("crawl_audience_graph")), _db_bool(row.get("only_uncontacted_kols")), _db_bool(row.get("include_company_accounts")), _db_bool(row.get("include_competitor_accounts")),
                    _db_bool(row.get("include_candidate_kols")), row.get("monthly_budget_usd"), row.get("failure_threshold"), row.get("last_test_status"), row.get("last_test_at"),
                    row.get("updated_by_staff_id"), row.get("updated_at"), row.get("metadata_json") or "{}", platform,
                ),
            )
        for key, row in self.old_budgets.items():
            self.conn.execute(
                "UPDATE vkpi_budget_settings SET monthly_limit_usd=?, current_month_spent=?, alert_threshold_pct=?, enabled=?, updated_by_staff_id=?, updated_at=?, metadata_json=? WHERE budget_key=?",
                (row.get("monthly_limit_usd"), row.get("current_month_spent"), row.get("alert_threshold_pct"), _db_bool(row.get("enabled")), row.get("updated_by_staff_id"), row.get("updated_at"), row.get("metadata_json") or "{}", key),
            )
        like = f"%{self.marker}%"
        self.conn.execute("DELETE FROM vkpi_settings_change_logs WHERE metadata_json LIKE ?", (like,))
        self.conn.commit()
        return {
            "settings_logs": int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE metadata_json LIKE ?", (like,)).fetchone()["n"]),
        }

    def run(self) -> dict[str, Any]:
        self.remember()
        try:
            initial = self.request("GET", "/api/admin/vkpi/settings/control-status")
            assert initial.get("sync_policy", {}).get("daily_sync_time") == "08:00", initial
            assert initial.get("sync_policy", {}).get("candidate_limit_per_staff") == 100, initial
            assert initial.get("sync_policy", {}).get("only_uncontacted_kols") is True, initial
            assert initial.get("youtube_kpi", {}).get("reserved") is True, initial
            high_cost_keys = {row.get("flag_key") for row in initial.get("high_cost_controls", [])}
            assert {"audience_graph_l3", "ml_scoring", "llm_summary"}.issubset(high_cost_keys), high_cost_keys

            payload_marker = {"marker": self.marker}
            self.request("PATCH", "/api/admin/vkpi/settings/feature-flags", {"flags": [
                {"flag_key": "audience_graph_l3", "enabled": True, "metadata": payload_marker},
                {"flag_key": "ml_scoring", "enabled": True, "metadata": payload_marker},
                {"flag_key": "youtube_kpi_reserved", "enabled": True, "metadata": payload_marker},
            ]})
            self.request("PATCH", "/api/admin/vkpi/settings/platform-crawl", {"platforms": [
                {"platform": "youtube", "crawl_enabled": True, "daily_account_limit": 10, "posts_per_account": 20, "crawl_followers": False, "crawl_audience_graph": False, "monthly_budget_usd": 25, "last_test_status": "not_configured", "metadata": payload_marker}
            ]})
            self.request("PATCH", "/api/admin/vkpi/settings/budgets", {"budgets": [
                {"budget_key": "audience_graph", "monthly_limit_usd": 100, "current_month_spent": 10, "enabled": True, "metadata": payload_marker},
                {"budget_key": "crawl_total", "monthly_limit_usd": 200, "current_month_spent": 50, "enabled": True, "metadata": payload_marker},
            ]})
            updated = self.request("GET", "/api/admin/vkpi/settings/control-status")
            assert updated.get("summary", {}).get("risk_level") == "high", updated
            assert int(updated.get("summary", {}).get("enabled_high_cost_controls") or 0) >= 2, updated
            assert updated.get("youtube_kpi", {}).get("flag_enabled") is True, updated
            assert updated.get("youtube_kpi", {}).get("platform_enabled") is True, updated
            logs = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE metadata_json LIKE ?", (f"%{self.marker}%",)).fetchone()["n"])
            assert logs >= 5, logs
            cleanup = self.restore()
            assert cleanup["settings_logs"] == 0, cleanup
            return {
                "marker": self.marker,
                "initial_risk": initial.get("summary", {}).get("risk_level"),
                "updated_risk": updated.get("summary", {}).get("risk_level"),
                "settings_logs_verified": logs,
                "cleanup": cleanup,
            }
        except Exception:
            self.restore()
            raise


def main() -> None:
    ensure_vkpi_product_industry_schema()
    ensure_vkpi_audit_schema()
    result = Smoke().run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
