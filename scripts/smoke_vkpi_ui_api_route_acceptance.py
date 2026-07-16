#!/usr/bin/env python3
"""Smoke test for V-KPI frontend-facing API route acceptance.

P2.13 goal:
  - catch login-after-500 regressions before browser QA
  - verify the dashboard/settings routes used by the frontend return non-500
  - verify management-only settings are blocked for non-manager staff
  - verify provider status responses never expose full API keys

Default mode is offline-safe: it does not trigger provider probes or crawler
calls. Set VKPI_P2_13_PROBE=1 for a small live provider probe pass.
"""
from __future__ import annotations

from stdout_utils import out

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.core.security import make_token
from app.db.connection import get_conn

from _smoke_seed import cleanup_admin, seed_admin


BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102").rstrip("/")
MARKER_PREFIX = "vkpi-ui-api-qa"
SECRET_PATTERNS = [
    re.compile(r"apify_api_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"anthropic-[A-Za-z0-9_-]{20,}"),
]


def _redact(value: str) -> str:
    result = value
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _assert_no_secret_payload(label: str, payload: Any) -> None:
    raw = _json_dumps(payload)
    hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(raw)]
    if hits:
        raise AssertionError(f"{label} response appears to expose provider secret: {hits}")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{MARKER_PREFIX}-{int(time.time())}"
        self.conn = get_conn()
        self.admin_user_id = 0
        self.admin_staff_id = 0
        self.admin_token = ""
        self.operator_user_id = 0
        self.operator_staff_id = 0
        self.operator_token = ""
        self.results: list[dict[str, Any]] = []

    def seed(self) -> None:
        self.admin_user_id, self.admin_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="admin",
            role="admin",
            vkpi_permission="admin",
            is_owner=True,
            extra_permissions={"system": "admin"},
        )
        self.admin_token = make_token(self.admin_user_id, "admin")
        self.operator_user_id, self.operator_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="operator",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        self.operator_token = make_token(self.operator_user_id, "employee")

    def cleanup(self) -> None:
        cleanup_admin(self.conn, user_id=self.operator_user_id, staff_id=self.operator_staff_id)
        cleanup_admin(self.conn, user_id=self.admin_user_id, staff_id=self.admin_staff_id)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        expected_status: int = 200,
        timeout: int = 60,
    ) -> Any:
        data = None
        if payload is not None:
            data = _json_dumps(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token or self.admin_token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status != expected_status:
                    raise AssertionError(
                        f"expected HTTP {expected_status}, got {resp.status} for {method} {path}"
                    )
                parsed = json.loads(body) if body else {}
                self.results.append({"method": method, "path": path, "status": resp.status})
                return parsed
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == expected_status:
                self.results.append({"method": method, "path": path, "status": exc.code})
                try:
                    return json.loads(body) if body else {"status": exc.code}
                except json.JSONDecodeError:
                    return {"status": exc.code, "body": body}
            body_preview = _redact(body[:1200])
            raise RuntimeError(f"HTTP {exc.code} for {method} {path}: {body_preview}") from exc

    def assert_admin_routes(self) -> None:
        routes = [
            ("/api/marketing/dashboard?window_days=7", "dashboard"),
            ("/api/marketing/dashboard/view/employee?window_days=7", "employee dashboard"),
            ("/api/marketing/dashboard/revenue-trend?window_days=7", "revenue trend"),
            ("/api/marketing/dashboard/product-performance?window_days=7&limit=5", "product performance"),
            ("/api/marketing/projects?limit=5", "projects"),
            ("/api/marketing/links?limit=5", "links"),
            ("/api/marketing/alerts?status=open&limit=5", "alerts"),
            ("/api/marketing/staff-kpi?window=week", "staff kpi"),
            ("/api/marketing/attribution?limit=5", "attribution"),
            ("/api/marketing/attribution/unmatched?limit=5", "unmatched attribution"),
            ("/api/marketing/costs?limit=5", "costs"),
            ("/api/admin/staff", "staff list"),
            ("/api/marketing/kpi-ledger?limit=5", "kpi ledger"),
            ("/api/marketing/product-costs?limit=5", "product costs"),
            ("/api/marketing/kols?limit=5", "kols"),
            ("/api/marketing/settings/providers", "provider settings"),
            ("/api/marketing/settings/platform-crawl", "platform crawl settings"),
            ("/api/marketing/settings/budgets", "budget settings"),
            ("/api/marketing/settings/control-status", "control status"),
            ("/api/admin/vkpi/settings/comment-alerts", "comment alert settings"),
            ("/api/admin/vkpi/sync/overview", "sync overview"),
            ("/api/admin/vkpi/sync/industry/failures?limit=5", "sync failures"),
            ("/api/admin/vkpi/comment-intelligence/overview?days=7&recent_limit=5", "comment intelligence"),
            ("/api/marketing/data-quality?limit=5", "data quality"),
        ]
        for path, label in routes:
            payload = self.request_json("GET", path)
            _assert_no_secret_payload(label, payload)

    def assert_provider_status_shape(self) -> None:
        payload = self.request_json("GET", "/api/marketing/settings/providers")
        _assert_no_secret_payload("provider settings", payload)
        providers = payload.get("providers")
        if not isinstance(providers, list):
            raise AssertionError(f"provider settings missing providers list: {payload}")
        names = {str(row.get("provider") or "") for row in providers if isinstance(row, dict)}
        missing = {"apify", "youtube"} - names
        if missing:
            raise AssertionError(f"provider settings missing expected providers: {sorted(missing)}")
        if bool(payload.get("full_key_readable")):
            raise AssertionError("provider settings must not expose full_key_readable=true")
        for row in providers:
            if not isinstance(row, dict):
                continue
            if bool(row.get("key_visible")):
                raise AssertionError(f"provider {row.get('provider')} exposes key_visible=true")

    def assert_manager_gate(self) -> None:
        self.request_json(
            "GET",
            "/api/marketing/settings/providers",
            token=self.operator_token,
            expected_status=403,
        )
        self.request_json(
            "GET",
            "/api/marketing/settings/budgets",
            token=self.operator_token,
            expected_status=403,
        )

    def optional_provider_probe(self) -> None:
        if os.environ.get("VKPI_P2_13_PROBE") != "1":
            return
        for provider in ("youtube", "apify"):
            payload = self.request_json(
                "POST",
                f"/api/marketing/settings/providers/{provider}/probe",
                payload={},
                timeout=90,
            )
            _assert_no_secret_payload(f"{provider} provider probe", payload)
            if str(payload.get("provider") or provider) not in {provider, "google"}:
                raise AssertionError(f"{provider} probe returned unexpected provider payload: {payload}")

    def run(self) -> dict[str, Any]:
        self.seed()
        try:
            self.assert_admin_routes()
            self.assert_provider_status_shape()
            self.assert_manager_gate()
            self.optional_provider_probe()
            return {
                "ok": True,
                "checked_routes": len(self.results),
                "probe": os.environ.get("VKPI_P2_13_PROBE") == "1",
            }
        finally:
            self.cleanup()


def main() -> None:
    smoke = Smoke()
    try:
        result = smoke.run()
    except Exception as exc:
        try:
            smoke.cleanup()
        finally:
            out(f"VKPI_UI_API_ROUTE_ACCEPTANCE_SMOKE_FAIL: {_redact(str(exc))}", file=sys.stderr)
        raise
    out(_json_dumps(result))
    out("VKPI_UI_API_ROUTE_ACCEPTANCE_SMOKE_OK")


if __name__ == "__main__":
    main()
