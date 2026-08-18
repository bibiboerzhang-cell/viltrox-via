"""Pure serialization/normalization helpers for KOL search sessions.

Behavior-preserving move out of ``search_sessions.py``. These are all pure
functions (no DB access) covering JSON (de)serialization, value coercion,
status/query-type normalization, row→dict mappers, item counting, and flow
compaction. Re-exported by ``search_sessions`` to keep all call sites stable.

This module never writes ``viltrox_fit_score`` (no fit writes whatsoever).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from app.core.coerce import _loads, _text
from app.domains.kol.search_sessions_schema import (
    ITEM_STATUSES,
    SESSION_QUERY_TYPES,
    SESSION_STATUSES,
)


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value or {}), ensure_ascii=False, default=str)


def _staff_user_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("user_id", "id", "staff_id"):
        parsed = _int_or_none(staff.get(key))
        if parsed:
            return parsed
    return None


def _normalize_query_type(value: Any) -> str:
    text = _text(value).lower()
    return text if text in SESSION_QUERY_TYPES else "unknown"


def _normalize_status(value: Any, *, item: bool = False) -> str:
    text = _text(value).lower()
    allowed = ITEM_STATUSES if item else SESSION_STATUSES
    if text in allowed:
        return text
    if text in {"dry_run_ready", "ready_to_execute", "resolved", "needs_video_resolution"}:
        return "identified" if item else "ready"
    if text in {"done", "completed"}:
        return "ready"
    if text in {"would_create", "would_reuse", "created", "reused"}:
        return "matched" if item else "ready"
    if text in {"error", "crawl_failed", "profile_crawl_failed", "creator_unresolved"}:
        return "failed"
    if text in {"unsupported_platform", "skipped_tiktok_video_resolver_known_issue"}:
        return "skipped" if item else "partial"
    if text in {"ai_disabled", "not_requested"}:
        return "skipped" if item else "ready"
    # 视频 URL dry-run 的两个真实中间态:URL 已识别、创作者留待后台解析——
    # 诚实映射为「已识别」,不再落成 unknown(unknown 会让历史回放误判成已执行)。
    if text in {"provider_refresh_pending", "creator_not_in_pool"}:
        return "identified" if item else "ready"
    # 官方自有账号的视频:按设计不建档、不做深析,终态=跳过(非失败非排队)。
    if text == "official_channel_video":
        return "skipped" if item else "ready"
    # 中国平台视频:仅内容分析、不建档。分析终态=ready;dry-run 计划=已识别。
    if text == "cn_platform_video":
        return "ready"
    if text == "cn_platform_video_planned":
        return "identified" if item else "ready"
    return "unknown" if item else "planned"


def _row_to_session(row: Any) -> dict[str, Any]:
    item = dict(row)
    query_text = _sanitize_session_value(_text(item.get("query_text"))[:500])
    source = _text(item.get("source"))[:160]
    archive_reason = _sanitize_session_value(_text(item.get("archive_reason"))[:160])
    approved_ids: list[int] = []
    for raw_id in _list(_loads(item.get("approved_kol_ids"), [])):
        approved_id = _int_or_none(raw_id)
        if approved_id and approved_id not in approved_ids:
            approved_ids.append(approved_id)
    return _jsonable(
        {
            "id": item.get("id"),
            "query_text": query_text if isinstance(query_text, str) else "",
            "query_type": item.get("query_type"),
            "source": source if _SAFE_PUBLIC_CODE.fullmatch(source) else "search_session",
            "status": item.get("status"),
            "created_by": item.get("created_by"),
            "input_payload": _sanitize_session_input_payload(
                _loads(item.get("input_payload_json"), {})
            ),
            "result_summary": _sanitize_session_payload(
                _loads(item.get("result_summary_json"), {})
            ),
            # R1:人审锁定的候选 kol_pool_id(迁移 176;旧行/缺列回退 [])。
            "approved_kol_ids": approved_ids,
            "archived_at": item.get("archived_at"),
            "archived_by": item.get("archived_by"),
            "archive_reason": archive_reason if isinstance(archive_reason, str) else "",
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
    )


def _row_to_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    item_type = item.get("item_type")
    dedupe_key = _sanitize_session_value(_text(item.get("dedupe_key"))[:500])
    source_url = _public_session_item_source_url(
        item.get("source_url"), item_type=item_type
    )
    payload = _loads(item.get("payload_json"), {})
    payload = dict(payload) if isinstance(payload, dict) else {}
    if item_type in {
        "existing_kol",
        "new_creator",
        "recall_candidate",
        "online_qualified_candidate",
    }:
        # These item types use source/profile/channel URLs as creator identity
        # locators.  Historical rows may predate the write-side guard, so the
        # read mapper must defensively remove DM/contact routes too.  Video URL
        # item types are deliberately excluded.
        from app.domains.kol.contact_system import (
            project_public_profile_url as project_identity_url,
        )

        source_url = project_identity_url(source_url)
        identity_aliases = {
            "source_url",
            "sourceUrl",
            "profile_url",
            "profileUrl",
            "channel_url",
            "channelUrl",
        }

        def project_identity_aliases(value: Any) -> Any:
            if isinstance(value, list):
                return [project_identity_aliases(entry) for entry in value]
            if not isinstance(value, dict):
                return value
            projected: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                key = str(raw_key)
                projected[key] = (
                    project_identity_url(raw_value)
                    if key in identity_aliases
                    else project_identity_aliases(raw_value)
                )
            return projected

        payload = project_identity_aliases(payload)
    payload = _sanitize_session_payload(payload)
    return _jsonable(
        {
            "id": item.get("id"),
            "session_id": item.get("session_id"),
            "dedupe_key": dedupe_key if isinstance(dedupe_key, str) else "",
            "item_type": item_type,
            "status": item.get("status"),
            "stage": item.get("stage"),
            "rank": item.get("rank"),
            "score": item.get("score"),
            "kol_pool_id": item.get("kol_pool_id"),
            "evidence_id": item.get("evidence_id"),
            "job_id": item.get("job_id"),
            "source_url": source_url if isinstance(source_url, str) else "",
            # Read-time projection also protects history/get_session for legacy
            # rows written before the strict public profile DTO existed.
            "payload": payload,
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
    )


def _item_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    for item in items:
        status = _text(item.get("status")) or "unknown"
        stage = _text(item.get("stage")) or "identified"
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
    return {"by_status": by_status, "by_stage": by_stage}


_PUBLIC_PROFILE_DATA_FIELDS = (
    "platform",
    "handle",
    "display_name",
    "channel_name",
    "channel_id",
    "profile_url",
    "avatar_url",
    "followers",
    "subscriber_count",
    "posts_count",
    "bio",
    "last_video_at",
)
_PUBLIC_MEDIA_CACHE_FIELDS = (
    "status",
    "cached",
    "storage_backend",
    "reason",
    "error",
    "skip_reason",
    "retry_after_seconds",
    "updated_at",
)
_SAFE_PUBLIC_CODE = re.compile(r"^[a-zA-Z0-9_.:-]{1,160}$")
_EMAIL_IN_TEXT = re.compile(
    r"(?<![\w.+-])[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+(?![\w.-])"
)
_PHONE_LIKE_IN_TEXT = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{5,}\d)(?!\w)")
_BARE_PHONE_IN_TEXT = re.compile(r"(?<![\w-])(\d{7,15})(?![\w-])")
_LABELED_PHONE_IN_TEXT = re.compile(
    r"\b(?:phone|mobile|telephone|tel|call)\s*(?:number|no\.?|#)?\s*[:：]?\s*"
    r"(\+?\d[\d\s().-]{5,}\d)(?!\w)",
    re.IGNORECASE,
)
_CONTACT_APP_IN_TEXT = re.compile(
    r"\b(?:whats?app|wechat|weixin|signal|discord|telegram)\b|"
    r"(?:^|[\s|/,:;])(?:line|messenger)(?:\s*(?:id|me|contact|dm|message|[:@]))|"
    r"\bmessenger\s+(?!app\b)@?[a-z0-9_.-]{3,}\b|"
    r"\b(?:instagram|tiktok|twitter|facebook|x)\s*(?:dm|contact|message)"
    r"(?:\s*(?:id|handle))?\s*[:@]?|"
    r"\b(?:dm|message|contact)(?:\s+me)?(?:\s+(?:on|via))?\s+"
    r"(?:instagram|tiktok|twitter|facebook|x|messenger)\b|"
    r"\bsend\s+me\s+a?\s*(?:dm|message)(?:\s+(?:on|via))?\s+"
    r"(?:instagram|tiktok|twitter|facebook|x|messenger)\b",
    re.IGNORECASE,
)
_CONTACT_ROUTE_IN_TEXT = re.compile(
    r"(?:mailto:|tel:|https?://(?:www\.)?(?:wa\.me|api\.whatsapp\.com|"
    r"line\.me|signal\.me|discord\.gg|weixin\.qq\.com|t\.me|m\.me)(?:/|\b)|"
    r"https?://(?:www\.)?(?:instagram\.com/direct|(?:x|twitter)\.com/messages|"
    r"facebook\.com/messages|discord\.com/(?:invite|users|channels/@me))(?:/|\b))",
    re.IGNORECASE,
)
_SENSITIVE_SESSION_KEYS = {
    "email",
    "emails",
    "contact_email",
    "contact_emails",
    "business_email",
    "public_email",
    "manager_email",
    "phone",
    "phones",
    "contact_phone",
    "phone_number",
    "telephone",
    "tel",
    "mobile",
    "whatsapp",
    "whatsapp_id",
    "wechat",
    "wechat_id",
    "weixin",
    "line_id",
    "line",
    "signal",
    "signal_id",
    "discord",
    "discord_id",
    "telegram",
    "telegram_id",
    "contact_value",
    "contact_values",
    "contact_channels",
    "other_contacts",
    "other_contacts_json",
    "contacts",
    "contact_links",
    "contact_links_json",
    "contact_raw_json",
    "contact_url",
    "contact_urls",
    # Common camelCase aliases after lower-casing.
    "contactemail",
    "businessemail",
    "publicemail",
    "manageremail",
    "contactphone",
    "phonenumber",
    "whatsappid",
    "wechatid",
    "lineid",
    "signalid",
    "discordid",
    "telegramid",
    "contactvalue",
    "contactchannels",
    "othercontacts",
    "contactlinks",
    "contacturl",
}
_RAW_PROVIDER_KEYS = {
    "raw_platform_data",
    "raw_payload",
    "raw_response",
    "provider_payload",
    "provider_response",
    "provider_data",
    "crawler_payload",
    "apify_payload",
    "apify_output",
    "rawplatformdata",
    "rawpayload",
    "rawresponse",
    "providerpayload",
    "providerresponse",
}
_SENSITIVE_URL_QUERY_MARKERS = (
    "x-amz-credential",
    "x-amz-signature",
    "signature=",
    "credential=",
    "access_token=",
    "token=",
    "api_key=",
    "apikey=",
)


def _has_phone_like_text(value: str) -> bool:
    for match in _PHONE_LIKE_IN_TEXT.finditer(value):
        candidate = match.group(1).strip()
        if re.fullmatch(
            r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",
            candidate,
        ):
            continue
        digits = re.sub(r"\D", "", candidate)
        separators = sum(candidate.count(char) for char in " -().")
        if 7 <= len(digits) <= 15 and (candidate.startswith("+") or separators >= 2):
            return True
    return False


def _has_labeled_phone_text(value: str) -> bool:
    for match in _LABELED_PHONE_IN_TEXT.finditer(value):
        candidate = match.group(1).strip()
        if re.fullmatch(
            r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",
            candidate,
        ):
            continue
        digits = re.sub(r"\D", "", candidate)
        if 7 <= len(digits) <= 15:
            return True
    return False


def _has_bare_phone_text(value: str) -> bool:
    for match in _BARE_PHONE_IN_TEXT.finditer(value):
        digits = match.group(1)
        if len(digits) == 8 and re.fullmatch(r"(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])", digits):
            continue
        return True
    return False


def _contains_contact_route(value: Any) -> bool:
    text = _text(value)[:4096]
    if not text:
        return False
    return bool(
        _EMAIL_IN_TEXT.search(text)
        or _has_phone_like_text(text)
        or _has_labeled_phone_text(text)
        or _has_bare_phone_text(text)
        or _CONTACT_APP_IN_TEXT.search(text)
        or _CONTACT_ROUTE_IN_TEXT.search(text)
    )


def contains_contact_route(value: Any) -> bool:
    """Public contact-route guard, including percent-encoded canaries."""
    text = _text(value)[:4096]
    decoded = text
    for _ in range(2):
        decoded = unquote(decoded)
    return _contains_contact_route(text) or _contains_contact_route(decoded)


def project_public_profile_text(value: Any, *, limit: int = 240) -> str:
    """Project a non-contact public identity/display field."""
    text = unicodedata.normalize("NFKC", _text(value)).strip()[: max(1, int(limit))]
    if not text or re.search(r"[\x00-\x1f\x7f]", text) or contains_contact_route(text):
        return ""
    return text


def project_public_asset_url(value: Any) -> str:
    """Project a public http(s) asset URL without query credentials/contact routes."""
    raw = _text(value)[:2048]
    if not raw or contains_contact_route(raw):
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    decoded_query = unquote(parsed.query).lower()
    if any(marker in decoded_query for marker in _SENSITIVE_URL_QUERY_MARKERS):
        return ""
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))[:2048]


def project_public_profile_url(platform: Any, handle: Any, value: Any) -> str:
    """Accept only an account-home URL matching the projected platform+handle."""
    platform_key = project_public_profile_text(platform, limit=40).lower()
    handle_key = project_public_profile_text(handle, limit=160).lstrip("@").casefold()
    raw = project_public_asset_url(value)
    if not platform_key or not handle_key or not raw:
        return ""
    parsed = urlsplit(raw)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    candidate = ""
    if platform_key == "youtube" and host == "youtube.com":
        if parts and parts[0].startswith("@"):
            candidate = parts[0][1:]
        elif len(parts) == 2 and parts[0].lower() in {"channel", "c", "user"}:
            candidate = parts[1]
    elif platform_key == "instagram" and host == "instagram.com":
        if len(parts) == 1 and parts[0].lower() not in {"p", "reel", "direct", "messages"}:
            candidate = parts[0]
    elif platform_key == "tiktok" and host == "tiktok.com":
        if len(parts) == 1 and parts[0].startswith("@"):
            candidate = parts[0][1:]
    elif platform_key in {"x", "twitter"} and host in {"x.com", "twitter.com"}:
        if len(parts) == 1 and parts[0].lower() != "messages":
            candidate = parts[0]
    elif platform_key == "facebook" and host in {"facebook.com", "fb.com"}:
        if len(parts) == 1 and parts[0].lower() != "messages":
            candidate = parts[0]
    if unicodedata.normalize("NFKC", candidate).casefold().lstrip("@") != handle_key:
        return ""
    return raw


def _is_sensitive_session_key(value: Any) -> bool:
    key = _text(value).lower().replace("-", "_")
    if key in _SENSITIVE_SESSION_KEYS or key in _RAW_PROVIDER_KEYS:
        return True
    if key.endswith(("_email", "_emails", "_phone", "_phones", "_raw_json")):
        return True
    return any(
        marker in key
        for marker in ("provider_payload", "providerpayload", "raw_platform_data", "rawplatformdata")
    )


def _bounded_public_count(value: Any) -> int | None:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(count, 10_000))


def _compact_contact_preview(value: Any) -> dict[str, Any]:
    """Expose only non-identifying contact readiness and a bounded count."""

    preview = _dict(value)
    compact: dict[str, Any] = {}
    status = _text(preview.get("status")).lower()[:40]
    if status and _SAFE_PUBLIC_CODE.fullmatch(status):
        compact["status"] = status
    for source_key in ("channel_count", "contact_count", "count", "available_count"):
        count = _bounded_public_count(preview.get(source_key))
        if count is not None:
            compact["channel_count"] = count
            break
    return compact


_AUDIENCE_PREVIEW_METHODS = {
    "comments_language",
    "ensemble_v1",
    "manual_verified",
    "platform_declared",
}


def _compact_audience_preview(value: Any) -> dict[str, Any]:
    """Typed audience readiness only; arbitrary legacy strings never cross the session API."""
    preview = _dict(value)
    compact: dict[str, Any] = {}
    status = _text(preview.get("status")).lower()[:40]
    if status and _SAFE_PUBLIC_CODE.fullmatch(status):
        compact["status"] = status
    method = _text(preview.get("method")).lower()[:40]
    if method in _AUDIENCE_PREVIEW_METHODS:
        compact["method"] = method
    confidence = preview.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        numeric_confidence = float(confidence)
        if 0.0 <= numeric_confidence <= 1.0:
            compact["confidence"] = numeric_confidence
    raw_sample_size = preview.get("sample_size")
    sample_size = (
        _bounded_public_count(raw_sample_size)
        if isinstance(raw_sample_size, (int, float)) and not isinstance(raw_sample_size, bool)
        else None
    )
    if sample_size is not None:
        compact["sample_size"] = sample_size
    if isinstance(preview.get("async"), bool):
        compact["async"] = preview["async"]
    return compact


def _compact_enrichment_state(value: Any) -> dict[str, Any]:
    """Keep queue/progress metadata while excluding provider/contact records."""

    enrichment = _dict(value)
    compact: dict[str, Any] = {}
    for key in ("status", "queue_status"):
        status = _text(enrichment.get(key)).lower()[:40]
        if status and _SAFE_PUBLIC_CODE.fullmatch(status):
            compact[key] = status
    job_id = _int_or_none(enrichment.get("job_id"))
    if job_id:
        compact["job_id"] = job_id
    for key in (
        "channel_count",
        "contact_count",
        "count",
        "candidate_count",
        "created_count",
        "sample_size",
    ):
        count = _bounded_public_count(enrichment.get(key))
        if count is not None:
            compact[key] = count
    if isinstance(enrichment.get("async"), bool):
        compact["async"] = enrichment["async"]
    return compact


def _compact_public_profile_data(value: Any) -> dict[str, Any]:
    """Strict public profile DTO; contact-bearing public text is not retained."""

    profile = _dict(value)
    compact: dict[str, Any] = {}
    for key in _PUBLIC_PROFILE_DATA_FIELDS:
        item = profile.get(key)
        if item in (None, ""):
            continue
        if isinstance(item, str):
            limit = 1000 if key == "bio" else 2048 if key.endswith("_url") else 240
            item = item[:limit]
            if _contains_contact_route(item):
                continue
        elif not isinstance(item, (int, float, bool)):
            continue
        compact[key] = item
    return compact


def _sanitize_session_value(value: Any, *, field_name: str = "") -> Any:
    """Defense-in-depth projection for new writes and legacy session rows."""

    normalized_field = _text(field_name).lower().replace("-", "_")
    if normalized_field == "profile_data":
        return _compact_public_profile_data(value)
    if normalized_field == "contact_preview":
        return _compact_contact_preview(value)
    if normalized_field == "audience_preview":
        return _compact_audience_preview(value)
    if normalized_field in {"contact_enrichment", "audience_enrichment", "write_result"}:
        return _compact_enrichment_state(value)
    if normalized_field in {"representative_video_analysis", "history_video_evidence"}:
        return _sanitize_session_value(_compact_video_batch_flow(value))
    if isinstance(value, str) and _contains_contact_route(value):
        return None
    if isinstance(value, list):
        sanitized_items = [_sanitize_session_value(item) for item in value]
        return [item for item in sanitized_items if item is not None]
    if not isinstance(value, dict):
        return value
    compact: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        normalized_key = key.lower().replace("-", "_")
        if _is_sensitive_session_key(normalized_key):
            continue
        sanitized = _sanitize_session_value(raw_value, field_name=normalized_key)
        if sanitized is None:
            continue
        compact[key] = sanitized
    return compact


def _sanitize_session_payload(value: Any) -> dict[str, Any]:
    sanitized = _sanitize_session_value(_dict(value))
    return sanitized if isinstance(sanitized, dict) else {}


_SESSION_INPUT_FIELDS = {
    "advance_limit", "candidate_limit", "content_languages", "country", "create_session",
    "creator_quota", "dedupe", "defer_to_queue", "discovery_platforms", "execute",
    "exclude_chinese", "force_full_history", "handle", "handle_or_url", "include_discovery",
    "include_new_discovery", "input", "kol_types", "languages", "limit", "local_evaluation",
    "local_qualification_spec", "market", "max_posts", "mixed_policy", "mode",
    "new_discovery_limit", "new_discovery_per_platform_limit", "new_discovery_platforms",
    "platform", "platforms", "product_sku", "profile_types", "query_text", "queue_pipeline",
    "ratio_policy", "representative_video_limit", "reviewer_quota", "scan_account",
    "search_session_id", "session_id", "source", "task_id", "type_boost_enabled",
    "type_weight", "url", "vector_weight",
}


def _sanitize_session_input_payload(value: Any) -> dict[str, Any]:
    """Allowlist durable operator inputs, then apply the recursive contact scrubber."""
    raw = _dict(value)
    bounded = {key: raw[key] for key in _SESSION_INPUT_FIELDS if key in raw}
    return _sanitize_session_payload(bounded)


def _public_cached_video_url(value: Any) -> str:
    """Return a replay-safe cache URL, never a persisted presigned credential."""

    raw = _text(value)[:4096]
    if not raw:
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    lowered_query = parsed.query.lower()
    if any(marker in lowered_query for marker in _SENSITIVE_URL_QUERY_MARKERS):
        return ""
    # Tracking/cache-busting query strings are unnecessary for durable history.
    # YouTube's public ``v`` parameter is the media identity itself, however,
    # so retain that one bounded identifier while discarding every other key.
    safe_query = ""
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host == "youtube.com" and parsed.path.rstrip("/") == "/watch":
        video_id = next(
            (
                value
                for key, value in parse_qsl(parsed.query, keep_blank_values=False)
                if key == "v" and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value)
            ),
            "",
        )
        if video_id:
            safe_query = urlencode({"v": video_id})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, safe_query, ""))[:2048]


def _public_session_item_source_url(value: Any, *, item_type: Any) -> str:
    """Project a durable item locator without mistaking video IDs for phones."""

    raw = _text(value)[:2048]
    if not raw:
        return ""
    if _text(item_type).lower() != "url_video":
        sanitized = _sanitize_session_value(raw)
        return sanitized if isinstance(sanitized, str) else ""
    # Public video URLs routinely contain long numeric platform IDs. Preserve
    # those IDs, while still rejecting explicit contact routes and contact text.
    if (
        _EMAIL_IN_TEXT.search(raw)
        or _has_phone_like_text(raw)
        or _has_labeled_phone_text(raw)
        or _CONTACT_APP_IN_TEXT.search(raw)
        or _CONTACT_ROUTE_IN_TEXT.search(raw)
    ):
        return ""
    return _public_cached_video_url(raw)


def _public_cache_code(value: Any, *, fallback: str = "") -> str:
    text = _text(value)[:160]
    if not text:
        return ""
    return text if _SAFE_PUBLIC_CODE.fullmatch(text) else fallback


def _compact_public_media_cache(value: Any) -> dict[str, Any]:
    cache = _dict(value)
    compact: dict[str, Any] = {}
    for key in _PUBLIC_MEDIA_CACHE_FIELDS:
        item = cache.get(key)
        if item in (None, ""):
            continue
        if key in {"status", "storage_backend", "reason", "skip_reason"}:
            item = _public_cache_code(item)
        elif key == "error":
            item = _public_cache_code(item, fallback="media_cache_failed")
        elif not isinstance(item, (str, int, float, bool)):
            continue
        if item not in (None, ""):
            compact[key] = item
    return compact


def _compact_ai_analysis(value: Any) -> dict[str, Any]:
    analysis = _dict(value)
    return {
        key: analysis.get(key)
        for key in (
            "state",
            "reason",
            "gate_reason",
            "model_readiness_status",
            "provider_calls_allowed",
            "item_count",
            "not_requested_count",
        )
        if key in analysis
    }


def _compact_video_batch_flow(flow: Any) -> dict[str, Any]:
    if not isinstance(flow, dict):
        return {}
    keep = (
        "enabled",
        "status",
        "limit",
        "requested",
        "candidate_count",
        "skipped_by_incremental",
        "queued",
        "skipped",
        "errors",
        "materialized",
        "reused",
        "worker_touched",
        "viltrox_fit_score_changed_ids",
        "viltrox_fit_score_untouched",
    )
    compact = {key: flow.get(key) for key in keep if key in flow}
    ai_analysis = _compact_ai_analysis(flow.get("ai_analysis"))
    if ai_analysis:
        compact["ai_analysis"] = ai_analysis
    items: list[dict[str, Any]] = []
    for raw in _list(flow.get("items"))[:12]:
        if not isinstance(raw, dict):
            continue
        metadata = _dict(raw.get("metadata"))
        evidence = _dict(raw.get("evidence_result"))
        enqueue = _dict(raw.get("enqueue_result"))
        item = {
            "status": raw.get("status"),
            "error": raw.get("error"),
            "title": metadata.get("title") or raw.get("title"),
            "content_url": metadata.get("content_url") or raw.get("content_url"),
            "evidence_id": evidence.get("evidence_id") or raw.get("evidence_id"),
            "job_id": (
                _dict(enqueue.get("job")).get("id")
                or enqueue.get("job_id")
                or raw.get("job_id")
            ),
        }
        ai_analysis = _compact_ai_analysis(raw.get("ai_analysis") or enqueue.get("ai_analysis"))
        if ai_analysis:
            item["ai_analysis"] = ai_analysis
        cached_video_url = _public_cached_video_url(raw.get("cached_video_url"))
        if cached_video_url:
            item["cached_video_url"] = cached_video_url
        items.append(item)
    if items:
        compact["items"] = items
    return compact


def _compact_flow(flow: dict[str, Any]) -> dict[str, Any]:
    if not flow:
        return {}
    keep = (
        "status",
        "operation",
        "kol_pool_id",
        "evidence_id",
        "run_id",
        "worker_touched",
        "llm_calls_performed",
        "viltrox_fit_score_changed_ids",
        "viltrox_fit_score_untouched",
        "writes",
        "error",
        "elapsed_ms",
        # 中国平台「仅视频分析」终态的展示字段(不建档;设计定案 2026-07-20)。
        "cn_platform_video",
        "cn_platform_notice",
        "cn_analysis",
        "media_degraded",
        "media_degraded_reason",
        "message",
    )
    compact = {key: flow.get(key) for key in keep if key in flow}
    raw_progress = _dict(flow.get("resolution_progress"))
    if raw_progress:
        compact["resolution_progress"] = {
            "version": raw_progress.get("version"),
            "status": raw_progress.get("status"),
            "base_status": raw_progress.get("base_status"),
            "current_step": raw_progress.get("current_step"),
            "updated_at": raw_progress.get("updated_at"),
            "steps": [
                {
                    "key": item.get("key"),
                    "label": item.get("label"),
                    "status": item.get("status"),
                    "reason": _text(item.get("reason"))[:240],
                }
                for item in _list(raw_progress.get("steps"))[:4]
                if isinstance(item, dict)
            ],
        }
    ai_analysis = _compact_ai_analysis(
        flow.get("ai_analysis") or _dict(flow.get("enqueue_result")).get("ai_analysis")
    )
    if ai_analysis:
        compact["ai_analysis"] = ai_analysis
    cached_video_url = _public_cached_video_url(flow.get("cached_video_url"))
    if cached_video_url:
        compact["cached_video_url"] = cached_video_url
    profile_data = _compact_public_profile_data(flow.get("profile_data"))
    if profile_data:
        compact["profile_data"] = profile_data
    for status_key in ("media_cache_status", "video_cache_status", "cache_status"):
        status = _public_cache_code(flow.get(status_key))
        if status:
            compact[status_key] = status
    for error_key in ("media_cache_error", "video_cache_error", "cache_error"):
        error = _public_cache_code(flow.get(error_key), fallback="media_cache_failed")
        if error:
            compact[error_key] = error
    media_cache = _compact_public_media_cache(flow.get("media_cache") or flow.get("video_cache"))
    if media_cache:
        compact["media_cache"] = media_cache
    representative = _compact_video_batch_flow(flow.get("representative_video_analysis"))
    history = _compact_video_batch_flow(flow.get("history_video_evidence"))
    if representative:
        compact["representative_video_analysis"] = representative
    if history:
        compact["history_video_evidence"] = history
    if isinstance(flow.get("account_dossier_extract_job"), dict):
        job = _dict(flow.get("account_dossier_extract_job"))
        compact["account_dossier_extract_job"] = {
            key: job.get(key)
            for key in ("status", "job_id", "kol_pool_id")
            if key in job
        }
    return compact
