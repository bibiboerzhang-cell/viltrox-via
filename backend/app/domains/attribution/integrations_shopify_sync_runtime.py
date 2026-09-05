"""Bounded GraphQL order observation pages and durable checkpoints."""
import hashlib
import json
import time
from typing import Any

from app.domains import attribution
from app.domains.attribution import integrations
from app.domains.attribution import integrations_shopify_sync as sync
from app.domains.attribution.integrations_money import currency_code, exact_cents
from app.domains.commerce import shopify_connect

# Official Admin GraphQL OrderConnection contract, checked against 2026-07.
ORDER_QUERY = """query VkpiOrders($first: Int!, $after: String, $query: String!) {
  orders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { id legacyResourceId name createdAt updatedAt processedAt cancelledAt
      displayFinancialStatus currencyCode discountCodes
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      totalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 20) { pageInfo { hasNextPage } nodes { sku title quantity } }
    }
  }
}"""


def observation(node: dict[str, Any], shop: str) -> dict[str, Any]:
    order_id = str(node.get("id") or "")
    if not order_id.startswith("gid://shopify/Order/"):
        raise ValueError("invalid_order_identity")
    money = (node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
    currency = currency_code(money.get("currencyCode"))
    if currency != currency_code(node.get("currencyCode")):
        raise ValueError("order_currency_mismatch")
    amount = exact_cents(money.get("amount"))
    if amount < 0:
        raise ValueError("negative_order_total")
    lines = node.get("lineItems") or {}
    if (lines.get("pageInfo") or {}).get("hasNextPage"):
        raise ValueError("order_line_items_truncated")
    native_shape = {"id": node.get("legacyResourceId"), "admin_graphql_api_id": order_id,
                    "line_items": lines.get("nodes") or [], "discount_codes": [{"code": c} for c in node.get("discountCodes") or []]}
    context = integrations._shopify_ref_context(native_shape)
    match = context.get("match") or {}
    return {"source_platform": "shopify", "source_ref": f"shopify:api:{shop}:{order_id}",
            "project_id": match.get("project_id"), "link_id": match.get("link_id"),
            "kol_id": match.get("kol_id"), "staff_id": match.get("staff_id"),
            "product_sku": context.get("product_sku") or "", "revenue_cents": amount,
            "currency": currency, "occurred_at": node.get("processedAt") or node.get("createdAt"),
            "evidence": {"source": "shopify_admin_api", "source_order_id": order_id, "shop_domain": shop,
                         "auth_mode": "shopify-admin-api", "counts_toward_verified_gmv": False,
                         "amount_basis": "current_order_total_reference_only", "payload": node,
                         "raw_payload_hash": hashlib.sha256(json.dumps(node, sort_keys=True).encode()).hexdigest()}}


def _require_api_success(result: dict[str, Any]) -> None:
    """Map provider failures before inspecting or persisting a page."""
    if result.get("ok"):
        return
    codes = {(e.get("extensions") or {}).get("code") for e in result.get("errors") or [] if isinstance(e, dict)}
    if result.get("status_code") == 429 or "THROTTLED" in codes:
        raise RuntimeError("shopify_rate_limited")
    if result.get("status_code") in {401, 403} or "ACCESS_DENIED" in codes:
        raise RuntimeError("shopify_permission_denied")
    if result.get("reason") == "deadline_exceeded":
        raise RuntimeError("sync_deadline_exceeded")
    raise RuntimeError("shopify_api_rejected_or_unavailable")


def _prepare_page(result: dict[str, Any], shop: str, seen: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate page bounds, cursor progress and every row before any write."""
    _require_api_success(result)
    connection = (result.get("data") or {}).get("orders") or {}
    page_info = connection.get("pageInfo") or {}
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or len(nodes) > sync.PAGE_SIZE or not isinstance(page_info.get("hasNextPage"), bool):
        raise ValueError("malformed_orders_page")
    if page_info["hasNextPage"] and (not nodes or not page_info.get("endCursor") or page_info["endCursor"] in seen):
        raise ValueError("orders_cursor_did_not_advance")
    return page_info, [observation(node, shop) for node in nodes]


def run_sync(payload: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    uid = str(payload.get("sync_uid") or "")
    if not uid:
        raise ValueError("sync_uid_required")
    stored = sync.get_conn().execute("SELECT status,metadata_json,orders_received,orders_matched,error_message FROM vkpi_shopify_sync_runs WHERE sync_uid=?", (uid,)).fetchone()
    if not stored:
        raise ValueError("sync_run_not_found")
    state = dict(stored)
    metadata = json.loads(state.get("metadata_json") or "{}")
    if payload.get("task_id"):
        metadata["task_id"] = payload["task_id"]
    metadata.update(evidence_class="provider_observed", counts_toward_verified_gmv=False)
    received = int(state.get("orders_received") or 0)
    matched = int(state.get("orders_matched") or 0)
    completed_pages = int(metadata.get("pages") or 0)
    cursor = metadata.get("resume_cursor")
    if state["status"] in {"completed", "partial", "duplicate"} or metadata.get("has_next_page") is False:
        return {"ok": state["status"] == "completed", "status": state["status"], "orders_received": received,
                "resume_cursor": cursor, "resume_sync_uid": uid if state["status"] != "completed" else None,
                "reason": state.get("error_message") or "", "idempotent": True,
                "counts_toward_verified_gmv": False}
    seen = {cursor} if cursor else set()
    try:
        creds = sync.configured_credentials()
        if creds["shop_domain"] != metadata.get("shop_domain"):
            raise ValueError("connected_shop_changed")
        bounds = sync.window(metadata.get("window") or {})
        metadata.setdefault("created_after", sync.iso(sync.now() - sync.timedelta(days=60)))
        query = f"updated_at:>='{bounds['start_at']}' updated_at:<='{bounds['end_at']}' created_at:>='{metadata['created_after']}'"
        sync.save_state(uid, "running", metadata, received=received, matched=matched)
        for page_index in range(completed_pages, sync.MAX_PAGES):
            if time.monotonic() >= deadline:
                raise RuntimeError("sync_deadline_exceeded")
            result = shopify_connect.post_graphql(ORDER_QUERY, {"first": sync.PAGE_SIZE, "after": cursor, "query": query}, timeout_seconds=deadline - time.monotonic())
            # Validate a whole page before writing it; the checkpoint moves only
            # after every idempotent upsert, so interruption safely replays the page.
            page_info, prepared = _prepare_page(result, creds["shop_domain"], seen)
            for item in prepared:
                attribution.create_attribution(item, ingest_class="provider_observed")
            received += len(prepared)
            matched += sum(bool(item.get("project_id")) for item in prepared)
            cursor = page_info.get("endCursor") if page_info["hasNextPage"] else None
            metadata.update(resume_cursor=cursor, pages=page_index + 1, has_next_page=page_info["hasNextPage"])
            sync.save_state(uid, "running", metadata, received=received, matched=matched)
            if time.monotonic() >= deadline:
                metadata["deadline_exceeded"] = True
                raise RuntimeError("sync_deadline_exceeded")
            if not page_info["hasNextPage"]:
                status = "completed"
                break
            seen.add(cursor)
        else:
            status = "partial"
        sync.save_state(uid, status, metadata, received=received, matched=matched)
        return {"ok": status == "completed", "status": status, "orders_received": received,
                "resume_cursor": cursor, "resume_sync_uid": uid if status == "partial" else None,
                "counts_toward_verified_gmv": False}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "shopify_sync_persistence_failed"
        sync.save_state(uid, "failed", metadata, received=received, matched=matched, error=reason)
        return {"ok": False, "status": "failed", "reason": reason[:160], "orders_received": received,
                "resume_cursor": metadata.get("resume_cursor"), "resume_sync_uid": uid}
