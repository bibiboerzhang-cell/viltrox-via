"""Affiliate order reward-trace synchronization for Via learning."""
from __future__ import annotations

from app.services.memory.via_learning_common import *

def _filter_recent_control_rows(rows: list[dict[str, Any]], window_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days or 1)))
    filtered: list[dict[str, Any]] = []
    for row in rows:
        created_at = _parse_timestamp(row.get("created_at") or row.get("updated_at") or "")
        if created_at and created_at < cutoff:
            continue
        filtered.append(row)
    return filtered


def _load_json_doc(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _clean_affiliate_ref(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _extract_affiliate_order_candidates(row: Any, payload: dict[str, Any]) -> list[str]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    note_attributes = body.get("note_attributes") if isinstance(body.get("note_attributes"), list) else []
    candidates: list[str] = []
    for candidate in (
        row["creator_handle"],
        payload.get("ref_code"),
        payload.get("creator_code"),
        payload.get("creator_handle"),
        body.get("discount_code"),
        body.get("source_name"),
    ):
        cleaned = _clean_affiliate_ref(candidate)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    for item in note_attributes:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or item.get("key") or "").strip().lower()
        value = _clean_affiliate_ref(item.get("value"))
        if key in {"ref", "creator", "creator_code", "creator_id", "affiliate", "code"} and value and value not in candidates:
            candidates.append(value)
    return candidates


def _sync_affiliate_order_reward_traces(limit: int = 400, window_days: int = 21) -> dict[str, Any]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, external_id, creator_handle, occurred_at, processed_at, ingest_status, payload_json
            FROM platform_ingest_events
            WHERE source_platform='shopify' AND entity_type='order'
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        user_rows = conn.execute("SELECT * FROM users ORDER BY id DESC LIMIT ?", (max(200, int(limit) * 2),)).fetchall()
    except Exception:
        return {"imported": 0, "skipped": 0, "matched_users": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days or 1)))
    user_by_code = {_clean_affiliate_ref(row["creator_code"]): dict(row) for row in user_rows if _clean_affiliate_ref(row["creator_code"])}
    user_by_email = {_clean_affiliate_ref(row["email"]): dict(row) for row in user_rows if _clean_affiliate_ref(row["email"])}
    latest_decision_by_user: dict[int, dict[str, Any]] = {}
    program_cache: dict[int, dict[str, Any]] = {}
    for item in list_recent_via_decisions(max(160, int(limit) * 2)):
        user_id = int(item.get("user_id") or 0)
        if user_id > 0 and user_id not in latest_decision_by_user:
            latest_decision_by_user[user_id] = item

    imported = 0
    skipped = 0
    matched_users = 0
    for row in rows:
        occurred_at = _parse_timestamp(row["occurred_at"] or row["processed_at"] or "")
        if occurred_at and occurred_at < cutoff:
            continue
        payload = _load_json_doc(row["payload_json"], {})
        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        customer = body.get("customer") if isinstance(body.get("customer"), dict) else {}
        candidates = _extract_affiliate_order_candidates(row, payload)
        customer_email = _clean_affiliate_ref(customer.get("email"))
        if customer_email and customer_email not in candidates:
            candidates.append(customer_email)
        matched_user = None
        for candidate in candidates:
            matched_user = user_by_code.get(candidate) or user_by_email.get(candidate)
            if matched_user:
                break
        user_id = int((matched_user or {}).get("id") or 0)
        if user_id > 0:
            matched_users += 1
        creator_ref = next((candidate for candidate in candidates if candidate), "") or _clean_affiliate_ref(row["creator_handle"]) or f"order-{int(row['id'])}"
        latest_decision = latest_decision_by_user.get(user_id) if user_id > 0 else {}
        session_key = str((latest_decision or {}).get("session_key") or f"affiliate:{creator_ref}")
        order_id = str(row["external_id"] or body.get("id") or body.get("order_number") or row["id"]).strip()
        idempotency_key = f"shopify-order:{order_id}"
        if get_via_reward_trace_by_idempotency_key(idempotency_key):
            skipped += 1
            continue
        effective_rate = 0.0
        if matched_user:
            if user_id not in program_cache:
                program_cache[user_id] = build_creator_program_snapshot(dict(matched_user))
            program = program_cache[user_id]
            effective_rate = float(program.get("effective_commission_rate") or 0.0)
        order_total = float(body.get("current_total_price") or body.get("total_price") or payload.get("order_total") or 0.0)
        estimated_commission = round(order_total * effective_rate, 2) if effective_rate > 0 else 0.0
        insert_via_reward_trace(
            session_key=session_key,
            decision_id=str((latest_decision or {}).get("decision_id") or ""),
            user_id=user_id,
            event_type="affiliate_order",
            surface="affiliate",
            source="shopify",
            origin="platform_ingest",
            product_key=creator_ref,
            event_value=order_total,
            event_payload={
                "order_id": order_id,
                "ref_code": creator_ref,
                "financial_status": str(body.get("financial_status") or ""),
                "ingest_status": str(row["ingest_status"] or ""),
                "estimated_commission": estimated_commission,
            },
            idempotency_key=idempotency_key,
        )
        imported += 1
    return {"imported": imported, "skipped": skipped, "matched_users": matched_users}


__all__ = [name for name in globals() if not name.startswith("__")]
