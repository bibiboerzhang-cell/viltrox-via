"""
services/ingestion/webhooks.py — webhook auth and provider-specific payload normalization
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse
from typing import Any

from starlette.datastructures import Headers, QueryParams

from app.core.config import (
    META_APP_SECRET_PREVIOUS,
    META_APP_SECRET,
    META_WEBHOOK_VERIFY_TOKEN_PREVIOUS,
    META_WEBHOOK_VERIFY_TOKEN,
    PLATFORM_INGEST_SHARED_SECRET_PREVIOUS,
    PLATFORM_INGEST_SHARED_SECRET,
    SHOPIFY_WEBHOOK_SECRET_PREVIOUS,
    SHOPIFY_WEBHOOK_SECRET,
    TIKTOK_WEBHOOK_SECRET_PREVIOUS,
    TIKTOK_WEBHOOK_SECRET,
)


LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost"}
SUPPORTED_WEBHOOK_PLATFORMS = {"facebook", "instagram", "tiktok", "shopify"}
SCENE_KEYWORDS = {
    "studio": "studio",
    "outdoor": "outdoor",
    "travel": "travel",
    "portrait": "portrait",
    "wedding": "wedding",
    "product": "product",
    "review": "review",
    "tutorial": "tutorial",
    "cinematic": "cinematic",
    "fashion": "fashion",
    "sports": "sports",
    "food": "food",
    "interview": "interview",
    "bts": "behind_the_scenes",
    "behind the scenes": "behind_the_scenes",
}


def _safe_compare(left: str, right: str) -> bool:
    return bool(left and right) and hmac.compare_digest(left, right)


def _safe_compare_any(value: str, candidates: list[str]) -> bool:
    candidate_value = str(value or "").strip()
    if not candidate_value:
        return False
    for item in candidates:
        if _safe_compare(candidate_value, str(item or "").strip()):
            return True
    return False


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pick_first(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _selected_headers(headers: Headers) -> dict[str, str]:
    keep = (
        "x-shopify-topic",
        "x-shopify-shop-domain",
        "x-shopify-order-id",
        "x-meta-signature",
        "x-hub-signature-256",
        "x-tiktok-event-type",
        "x-tiktok-webhook-secret",
        "user-agent",
    )
    return {name: headers.get(name, "") for name in keep}


def _extract_external_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("external_id", "id", "order_id", "media_id", "post_id", "video_id", "aweme_id"):
            value = payload.get(key)
            if value:
                return str(value)
        entry = payload.get("entry")
        if isinstance(entry, list) and entry:
            first = entry[0]
            if isinstance(first, dict):
                for key in ("id", "time"):
                    value = first.get(key)
                    if value:
                        return str(value)
        customer = payload.get("customer")
        if isinstance(customer, dict) and customer.get("id"):
            return str(customer["id"])
    return ""


def _extract_region_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("region_code", "country_code", "country", "region"):
        value = payload.get(key)
        if value:
            return str(value).strip().upper()[:8]
    for nested_key in ("shipping_address", "billing_address", "address", "customer", "user"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in ("country_code", "country", "region"):
                value = nested.get(key)
                if value:
                    return str(value).strip().upper()[:8]
    return ""


def _extract_creator_handle(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("creator_handle", "handle", "username", "owner_username", "creator_code", "unique_id"):
        value = payload.get(key)
        if value:
            return str(value).strip()
    for nested_key in ("customer", "from", "user", "owner"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in ("email", "first_name", "username", "unique_id"):
                value = nested.get(key)
                if value:
                    return str(value).strip()
    return ""


def _extract_product_fields(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    for line_key in ("line_items", "items", "products"):
        line_items = payload.get(line_key)
        if isinstance(line_items, list) and line_items:
            first = _as_dict(line_items[0])
            product_key = _pick_first(first.get("sku"), first.get("product_id"), first.get("variant_id"))
            product_label = _pick_first(first.get("title"), first.get("name"), first.get("product_title"), product_key)
            return product_key, product_label
    product_key = _pick_first(payload.get("product_key"), payload.get("sku"))
    product_label = _pick_first(payload.get("product_label"), payload.get("product_name"), product_key)
    return product_key, product_label


def _extract_text_block(payload: dict[str, Any]) -> str:
    nested_media = _as_dict(payload.get("media"))
    parts = [
        payload.get("caption"),
        payload.get("text"),
        payload.get("message"),
        payload.get("description"),
        payload.get("title"),
        payload.get("body"),
        nested_media.get("caption"),
        nested_media.get("title"),
    ]
    return " | ".join(str(part).strip() for part in parts if str(part or "").strip())[:1000]


def _extract_scene_tags(text: str) -> list[str]:
    lowered = str(text or "").lower()
    tags: list[str] = []
    for keyword, label in SCENE_KEYWORDS.items():
        if keyword in lowered and label not in tags:
            tags.append(label)
    return tags


def _extract_metrics_map(payload: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    candidates = (
        "views",
        "view_count",
        "video_views",
        "plays",
        "likes",
        "like_count",
        "comments",
        "comment_count",
        "shares",
        "share_count",
        "favorites",
        "save_count",
        "impressions",
        "reach",
        "engagement",
        "watch_time",
    )
    nested_sources = (
        payload,
        _as_dict(payload.get("metrics")),
        _as_dict(payload.get("insights")),
        _as_dict(payload.get("stats")),
    )
    for source in nested_sources:
        for key in candidates:
            if key in source and source.get(key) not in ("", None):
                metrics[key] = source.get(key)
    return metrics


def _extract_ref_code(urls: list[str], note_attributes: list[dict[str, Any]] | None = None) -> str:
    param_names = ("ref", "creator", "creator_id", "creator_code", "affiliate", "aff", "discount", "code")
    for raw_url in urls:
        url = str(raw_url or "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        query = parse_qs(parsed.query or "")
        for name in param_names:
            values = query.get(name) or []
            for value in values:
                if str(value).strip():
                    return str(value).strip()[:120]
    for attr in note_attributes or []:
        item = _as_dict(attr)
        key = str(item.get("name") or item.get("key") or "").strip().lower()
        if key in param_names:
            value = _pick_first(item.get("value"))
            if value:
                return value[:120]
    return ""


def verify_meta_challenge(query: QueryParams) -> str:
    mode = str(query.get("hub.mode") or "").strip()
    token = str(query.get("hub.verify_token") or "").strip()
    challenge = str(query.get("hub.challenge") or "").strip()
    if mode != "subscribe" or not challenge:
        raise ValueError("invalid meta challenge")
    valid_tokens = [META_WEBHOOK_VERIFY_TOKEN, *META_WEBHOOK_VERIFY_TOKEN_PREVIOUS]
    if META_WEBHOOK_VERIFY_TOKEN and not _safe_compare_any(token, valid_tokens):
        raise PermissionError("invalid meta verify token")
    return challenge


def verify_webhook_request(platform: str, headers: Headers, raw_body: bytes, client_host: str = "") -> str:
    client = (client_host or "").strip().lower()

    # Shopify money events are a stricter trust boundary than the generic
    # ingestion adapters below.  A fleet shared secret or a loopback client
    # proves only that the request reached our infrastructure; neither proves
    # that Shopify emitted the order/refund.  Always require Shopify's native
    # HMAC before a caller can reach the financial attribution adapter.
    if platform == "shopify":
        try:
            from app.domains.commerce import shopify_connect

            shopify_secret = str(shopify_connect.get_credentials().get("webhook_secret") or "").strip()
        except Exception:
            shopify_secret = str(SHOPIFY_WEBHOOK_SECRET or "").strip()
        if not shopify_secret:
            raise RuntimeError("shopify webhook secret not configured")
        provided = headers.get("x-shopify-hmac-sha256", "").strip()
        valid_shopify = [shopify_secret, *SHOPIFY_WEBHOOK_SECRET_PREVIOUS]
        for candidate_secret in valid_shopify:
            digest = base64.b64encode(
                hmac.new(
                    candidate_secret.encode("utf-8"),
                    raw_body,
                    hashlib.sha256,
                ).digest()
            ).decode("utf-8")
            if _safe_compare(provided, digest):
                return "shopify-hmac"
        raise PermissionError("invalid shopify hmac")

    shared = headers.get("x-viltrox-ingest-secret", "") or headers.get("x-ingest-secret", "")
    # A configured shared secret must not shadow a provider's native signature.
    # Only select this mode when the caller actually sends the shared header.
    if PLATFORM_INGEST_SHARED_SECRET and shared:
        valid_shared = [PLATFORM_INGEST_SHARED_SECRET, *PLATFORM_INGEST_SHARED_SECRET_PREVIOUS]
        if _safe_compare_any(shared, valid_shared):
            return "shared-secret"
        raise PermissionError("invalid shared secret")

    if platform in {"facebook", "instagram"} and META_APP_SECRET:
        provided = headers.get("x-hub-signature-256", "").strip()
        valid_meta_secrets = [META_APP_SECRET, *META_APP_SECRET_PREVIOUS]
        for candidate_secret in valid_meta_secrets:
            digest = "sha256=" + hmac.new(
                candidate_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
            if _safe_compare(provided, digest):
                return "meta-hmac"
        raise PermissionError("invalid meta signature")

    if platform == "tiktok" and TIKTOK_WEBHOOK_SECRET:
        provided = headers.get("x-tiktok-webhook-secret", "").strip()
        valid_tiktok = [TIKTOK_WEBHOOK_SECRET, *TIKTOK_WEBHOOK_SECRET_PREVIOUS]
        if _safe_compare_any(provided, valid_tiktok):
            return "tiktok-secret"
        raise PermissionError("invalid tiktok webhook secret")

    if client in LOCAL_CLIENTS:
        return "local-dev"

    raise RuntimeError("webhook secret not configured")


def parse_request_body(raw_body: bytes, content_type: str) -> Any:
    body = raw_body.decode("utf-8", errors="ignore")
    if "application/json" in content_type:
        try:
            return json.loads(body or "{}")
        except Exception:
            return {"raw_body": body}
    if "application/x-www-form-urlencoded" in content_type:
        return {key: values[0] if len(values) == 1 else values for key, values in parse_qs(body, keep_blank_values=True).items()}
    return {"raw_body": body}


def _build_payload(
    *,
    platform: str,
    event_type: str,
    entity_type: str,
    external_id: str,
    creator_handle: str = "",
    region_code: str = "",
    product_key: str = "",
    product_label: str = "",
    source_url: str = "",
    observed_at: str = "",
    summary: str = "",
    note: str = "",
    metrics: dict[str, Any] | None = None,
    scene_tags: list[str] | None = None,
    feature_tags: list[str] | None = None,
    alias_terms: list[str] | None = None,
    dedupe_key: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_type = (event_type or f"{platform}_webhook").strip().lower()
    entity_type = (entity_type or "content").strip().lower()
    external_id = str(external_id or "").strip()
    creator_handle = str(creator_handle or "").strip()
    region_code = str(region_code or "").strip().upper()[:8]
    source_url = str(source_url or "").strip()
    dedupe_key = str(dedupe_key or f"{platform}:{event_type}:{entity_type}:{external_id or creator_handle or source_url}").strip()
    summary = str(summary or f"{platform} webhook {event_type}").strip()[:500]
    alias_terms = [term for term in (alias_terms or []) if str(term or "").strip()]
    feature_tags = [term for term in (feature_tags or []) if str(term or "").strip()]
    scene_tags = [term for term in (scene_tags or []) if str(term or "").strip()]
    metrics = metrics or {}
    payload = payload or {}

    return {
        "external_id": external_id,
        "event_type": event_type,
        "entity_type": entity_type,
        "creator_handle": creator_handle,
        "region_code": region_code,
        "product_key": product_key,
        "product_label": product_label,
        "source_url": source_url,
        "observed_at": observed_at,
        "summary": summary,
        "note": str(note or "").strip()[:1000],
        "metrics": metrics,
        "scene_tags": scene_tags,
        "feature_tags": feature_tags,
        "alias_terms": alias_terms,
        "dedupe_key": dedupe_key,
        "payload": payload,
    }


def _build_meta_webhook_payloads(platform: str, headers: Headers, query: QueryParams, body: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    object_type = _pick_first(body.get("object"), platform)
    entries = _as_list(body.get("entry")) or [body]

    for entry in entries:
        entry_dict = _as_dict(entry)
        entry_id = _pick_first(entry_dict.get("id"), body.get("id"))
        entry_time = _pick_first(entry_dict.get("time"), query.get("occurred_at"))

        for change in _as_list(entry_dict.get("changes")):
            change_dict = _as_dict(change)
            value = _as_dict(change_dict.get("value"))
            media = _as_dict(value.get("media"))
            text_block = _extract_text_block(value)
            source_url = _pick_first(
                value.get("permalink_url"),
                value.get("link"),
                media.get("permalink"),
                media.get("url"),
                query.get("source_url"),
            )
            creator_handle = _pick_first(
                _as_dict(value.get("from")).get("username"),
                value.get("username"),
                media.get("username"),
                _extract_creator_handle(value),
            )
            event_type = _pick_first(change_dict.get("field"), value.get("verb"), object_type, "meta_webhook")
            entity_type = _pick_first(value.get("item"), value.get("media_product_type"), platform)
            external_id = _pick_first(
                media.get("id"),
                value.get("post_id"),
                value.get("comment_id"),
                value.get("media_id"),
                value.get("id"),
                entry_id,
            )
            metrics = _extract_metrics_map(value)
            feature_tags = [tag for tag in {str(entity_type).lower(), str(value.get("media_product_type") or "").lower()} if tag]
            payloads.append(
                _build_payload(
                    platform=platform,
                    event_type=event_type,
                    entity_type=entity_type,
                    external_id=external_id,
                    creator_handle=creator_handle,
                    region_code=_pick_first(_extract_region_code(value), query.get("region_code")),
                    source_url=source_url,
                    observed_at=entry_time,
                    summary=f"{platform} {event_type} | {external_id or creator_handle or source_url}"[:500],
                    note=text_block,
                    metrics=metrics,
                    scene_tags=_extract_scene_tags(text_block),
                    feature_tags=feature_tags,
                    alias_terms=[creator_handle] if creator_handle else [],
                    dedupe_key=f"{platform}:{event_type}:{external_id or creator_handle or entry_id}:{entry_time or source_url}",
                    payload={
                        "headers": _selected_headers(headers),
                        "query": dict(query),
                        "body": body,
                        "entry": entry_dict,
                        "event": change_dict,
                    },
                )
            )

        for message in _as_list(entry_dict.get("messaging")):
            message_dict = _as_dict(message)
            message_body = _as_dict(message_dict.get("message"))
            postback = _as_dict(message_dict.get("postback"))
            sender = _as_dict(message_dict.get("sender"))
            text_block = _pick_first(message_body.get("text"), postback.get("title"), postback.get("payload"))
            creator_handle = _pick_first(sender.get("id"), sender.get("username"))
            external_id = _pick_first(message_body.get("mid"), postback.get("mid"), entry_id)
            event_type = "messaging"
            entity_type = "message"
            payloads.append(
                _build_payload(
                    platform=platform,
                    event_type=event_type,
                    entity_type=entity_type,
                    external_id=external_id,
                    creator_handle=creator_handle,
                    region_code=_pick_first(query.get("region_code")),
                    source_url=_pick_first(query.get("source_url")),
                    observed_at=_pick_first(message_dict.get("timestamp"), entry_time),
                    summary=f"{platform} messaging | {external_id or creator_handle}"[:500],
                    note=text_block,
                    scene_tags=_extract_scene_tags(text_block),
                    feature_tags=["messaging"],
                    alias_terms=[creator_handle] if creator_handle else [],
                    dedupe_key=f"{platform}:messaging:{external_id or creator_handle}:{message_dict.get('timestamp') or entry_time}",
                    payload={
                        "headers": _selected_headers(headers),
                        "query": dict(query),
                        "body": body,
                        "entry": entry_dict,
                        "event": message_dict,
                    },
                )
            )

    if payloads:
        return payloads

    generic_text = _extract_text_block(body)
    external_id = _extract_external_id(body)
    creator_handle = _extract_creator_handle(body)
    return [
        _build_payload(
            platform=platform,
            event_type=_pick_first(body.get("object"), "meta_webhook"),
            entity_type=platform,
            external_id=external_id,
            creator_handle=creator_handle,
            region_code=_pick_first(_extract_region_code(body), query.get("region_code")),
            source_url=_pick_first(body.get("permalink_url"), body.get("link"), query.get("source_url")),
            observed_at=_pick_first(body.get("time"), query.get("occurred_at")),
            summary=f"{platform} webhook {_pick_first(body.get('object'), 'event')} | {external_id or creator_handle}"[:500],
            note=generic_text,
            metrics=_extract_metrics_map(body),
            scene_tags=_extract_scene_tags(generic_text),
            alias_terms=[creator_handle] if creator_handle else [],
            payload={"headers": _selected_headers(headers), "query": dict(query), "body": body},
        )
    ]


def _build_tiktok_webhook_payloads(headers: Headers, query: QueryParams, body: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    events = _as_list(body.get("events"))
    if not events:
        data = body.get("data")
        if isinstance(data, list):
            events = data
    if not events:
        events = [body]

    for raw_event in events:
        event = _as_dict(raw_event)
        data = _as_dict(event.get("data")) or event
        user = _as_dict(data.get("user")) or _as_dict(event.get("user"))
        video = _as_dict(data.get("video")) or _as_dict(event.get("video"))
        text_block = _pick_first(data.get("caption"), data.get("desc"), video.get("title"), data.get("title"))
        source_url = _pick_first(video.get("share_url"), data.get("share_url"), data.get("permalink_url"), query.get("source_url"))
        creator_handle = _pick_first(user.get("unique_id"), user.get("username"), data.get("creator_handle"), data.get("username"))
        external_id = _pick_first(video.get("id"), data.get("aweme_id"), data.get("video_id"), event.get("event_id"), body.get("id"))
        event_type = _pick_first(event.get("type"), event.get("event"), body.get("type"), body.get("event"), headers.get("x-tiktok-event-type"), "tiktok_webhook")
        entity_type = _pick_first(event.get("entity_type"), data.get("entity_type"), "video")
        metrics = _extract_metrics_map(video) or _extract_metrics_map(data)
        feature_tags = [tag for tag in {str(entity_type).lower(), str(video.get("share_type") or "").lower()} if tag]
        payloads.append(
            _build_payload(
                platform="tiktok",
                event_type=event_type,
                entity_type=entity_type,
                external_id=external_id,
                creator_handle=creator_handle,
                region_code=_pick_first(_extract_region_code(data), user.get("region"), query.get("region_code")),
                source_url=source_url,
                observed_at=_pick_first(data.get("create_time"), event.get("timestamp"), query.get("occurred_at")),
                summary=f"tiktok {event_type} | {external_id or creator_handle or source_url}"[:500],
                note=text_block,
                metrics=metrics,
                scene_tags=_extract_scene_tags(text_block),
                feature_tags=feature_tags,
                alias_terms=[creator_handle] if creator_handle else [],
                dedupe_key=f"tiktok:{event_type}:{external_id or creator_handle}:{_pick_first(data.get('create_time'), event.get('timestamp'), source_url)}",
                payload={
                    "headers": _selected_headers(headers),
                    "query": dict(query),
                    "body": body,
                    "event": event,
                },
            )
        )

    return payloads


def _build_shopify_webhook_payloads(headers: Headers, query: QueryParams, body: dict[str, Any]) -> list[dict[str, Any]]:
    line_items = [_as_dict(item) for item in _as_list(body.get("line_items"))]
    topic = (headers.get("x-shopify-topic", "") or "shopify_webhook").replace("/", "_").strip().lower()
    product_key, product_label = _extract_product_fields(body)
    customer = _as_dict(body.get("customer"))
    region_code = _pick_first(_extract_region_code(body), query.get("region_code"))
    landing_site = _pick_first(body.get("landing_site"), body.get("order_status_url"), body.get("referring_site"), query.get("source_url"))
    note_attributes = [_as_dict(item) for item in _as_list(body.get("note_attributes"))]
    ref_code = _extract_ref_code(
        [landing_site, body.get("referring_site"), body.get("order_status_url"), query.get("source_url")],
        note_attributes=note_attributes,
    )
    creator_handle = _pick_first(ref_code, customer.get("email"), customer.get("id"))
    alias_terms = []
    for item in line_items:
        for candidate in (item.get("sku"), item.get("vendor"), item.get("title"), item.get("product_type")):
            text = str(candidate or "").strip()
            if text and text not in alias_terms:
                alias_terms.append(text)
    quantity_total = sum(_safe_int(item.get("quantity")) for item in line_items)
    metrics = {
        "order_total": _safe_float(body.get("total_price") or body.get("current_total_price")),
        "subtotal_price": _safe_float(body.get("subtotal_price") or body.get("current_subtotal_price")),
        "discount_total": _safe_float(body.get("total_discounts") or body.get("current_total_discounts")),
        "tax_total": _safe_float(body.get("total_tax") or body.get("current_total_tax")),
        "items_count": len(line_items),
        "quantity_total": quantity_total,
    }
    summary_bits = [f"shopify {topic}"]
    external_id = _pick_first(body.get("id"), body.get("order_number"), headers.get("x-shopify-order-id"))
    if external_id:
        summary_bits.append(str(external_id))
    if ref_code:
        summary_bits.append(f"ref={ref_code}")
    if product_label:
        summary_bits.append(product_label)
    note_parts = []
    if landing_site:
        note_parts.append(f"landing={landing_site}")
    if ref_code:
        note_parts.append(f"ref={ref_code}")
    feature_tags = [tag for tag in {topic.replace("orders_", ""), "conversion"} if tag]

    return [
        _build_payload(
            platform="shopify",
            event_type=topic,
            entity_type="order",
            external_id=external_id,
            creator_handle=creator_handle,
            region_code=region_code,
            product_key=product_key,
            product_label=product_label,
            source_url=landing_site,
            observed_at=_pick_first(body.get("processed_at"), body.get("updated_at"), body.get("created_at"), query.get("occurred_at")),
            summary=" | ".join(summary_bits)[:500],
            note=" | ".join(note_parts)[:1000],
            metrics=metrics,
            scene_tags=[],
            feature_tags=feature_tags,
            alias_terms=alias_terms,
            dedupe_key=f"shopify:{topic}:{external_id}:{_pick_first(body.get('updated_at'), body.get('processed_at'), body.get('created_at'))}",
            payload={
                "headers": _selected_headers(headers),
                "query": dict(query),
                "body": body,
                "ref_code": ref_code,
            },
        )
    ]


def build_webhook_ingest_payloads(
    *,
    platform: str,
    headers: Headers,
    query: QueryParams,
    body: Any,
) -> list[dict[str, Any]]:
    body_dict = _as_dict(body)

    if platform in {"facebook", "instagram"}:
        return _build_meta_webhook_payloads(platform, headers, query, body_dict)
    if platform == "tiktok":
        return _build_tiktok_webhook_payloads(headers, query, body_dict)
    if platform == "shopify":
        return _build_shopify_webhook_payloads(headers, query, body_dict)

    event_type = ""
    entity_type = ""
    if platform == "shopify":
        event_type = (headers.get("x-shopify-topic", "") or "shopify_webhook").replace("/", "_").strip().lower()
        entity_type = "order"
    elif platform in {"facebook", "instagram"}:
        event_type = _pick_first(body_dict.get("object"), "meta_webhook").strip().lower()
        entity_type = platform
    elif platform == "tiktok":
        event_type = _pick_first(body_dict.get("type"), "tiktok_webhook").strip().lower()
        entity_type = _pick_first(body_dict.get("entity_type"), "content").strip().lower()

    external_id = _extract_external_id(body_dict)
    creator_handle = _extract_creator_handle(body_dict)
    region_code = _extract_region_code(body_dict)
    product_key, product_label = _extract_product_fields(body_dict)

    summary = f"{platform} webhook {event_type or 'event'}"
    if external_id:
        summary += f" | {external_id}"

    return [
        _build_payload(
            platform=platform,
            event_type=event_type or f"{platform}_webhook",
            entity_type=entity_type or "content",
            external_id=external_id,
            creator_handle=creator_handle,
            region_code=region_code,
            product_key=product_key,
            product_label=product_label,
            source_url=str(query.get("source_url") or ""),
            summary=summary[:500],
            metrics=_extract_metrics_map(body_dict),
            scene_tags=_extract_scene_tags(_extract_text_block(body_dict)),
            payload={
                "headers": _selected_headers(headers),
                "query": dict(query),
                "body": body_dict,
            },
        )
    ]


def build_webhook_ingest_payload(
    *,
    platform: str,
    headers: Headers,
    query: QueryParams,
    body: Any,
) -> dict[str, Any]:
    payloads = build_webhook_ingest_payloads(
        platform=platform,
        headers=headers,
        query=query,
        body=body,
    )
    return payloads[0] if payloads else {}
