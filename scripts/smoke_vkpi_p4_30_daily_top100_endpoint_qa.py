#!/usr/bin/env python3
"""P4 Step30: Daily Top100 endpoint/service口径一致性 QA.

只读 smoke:
- 不生成 Daily Top100
- 不修改员工、候选、digest 数据
- 只验证真实 HTTP endpoint 返回与 service 当前口径一致
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import sys
import asyncio
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")

from app.core.permissions import staff_context_for_user  # noqa: E402
from app.core.security import make_token  # noqa: E402
from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains import analytics  # noqa: E402


BASE_URL = os.environ.get("VKPI_BASE_URL", "http://127.0.0.1:8102").rstrip("/")


def _fail(message: str, context: Any = None) -> None:
    suffix = f"\n{json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)}" if context is not None else ""
    raise AssertionError(f"{message}{suffix}")


def _request_json(path: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8") or "{}"
            return {"status_code": resp.status, "json": json.loads(body)}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        _fail(f"GET {path} returned HTTP {exc.code}", body)
    except Exception as exc:  # noqa: BLE001
        _fail(f"GET {path} failed", str(exc))
    raise AssertionError("unreachable")


def _query(path: str, params: dict[str, Any]) -> str:
    clean = {k: str(v) for k, v in params.items() if v is not None and str(v) != ""}
    if not clean:
        return path
    return f"{path}?{urllib.parse.urlencode(clean)}"


def _owner_user() -> dict[str, Any]:
    row = get_conn().execute(
        """
        SELECT u.*
        FROM staff s
        JOIN users u ON u.id = s.user_id
        WHERE COALESCE(s.active, 1)=1
          AND (COALESCE(s.is_owner, 0)=1 OR LOWER(COALESCE(s.role, '')) IN ('admin', 'manager', 'lead', 'marketing_lead'))
        ORDER BY COALESCE(s.is_owner, 0) DESC, s.id ASC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        _fail("No active owner/admin/manager staff user available for Daily Top100 endpoint QA")
    return dict(row)


def _active_staff_count() -> int:
    row = get_conn().execute("SELECT COUNT(*) AS n FROM staff WHERE COALESCE(active, 1)=1").fetchone()
    return int((row or {}).get("n") or 0)


def _first_ready_staff_id(status: dict[str, Any]) -> int:
    rows = status.get("staff") or []
    ready = [row for row in rows if str(row.get("status") or "") == "ready"]
    candidates = ready or rows
    if not candidates:
        _fail("Daily Top100 status returned no staff rows", status)
    return int(candidates[0].get("staff_id") or 0)


