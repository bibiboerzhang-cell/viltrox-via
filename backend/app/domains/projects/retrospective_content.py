"""Pure content reconciliation for project retrospectives.

The project retrospective reads two overlapping projections of the same
published content: analyzed video evidence and human-confirmed fulfillment
posts.  This module collapses those projections into one content grain without
performing IO.  Evidence ids are authoritative; canonical native URLs are the
secondary identity.  Missing metrics stay ``None`` and are never rewritten as
observed zeroes.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from app.domains.kol.search_sessions_serde import contains_contact_route


METRIC_FIELDS: tuple[str, ...] = ("view_count", "like_count", "comment_count")
CONTACT_REDACTION = "已移除联系方式"
PROJECTION_TRUNCATION = "已截断"
ANALYSIS_MAX_DEPTH = 6
ANALYSIS_MAX_NODES = 240
ANALYSIS_MAX_STRING_CHARS = 800
TITLE_MAX_CHARS = 500
CAPTION_MAX_CHARS = 1200
_SENSITIVE_ANALYSIS_KEYS = {
    "business_email", "businessemail", "contact", "contact_channels", "contact_details",
    "contact_email", "contact_info", "contact_links", "contact_links_json", "contact_method",
    "contact_phone", "contact_raw_json", "contact_route", "contact_url", "contact_urls",
    "contact_value", "contact_values", "contactemail", "contactphone", "contacts",
    "contacturl", "contactvalue", "direct_message", "discord", "discord_id", "dm", "email",
    "email_address", "emails", "facebook_dm", "ig_dm", "instagram_dm", "line", "line_id",
    "manager_email", "manageremail", "messenger", "messenger_id",
    "mobile", "mobile_number", "phone", "phone_number", "phones", "public_email", "tel",
    "other_contacts", "other_contacts_json", "phonenumber", "publicemail",
    "signal", "signal_id", "telegram", "telegram_id", "telephone", "tiktok_dm", "twitter_dm", "wechat",
    "wechat_id", "weixin", "whatsapp", "whatsapp_id", "x_dm",
    "邮箱", "微信", "联系方式", "联系电话", "电话",
}
_SENSITIVE_KEY_TOKENS = {
    "contact", "discord", "dm", "email", "line", "messenger", "mobile", "phone",
    "signal", "tel", "telegram", "telephone", "wechat", "weixin", "whatsapp",
}
_SENSITIVE_KEY_AFFIXES = {
    "contact", "discord", "email", "line", "messenger", "mobile", "phone", "signal",
    "tel", "telegram", "telephone", "wechat", "weixin", "whatsapp",
}
_SENSITIVE_KEY_CJK_MARKERS = ("邮箱", "邮件", "微信", "联系方式", "联系", "手机", "电话")
_OBFUSCATED_EMAIL = re.compile(
    r"(?<![\w.+-])[a-z0-9.!#$%&'*+/=?^_`{|}~-]+\s*"
    r"(?:\[\s*at\s*\]|\(\s*at\s*\)|\bat\b|＠)\s*"
    r"[a-z0-9-]+(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\bdot\b|\.)\s*[a-z0-9-]+)+",
    re.IGNORECASE,
)


def _host_matches(host: str, domain: str) -> bool:
    """Match a registrable domain without accepting lookalike suffixes."""

    return host == domain or host.endswith(f".{domain}")


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _metric(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _prompt_string(value: Any, *, limit: int, state: dict[str, int]) -> str:
    """Project one external-LLM string, replacing the entire contact-bearing value."""

    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return ""
    if contains_contact_route(text) or _OBFUSCATED_EMAIL.search(text):
        state["redacted_count"] += 1
        return CONTACT_REDACTION
    return text[: max(1, int(limit))]


def _canonical_sensitive_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if text in _SENSITIVE_ANALYSIS_KEYS:
        return text
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text).casefold()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _is_sensitive_analysis_key(value: Any) -> bool:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    canonical = _canonical_sensitive_key(value)
    if raw in _SENSITIVE_ANALYSIS_KEYS or canonical in _SENSITIVE_ANALYSIS_KEYS:
        return True
    if any(marker in raw for marker in _SENSITIVE_KEY_CJK_MARKERS):
        return True
    tokens = {token for token in canonical.split("_") if token}
    if tokens.intersection(_SENSITIVE_KEY_TOKENS):
        return True
    collapsed = canonical.replace("_", "")
    return any(
        collapsed.startswith(marker) or collapsed.endswith(marker)
        for marker in _SENSITIVE_KEY_AFFIXES
    )


def project_analysis_result_for_llm(value: Any) -> tuple[dict[str, Any], int]:
    """Bound and redact an analysis result before it crosses the provider boundary.

    The projection is deliberately independent of reconciliation and does not mutate
    the cached source object.  Depth, node and string limits prevent a raw provider
    payload from being serialized into the retrospective prompt wholesale.
    """

    state = {"nodes": 0, "redacted_count": 0}

    def visit(item: Any, depth: int) -> Any:
        if state["nodes"] >= ANALYSIS_MAX_NODES:
            return PROJECTION_TRUNCATION
        state["nodes"] += 1
        if isinstance(item, str):
            return _prompt_string(item, limit=ANALYSIS_MAX_STRING_CHARS, state=state)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, dict):
            if depth >= ANALYSIS_MAX_DEPTH:
                return PROJECTION_TRUNCATION
            projected: dict[str, Any] = {}
            for index, (raw_key, raw_value) in enumerate(item.items()):
                if state["nodes"] >= ANALYSIS_MAX_NODES:
                    break
                key = _prompt_string(raw_key, limit=120, state=state)
                if key == CONTACT_REDACTION:
                    key = f"redacted_field_{index + 1}"
                if not key:
                    key = f"field_{index + 1}"
                while key in projected:
                    key = f"{key}_{index + 1}"
                if _is_sensitive_analysis_key(raw_key):
                    state["redacted_count"] += 1
                    projected[key] = CONTACT_REDACTION
                else:
                    projected[key] = visit(raw_value, depth + 1)
            return projected
        if isinstance(item, (list, tuple)):
            if depth >= ANALYSIS_MAX_DEPTH:
                return PROJECTION_TRUNCATION
            projected_items: list[Any] = []
            for child in item:
                if state["nodes"] >= ANALYSIS_MAX_NODES:
                    break
                projected_items.append(visit(child, depth + 1))
            return projected_items
        return _prompt_string(item, limit=ANALYSIS_MAX_STRING_CHARS, state=state)

    projected = visit(value if isinstance(value, dict) else {}, 0)
    return (projected if isinstance(projected, dict) else {}), state["redacted_count"]


def project_retrospective_items_for_llm(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Create the strict DTO consumed by the retrospective prompt."""

    projected_items: list[dict[str, Any]] = []
    redacted_count = 0
    for item in items:
        text_state = {"redacted_count": 0}
        analysis, analysis_redacted = project_analysis_result_for_llm(item.get("analysis_result"))
        projected_items.append(
            {
                "source_kinds": [str(value)[:80] for value in (item.get("source_kinds") or [])[:4]],
                "kol_name": _prompt_string(item.get("kol_name"), limit=240, state=text_state),
                "platform": _prompt_string(item.get("platform"), limit=80, state=text_state),
                "title": _prompt_string(item.get("title"), limit=TITLE_MAX_CHARS, state=text_state),
                "caption": _prompt_string(item.get("caption"), limit=CAPTION_MAX_CHARS, state=text_state),
                "view_count": item.get("view_count"),
                "like_count": item.get("like_count"),
                "comment_count": item.get("comment_count"),
                "relationship": {
                    "project_linked": bool((item.get("relationship") or {}).get("project_linked")),
                    "matched_fulfillment": bool((item.get("relationship") or {}).get("matched_fulfillment")),
                },
                "brand_proof": str(item.get("brand_proof") or "unknown")[:32],
                "analysis_result": analysis,
            }
        )
        redacted_count += text_state["redacted_count"] + analysis_redacted
    return projected_items, redacted_count


