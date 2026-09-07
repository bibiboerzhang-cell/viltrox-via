"""Bounded metadata-only observation job for already-recorded Apify runs.

The public budget_guard facade injects its runtime bindings so existing test
and operational patch points continue to control the connection and clock.
No Actor start or retry loop is available in this module.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable

from app.domains.costs.apify_cost_reconciliation import reconcile_run_observation


def reconcile_apify_costs_job(
    hours: int, max_rows: int, *, ensure_schema: Callable, get_conn: Callable,
    is_postgres_runtime: Callable, now: Callable, logger: Any,
) -> dict[str, Any]:
    token = next((str(os.environ[key]).strip() for key in
                  ("APIFY_TOKEN", "APIFY_API_TOKEN", "APIFY_API_KEY")
                  if str(os.environ.get(key) or "").strip()), "")
    if not token:
        return {"reconciled": 0, "checked": 0, "reason": "no_token"}
    ensure_schema()
    conn = get_conn()
    cutoff = (now() - timedelta(hours=max(1, int(hours)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT id, cost_usd, metadata_json FROM vkpi_ai_cost_ledger
        WHERE ai_provider = 'apify' AND occurred_at >= ?
        ORDER BY id DESC LIMIT ?
        """,
        (cutoff, max(1, int(max_rows))),
    ).fetchall()
    import httpx

    checked = 0
    reconciled = 0
    delta_total = Decimal("0")
    needs_review = 0
    failures = 0
    pending = []
    # No redirects or retry loop. This job only reads already-known run IDs.
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        for row in rows:
            data = dict(row)
            try:
                meta = json.loads(data.get("metadata_json") or "{}")
                if not isinstance(meta, dict):
                    raise ValueError("metadata_not_object")
            except (TypeError, ValueError):
                failures += 1
                continue
            run_id = str(meta.get("apify_run_id") or "").strip()
            if not run_id or not all(c.isalnum() or c in "_-" for c in run_id) or len(run_id) > 160:
                continue
            checked += 1
            try:
                resp = client.get(
                    f"https://api.apify.com/v2/actor-runs/{run_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code != 200:
                    logger.warning("apify reconcile non-200 run_id=%s status=%s", run_id, resp.status_code)
                    failures += 1
                    continue
                observed_run = resp.json().get("data") or {}
                if not isinstance(observed_run, dict) or observed_run.get("id") != run_id:
                    failures += 1
                    continue
            except Exception:
                logger.warning("apify reconcile fetch failed run_id=%s", run_id, exc_info=True)
                failures += 1
                continue
            try:
                corrected = reconcile_run_observation(
                    conn, observed_run, postgres=is_postgres_runtime(), now=now(),
                    expected_ledger_id=int(data["id"]))
                if corrected.get("reconciled"):
                    reconciled += 1
                    delta_total += Decimal(str(corrected["delta_usd"]))
                else:
                    needs_review += 1
                    pending.append({"apify_run_id": run_id, "ledger_id": int(data["id"]),
                                    "reason": corrected.get("reason", "accounting_unconfirmed")})
            except Exception:
                failures += 1
                logger.warning("apify reconcile accounting failed run_id=%s", run_id, exc_info=True)
    if reconciled:
        logger.info("apify cost reconcile | rows=%s delta_usd=%.4f", reconciled, delta_total)
    return {"reconciled": reconciled, "checked": checked, "delta_usd": float(delta_total),
            "needs_review": needs_review, "failed": failures, "provider_cost_final": False,
            "pending": pending}