def _first_real_product_sku() -> str:
    rows = get_conn().execute(
        """
        SELECT source_product_sku AS sku, COUNT(*) AS n
        FROM vkpi_outreach_suggestions
        WHERE COALESCE(source_product_sku, '') <> ''
        GROUP BY source_product_sku
        ORDER BY n DESC, source_product_sku ASC
        LIMIT 1
        """
    ).fetchall()
    if rows:
        return str(rows[0]["sku"] or "")
    row = get_conn().execute(
        "SELECT product_sku FROM vkpi_monitored_products WHERE COALESCE(enabled, 1)=1 ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return str((row or {}).get("product_sku") or "")


def _assert_status_shape(endpoint_status: dict[str, Any], service_status: dict[str, Any], *, label: str) -> None:
    required = [
        "status",
        "digest_date",
        "eligible_staff_count",
        "active_staff_count",
        "generated_staff_count",
        "ready_staff_count",
        "empty_staff_count",
        "candidate_source",
        "total_candidates",
        "duplicate_suggestion_count",
        "assignment_strategy",
        "staff",
    ]
    missing = [key for key in required if key not in endpoint_status]
    if missing:
        _fail(f"{label}: missing status keys", {"missing": missing, "status": endpoint_status})
    if endpoint_status.get("status") != "ok":
        _fail(f"{label}: status should be ok", endpoint_status)

    comparable = [
        "digest_date",
        "eligible_staff_count",
        "active_staff_count",
        "generated_staff_count",
        "ready_staff_count",
        "empty_staff_count",
        "candidate_source",
        "total_candidates",
        "duplicate_suggestion_count",
        "assignment_strategy",
        "items_total",
    ]
    mismatches = {
        key: {"endpoint": endpoint_status.get(key), "service": service_status.get(key)}
        for key in comparable
        if endpoint_status.get(key) != service_status.get(key)
    }
    if mismatches:
        _fail(f"{label}: endpoint/service status mismatch", mismatches)

    eligible = int(endpoint_status.get("eligible_staff_count") or 0)
    ready = int(endpoint_status.get("ready_staff_count") or 0)
    generated = int(endpoint_status.get("generated_staff_count") or 0)
    total_candidates = int(endpoint_status.get("total_candidates") or 0)
    duplicates = int(endpoint_status.get("duplicate_suggestion_count") or 0)
    active_db = _active_staff_count()

    if eligible <= 0:
        _fail(f"{label}: eligible_staff_count should be > 0", endpoint_status)
    if ready > eligible or generated > eligible:
        _fail(f"{label}: ready/generated count cannot exceed eligible count", endpoint_status)
    if total_candidates <= 0:
        _fail(f"{label}: total_candidates should be > 0 in current local dataset", endpoint_status)
    if duplicates != 0:
        _fail(f"{label}: duplicate_suggestion_count should be 0", endpoint_status)
    if endpoint_status.get("candidate_source") != "outreach_suggestions":
        _fail(f"{label}: candidate_source should be outreach_suggestions", endpoint_status)
    if active_db != 11 and eligible == 11:
        _fail(f"{label}: stale 11-staff expectation leaked into endpoint", {"active_db": active_db, "status": endpoint_status})


def main() -> None:
    try:
        user = _owner_user()
        token = make_token(int(user["id"]), str(user.get("role") or "admin"))
        staff = staff_context_for_user(user)
        product_sku = _first_real_product_sku()

        service_all = analytics.daily_staff_outreach_digest_status(limit=100, staff=staff)
        endpoint_all = _request_json("/api/admin/vkpi/analytics/daily-digest/status", token)
        if endpoint_all["status_code"] != 200:
            _fail("status endpoint should return HTTP 200", endpoint_all)
        _assert_status_shape(endpoint_all["json"], service_all, label="all-products status")

        service_product = None
        endpoint_product = None
        if product_sku:
            service_product = analytics.daily_staff_outreach_digest_status(limit=100, staff=staff, product_sku=product_sku)
            endpoint_product = _request_json(
                _query("/api/admin/vkpi/analytics/daily-digest/status", {"product_sku": product_sku}),
                token,
            )
            if endpoint_product["status_code"] != 200:
                _fail("product status endpoint should return HTTP 200", endpoint_product)
            _assert_status_shape(endpoint_product["json"], service_product, label=f"product status {product_sku}")

        target_staff_id = _first_ready_staff_id(endpoint_all["json"])
        service_digest = analytics.list_daily_staff_outreach_digest(target_staff_id, limit=100)
        endpoint_digest = _request_json(
            _query("/api/admin/vkpi/analytics/daily-digest", {"staff_id": target_staff_id, "limit": 100}),
            token,
        )
        if endpoint_digest["status_code"] != 200:
            _fail("digest endpoint should return HTTP 200", endpoint_digest)
        digest_json = endpoint_digest["json"]
        if digest_json.get("digest_date") != service_digest.get("digest_date"):
            _fail("digest endpoint/service date mismatch", {"endpoint": digest_json, "service": service_digest})
        if len(digest_json.get("items") or []) != len(service_digest.get("items") or []):
            _fail("digest endpoint/service item count mismatch", {"endpoint": digest_json, "service": service_digest})
        if digest_json.get("digest") and not isinstance(digest_json.get("items"), list):
            _fail("digest endpoint items should be a list", digest_json)

        stdout_out(
            json.dumps(
                {
                    "ok": True,
                    "marker": "VKPI_P4_30_DAILY_TOP100_ENDPOINT_QA_OK",
                    "base_url": BASE_URL,
                    "active_staff_db": _active_staff_count(),
                    "eligible_staff_count": endpoint_all["json"].get("eligible_staff_count"),
                    "ready_staff_count": endpoint_all["json"].get("ready_staff_count"),
                    "total_candidates": endpoint_all["json"].get("total_candidates"),
                    "candidate_source": endpoint_all["json"].get("candidate_source"),
                    "duplicate_suggestion_count": endpoint_all["json"].get("duplicate_suggestion_count"),
                    "product_sku_checked": product_sku,
                    "staff_digest_checked": target_staff_id,
                    "staff_digest_items": len(digest_json.get("items") or []),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    main()
