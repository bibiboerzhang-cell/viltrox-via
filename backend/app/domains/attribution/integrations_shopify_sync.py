"""Durable, bounded Shopify Admin API observation sync (never forged HMAC proof)."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid
from typing import Any

from app.core.release_validation import release_validation_active
from app.db.connection import get_conn
from app.domains.commerce import shopify_connect
from app.domains.projects.workflow import staff_id
from app.platform.db.schema_reconciliation import ensure_vkpi_reconciliation_schema

JOB_TYPE = "vkpi_shopify_order_sync"
MAX_PAGES = 5
PAGE_SIZE = 20


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(body: dict[str, Any]) -> dict[str, str]:
    values = body.get("window") or body
    if not isinstance(values, dict):
        raise ValueError("window must be an object")
    end = now().replace(microsecond=0)
    start = end - timedelta(days=1)
    try:
        start = datetime.fromisoformat(str(values.get("start_at") or iso(start)).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(values.get("end_at") or iso(end)).replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError) as exc:
        raise ValueError("window requires timezone-aware ISO start_at/end_at") from exc
    if not timedelta(0) < end - start <= timedelta(days=31) or start < now() - timedelta(days=60) or end > now():
        raise ValueError("window must be at most 31 days within the past 60 days")
    return {"start_at": iso(start), "end_at": iso(end)}


def configured_credentials() -> dict[str, Any]:
    if release_validation_active():
        raise RuntimeError("release_validation_fenced")
    creds = shopify_connect.get_credentials()  # Read-only; refresh occurs only inside canonical post_graphql.
    if not creds.get("shop_domain") or not (creds.get("access_token") or creds.get("client_secret")):
        raise RuntimeError("not_configured")
    if creds.get("status") != "connected":
        raise RuntimeError("shopify_authorization_not_verified")
    return creds


def save_state(uid: str, status: str, metadata: dict[str, Any], *, received: int = 0, matched: int = 0, error: str = "") -> None:
    conn = get_conn()
    conn.execute("""UPDATE vkpi_shopify_sync_runs SET status=?, completed_at=?, orders_received=?,
        orders_matched=?, orders_unmatched=?, orders_failed=?, error_message=?, metadata_json=? WHERE sync_uid=?""",
        (status, None if status in {"queued", "running"} else iso(now()), received, matched,
         received - matched, int(bool(error)), error[:160], json.dumps(metadata, ensure_ascii=False), uid))
    conn.commit()


async def enqueue_sync(queue: Any, body: dict[str, Any], *, staff: dict[str, Any], mode: str) -> dict[str, Any]:
    creds = configured_credentials()
    if queue is None or getattr(queue, "backend_name", "") != "redis-stream":
        raise RuntimeError("durable_queue_required")
    ensure_vkpi_reconciliation_schema()
    if body.get("resume_cursor"):
        raise ValueError("resume using the issued resume_sync_uid, not an arbitrary cursor")
    cursor = None
    created_after = iso(now() - timedelta(days=60))
    if body.get("resume_sync_uid"):
        prior = get_conn().execute("SELECT status, metadata_json FROM vkpi_shopify_sync_runs WHERE sync_uid=?", (str(body["resume_sync_uid"]),)).fetchone()
        if not prior or prior["status"] not in {"partial", "failed"}:
            raise ValueError("resume_sync_uid must identify a failed or partial sync")
        metadata = json.loads(prior["metadata_json"] or "{}")
        if metadata.get("shop_domain") != creds["shop_domain"]:
            raise ValueError("resume shop differs from connected shop")
        bounds = window(metadata["window"])
        cursor = metadata.get("resume_cursor")
        created_after = metadata.get("created_after") or created_after
    else:
        bounds = window(body)
    uid = "shopify-api-" + uuid.uuid4().hex
    metadata = {"window": bounds, "resume_cursor": cursor, "shop_domain": creds["shop_domain"],
                "created_after": created_after,
                "mode": mode, "max_pages": MAX_PAGES, "page_size": PAGE_SIZE,
                "evidence_class": "provider_observed", "counts_toward_verified_gmv": False}
    conn = get_conn()
    conn.execute("""INSERT INTO vkpi_shopify_sync_runs (sync_uid,source,started_at,status,triggered_by_staff_id,metadata_json)
        VALUES (?,?,?,'queued',?,?)""", (uid, "admin_api", iso(now()), staff_id(staff), json.dumps(metadata)))
    conn.commit()
    payload = {"sync_uid": uid, **metadata, "staff_id": staff_id(staff), "user_id": staff.get("user_id") or 0}
    lock_key = "shopify-sync:" + hashlib.sha256(json.dumps([creds["shop_domain"], bounds, cursor], sort_keys=True).encode()).hexdigest()
    try:
        task_id = await queue.enqueue(JOB_TYPE, payload, lock_key=lock_key, timeout_seconds=150)
        receipt = await queue.get_status(task_id)
        original = (receipt or {}).get("payload") or {}
        if isinstance(original, dict) and original.get("sync_uid") and original["sync_uid"] != uid:
            save_state(uid, "duplicate", {**metadata, "existing_sync_uid": original["sync_uid"], "task_id": task_id})
            uid = original["sync_uid"]
        else:
            # The worker owns state after enqueue; avoid racing it by resetting queued.
            pass
    except Exception:
        save_state(uid, "failed", metadata, error="queue_enqueue_failed")
        raise RuntimeError("shopify_queue_enqueue_failed") from None
    return {"status": "queued", "sync_uid": uid, "task_id": task_id, "window": bounds,
            "max_orders": MAX_PAGES * PAGE_SIZE, "counts_toward_verified_gmv": False}
