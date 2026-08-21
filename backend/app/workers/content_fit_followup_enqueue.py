"""Authorized final-v1 -> content-fit follow-up enqueue."""
from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from app.domains.kol import content_fit_analysis
from app.domains.tasks.apify_idempotency import active_job_idempotency_key


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _authorization_scope(payload: dict[str, Any]) -> str:
    from app.domains.kol.provider_job_access import FENCE_KEY as PROVIDER_FENCE_KEY

    fence = payload.get(PROVIDER_FENCE_KEY)
    if isinstance(fence, dict):
        session = fence.get("session") if isinstance(fence.get("session"), dict) else {}
        if fence.get("mode") == "server_owned":
            return f"system:target:{fence.get('target_id')}:session:0"
        actor = fence.get("actor") if isinstance(fence.get("actor"), dict) else {}
        return (
            f"user:{_int(actor.get('user_id'))}:"
            f"session:{_int(session.get('search_session_id'))}:"
            f"item:{_int(payload.get('search_session_item_id'))}"
        )
    from app.domains.kol.my_kol_paid_action_access import FENCE_KEY as MY_KOL_FENCE_KEY

    my_kol = payload.get(MY_KOL_FENCE_KEY)
    if isinstance(my_kol, dict):
        return f"mykol:staff:{_int(my_kol.get('staff_id'))}"
    return "authorization-missing"


def enqueue_content_fit_after_final_v1(
    conn: Any,
    *,
    job_id: int,
    deep_result: dict[str, Any] | None,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Authorize first, then use only the signed parent product/session scope."""

    if not deep_result or deep_result.get("status") != "ready":
        return None
    kol_pool_id = _int(deep_result.get("kol_pool_id"))
    if kol_pool_id <= 0:
        return None
    source = dict(source_payload or {})
    normalized_sku = content_fit_analysis.normalize_product_sku(
        source.get("product_sku")
    )
    derive_method = content_fit_analysis.content_fit_derive_method(normalized_sku)
    payload = {
        "queue_lane": "batch",
        "target_type": "kol",
        "target_id": str(kol_pool_id),
        "kol_pool_id": kol_pool_id,
        "product_sku": normalized_sku or None,
        "derive_method": derive_method,
        "source": "final_v1_worker_followup",
        "trigger": "final_v1_done",
        "source_job_id": int(job_id),
        "viltrox_fit_score_untouched": True,
        "query_text": f"content fit - kol_pool #{kol_pool_id}",
    }
    # This must precede every cache/job lookup.  A revoked actor or drifted
    # session must learn nothing and must never attach to another actor's job.
    from app.domains.kol.content_fit_job_access import authorize_content_fit_followup

    payload = authorize_content_fit_followup(payload, source_payload=source)
    idempotency_key = active_job_idempotency_key(
        "kol_content_fit_analysis",
        _authorization_scope(payload),
        kol_pool_id,
        normalized_sku,
    )

    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT 1 FROM vkpi_analysis_cache
                WHERE target_type='kol' AND derive_method=%s
                  AND target_id=%s AND status='ready' LIMIT 1
                """,
                (derive_method, str(kol_pool_id)),
            )
            if cur.fetchone():
                return {
                    "status": "cache_reused",
                    "kol_pool_id": kol_pool_id,
                    "product_sku": normalized_sku or None,
                    "derive_method": derive_method,
                }
            cur.execute(
                """
                SELECT id, status FROM apify_jobs
                WHERE idempotency_key=%s AND status IN ('queued', 'running')
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (idempotency_key,),
            )
            existing = cur.fetchone()
            if existing:
                return {
                    "status": "already_queued" if existing["status"] == "queued" else "already_running",
                    "job_id": int(existing["id"]),
                    "kol_pool_id": kol_pool_id,
                    "product_sku": normalized_sku or None,
                    "derive_method": derive_method,
                }
            cur.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, idempotency_key, status, created_at, updated_at)
                VALUES ('kol_content_fit_analysis', %s::jsonb, %s, 'queued', NOW(), NOW())
                ON CONFLICT (idempotency_key)
                  WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
                    AND status IN ('queued', 'running')
                DO NOTHING
                RETURNING id, status
                """,
                (json.dumps(payload, ensure_ascii=False, default=str), idempotency_key),
            )
            row = cur.fetchone() or {}
            inserted = bool(row)
            if not row:
                cur.execute(
                    """SELECT id, status FROM apify_jobs
                       WHERE idempotency_key=%s AND status IN ('queued', 'running')
                       ORDER BY id DESC LIMIT 1""",
                    (idempotency_key,),
                )
                row = cur.fetchone() or {}
    return {
        "status": "queued" if inserted else ("already_running" if row.get("status") == "running" else "already_queued"),
        "job_id": int(row["id"]) if row.get("id") is not None else None,
        "kol_pool_id": kol_pool_id,
        "product_sku": normalized_sku or None,
        "derive_method": derive_method,
    }


__all__ = ["enqueue_content_fit_after_final_v1"]