def canonical_content_identity(value: Any) -> str:
    """Return a stable native-video identity, falling back to a clean URL.

    Tracking parameters, fragments, mobile hosts, and common YouTube URL forms
    do not create separate content identities.  Empty or invalid URLs return an
    empty identity and therefore never merge unrelated rows.
    """

    raw = _text(value)
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    host = str(parsed.hostname or "").lower()
    host = host.removeprefix("www.").removeprefix("m.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    lowered = [part.lower() for part in parts]

    if host == "youtu.be" or _host_matches(host, "youtube.com"):
        video_id = ""
        if host == "youtu.be" and parts:
            video_id = parts[0]
        elif parsed.path.rstrip("/").lower() == "/watch":
            video_id = str((parse_qs(parsed.query).get("v") or [""])[0]).strip()
        elif len(parts) >= 2 and lowered[0] in {"shorts", "embed", "live"}:
            video_id = parts[1]
        if video_id:
            return f"youtube:{video_id}"
    if _host_matches(host, "instagram.com"):
        for index, part in enumerate(lowered):
            if part in {"p", "reel", "tv"} and index + 1 < len(parts):
                return f"instagram:{parts[index + 1]}"
    if _host_matches(host, "tiktok.com"):
        for index, part in enumerate(lowered):
            if part == "video" and index + 1 < len(parts):
                return f"tiktok:{parts[index + 1]}"

    path = "/" + "/".join(parts) if parts else ""
    tracking_keys = {
        "fbclid", "gclid", "igsh", "ref", "ref_src", "si", "source", "feature",
        "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
    }
    clean_query = urlencode(
        sorted((key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in tracking_keys)
    )
    suffix = f"?{clean_query}" if clean_query else ""
    return f"url:https://{host}{path}{suffix}" if host else ""


def _analysis_result(row: dict[str, Any]) -> dict[str, Any]:
    entry = row.get("entry") if isinstance(row.get("entry"), dict) else {}
    return _as_dict(entry.get("result"))


def _brand_proof(result: dict[str, Any]) -> str:
    """Return proof status from analyzed content only, never project linkage."""

    raw = _as_dict(result.get("raw_gemini_video"))
    detected_raw = raw.get("viltrox_detected")
    detected = (
        detected_raw
        if isinstance(detected_raw, bool)
        else str(detected_raw or "").strip().lower() == "true"
        if str(detected_raw or "").strip().lower() in {"true", "false"}
        else None
    )
    products = raw.get("viltrox_products_all")
    if detected is True or (isinstance(products, list) and bool(products)):
        return "confirmed"
    if detected is False:
        return "negative"
    return "unknown"


def _source_record(row: dict[str, Any], kind: str) -> dict[str, Any]:
    is_final = kind == "final_v1"
    result = _analysis_result(row) if is_final else {}
    evidence_id = _positive_int(row.get("evidence_id"))
    post_id = _positive_int(row.get("id")) if not is_final else None
    observation_status = _text(row.get("metric_observation_status")).lower()
    observation_source = _text(row.get("metric_observation_source"))

    def source_metric(field: str) -> int | None:
        if is_final:
            return _metric(row.get(field))
        evidence_field = f"evidence_{field}"
        if observation_status in {"observed", "observed_evidence"}:
            return _metric(row.get(evidence_field) if evidence_field in row else row.get(field))
        if observation_status:
            return None
        # Backward-compatible callers may not yet project observation status.
        # Legacy NOT NULL zero is not evidence that the platform observed zero.
        value = _metric(row.get(field))
        return None if value == 0 else value

    return {
        "kind": kind,
        "evidence_id": evidence_id,
        "post_id": post_id,
        "canonical_identity": canonical_content_identity(row.get("content_url")),
        "content_url": _text(row.get("content_url")),
        "title": _text(row.get("title")),
        "caption": _text(row.get("caption")),
        "platform": _text(row.get("platform")),
        "kol_name": _text(row.get("kol_name") or row.get("handle")),
        "published_at": row.get("publish_date") or row.get("published_at"),
        "status": _text(row.get("status")),
        "project_linked": bool(is_final or _positive_int(row.get("project_id"))),
        "matched_fulfillment": not is_final,
        "metric_observation_status": observation_status or ("analysis_evidence" if is_final else "legacy_unspecified"),
        "metric_observation_source": observation_source,
        "analysis_result": result,
        "brand_proof": _brand_proof(result) if is_final else "unknown",
        **{field: source_metric(field) for field in METRIC_FIELDS},
    }


def _best_text(records: list[dict[str, Any]], field: str) -> str:
    values = [_text(record.get(field)) for record in records]
    values = [value for value in values if value]
    return max(values, key=lambda value: (len(value), value)) if values else ""


def _metric_choice(records: list[dict[str, Any]], field: str) -> tuple[int | None, str | None, list[int]]:
    measured = [record for record in records if record.get(field) is not None]
    if not measured:
        return None, None, []
    measured.sort(key=lambda record: (record.get("kind") != "final_v1", record.get("post_id") or 0))
    chosen = measured[0]
    values = sorted({int(record[field]) for record in measured})
    source = str(chosen.get("metric_observation_source") or chosen["kind"])
    return int(chosen[field]), source, values if len(values) > 1 else []


def _merge_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_ids = sorted({int(r["evidence_id"]) for r in records if r.get("evidence_id")})
    post_ids = sorted({int(r["post_id"]) for r in records if r.get("post_id")})
    source_kinds = sorted({str(r["kind"]) for r in records})
    final_records = [r for r in records if r.get("kind") == "final_v1"]
    richest_final = max(
        final_records,
        key=lambda record: len(json.dumps(record.get("analysis_result") or {}, ensure_ascii=False, default=str)),
        default=None,
    )
    metrics: dict[str, int | None] = {}
    metric_sources: dict[str, str | None] = {}
    metric_conflicts: dict[str, list[int]] = {}
    for field in METRIC_FIELDS:
        value, source, conflicts = _metric_choice(records, field)
        metrics[field] = value
        metric_sources[field] = source
        if conflicts:
            metric_conflicts[field] = conflicts
    proofs = {str(r.get("brand_proof") or "unknown") for r in final_records}
    brand_proof = "confirmed" if "confirmed" in proofs else "negative" if proofs == {"negative"} else "unknown"
    canonical_identity = next((str(r["canonical_identity"]) for r in records if r.get("canonical_identity")), "")
    identity = (
        f"evidence:{evidence_ids[0]}"
        if evidence_ids
        else canonical_identity
        if canonical_identity
        else f"post:{post_ids[0]}"
    )
    return {
        "identity": identity,
        "evidence_id": evidence_ids[0] if evidence_ids else None,
        "evidence_ids": evidence_ids,
        "post_ids": post_ids,
        "source_kinds": source_kinds,
        "content_url": _best_text(records, "content_url"),
        "canonical_identity": canonical_identity,
        "title": _best_text(records, "title"),
        "caption": _best_text(records, "caption"),
        "platform": _best_text(records, "platform"),
        "kol_name": _best_text(records, "kol_name"),
        "published_at": next((r.get("published_at") for r in records if r.get("published_at") is not None), None),
        "status": _best_text(records, "status"),
        "relationship": {
            "project_linked": any(bool(r.get("project_linked")) for r in records),
            "matched_fulfillment": any(bool(r.get("matched_fulfillment")) for r in records),
        },
        "brand_proof": brand_proof,
        "analysis_result": (richest_final or {}).get("analysis_result") or {},
        "metric_sources": metric_sources,
        "metric_conflicts": metric_conflicts,
        **metrics,
    }


def summarize_content_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    metrics: dict[str, dict[str, Any]] = {}
    for field in METRIC_FIELDS:
        values = [int(item[field]) for item in items if item.get(field) is not None]
        metrics[field] = {
            "measured": len(values),
            "missing": total - len(values),
            "coverage": round(len(values) / total, 4) if total else None,
            "total": sum(values) if values else None,
        }
    engagement_values = [
        int(item["like_count"]) + int(item["comment_count"])
        for item in items
        if item.get("like_count") is not None and item.get("comment_count") is not None
    ]
    metrics["engagement"] = {
        "measured": len(engagement_values),
        "missing": total - len(engagement_values),
        "coverage": round(len(engagement_values) / total, 4) if total else None,
        "total": sum(engagement_values) if engagement_values else None,
        "requires": ["like_count", "comment_count"],
    }
    return metrics


def reconcile_retrospective_content(
    final_v1_items: list[dict[str, Any]],
    matched_posts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge final_v1 and matched-post projections into one content grain."""

    records = [*(_source_record(row, "final_v1") for row in final_v1_items)]
    records.extend(_source_record(row, "matched_content_post") for row in matched_posts)
    parent = list(range(len(records)))
    root_evidence_ids: list[set[int]] = [
        {int(record["evidence_id"])} if record.get("evidence_id") is not None else set()
        for record in records
    ]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root
            root_evidence_ids[left_root].update(root_evidence_ids[right_root])
            root_evidence_ids[right_root].clear()

    evidence_seen: dict[int, int] = {}
    evidence_matches = 0
    for index, record in enumerate(records):
        evidence_id = record.get("evidence_id")
        if evidence_id is None:
            continue
        if evidence_id in evidence_seen:
            union(evidence_seen[evidence_id], index)
            evidence_matches += 1
        else:
            evidence_seen[evidence_id] = index

    url_seen: dict[str, int] = {}
    url_matches = 0
    identity_conflict_pairs: set[tuple[int, int]] = set()
    for index, record in enumerate(records):
        identity = str(record.get("canonical_identity") or "")
        if not identity:
            continue
        if identity not in url_seen:
            url_seen[identity] = index
            continue
        left_root, right_root = find(url_seen[identity]), find(index)
        if left_root == right_root:
            continue
        left_ids = root_evidence_ids[left_root]
        right_ids = root_evidence_ids[right_root]
        if left_ids and right_ids and left_ids.isdisjoint(right_ids):
            for left_id in left_ids:
                for right_id in right_ids:
                    identity_conflict_pairs.add(tuple(sorted((left_id, right_id))))
            continue
        union(left_root, right_root)
        url_matches += 1
        url_seen[identity] = find(left_root)

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        groups.setdefault(find(index), []).append(record)
    items = [_merge_group(group) for group in groups.values()]
    conflicted_evidence_ids = {value for pair in identity_conflict_pairs for value in pair}
    for item in items:
        item["identity_conflict"] = bool(
            conflicted_evidence_ids.intersection(item.get("evidence_ids") or [])
        )
    items.sort(
        key=lambda item: (
            item.get("view_count") is None,
            -(int(item["view_count"]) if item.get("view_count") is not None else 0),
            int(item.get("evidence_id") or 0),
            str(item.get("identity") or ""),
        )
    )
    metrics = summarize_content_metrics(items)
    proof_counts = {
        status: sum(1 for item in items if item.get("brand_proof") == status)
        for status in ("confirmed", "negative", "unknown")
    }
    cross_source = sum(1 for item in items if len(item.get("source_kinds") or []) > 1)
    conflicts = sum(1 for item in items if item.get("metric_conflicts"))
    partial = bool(items) and (
        any(metrics[field]["missing"] > 0 for field in METRIC_FIELDS)
        or proof_counts["unknown"] > 0
        or conflicts > 0
        or bool(identity_conflict_pairs)
    )
    return {
        "items": items,
        "diagnostics": {
            "input_rows": {
                "final_v1": len(final_v1_items),
                "matched_content_posts": len(matched_posts),
                "total": len(records),
            },
            "unique_content_count": len(items),
            "deduped_row_count": len(records) - len(items),
            "dedupe_matches": {
                "evidence_id": evidence_matches,
                "canonical_url": url_matches,
                "cross_source": cross_source,
            },
            "metrics": metrics,
            "analysis_coverage": {
                "final_v1": sum(1 for item in items if "final_v1" in (item.get("source_kinds") or [])),
                "matched_fulfillment": sum(
                    1 for item in items if item.get("relationship", {}).get("matched_fulfillment")
                ),
                "brand_proof": proof_counts,
            },
            "metric_conflict_content_count": conflicts,
            "identity_conflicts": {
                "count": len(identity_conflict_pairs),
                "evidence_id_pairs": [list(pair) for pair in sorted(identity_conflict_pairs)],
            },
            "partial": partial,
            "brand_proof_note": (
                "project relationship is tracked separately and never promotes brand_proof; "
                "brand proof comes only from analyzed content signals"
            ),
        },
    }


__all__ = [
    "ANALYSIS_MAX_DEPTH",
    "ANALYSIS_MAX_NODES",
    "ANALYSIS_MAX_STRING_CHARS",
    "CONTACT_REDACTION",
    "canonical_content_identity",
    "project_analysis_result_for_llm",
    "project_retrospective_items_for_llm",
    "reconcile_retrospective_content",
    "summarize_content_metrics",
]
