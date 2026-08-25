"""MY KOL 一键「数据关注」写边界(波 D·B 车道)。

用户在视频卡上点一下「数据关注」= 关联产品 + 激活持续追踪 + 排一次数值刷新。
SKU 解析五级优先:
  1. 请求体 product_skus(手选,sku_source=manual)
  2. 该视频已有产品关联 vkpi_kol_video_product_links(sku_source=existing)
  3. 已有 final_v1 结构化深析派生证据 vkpi_kol_lens_evidence:只接受
     resolution=sku 且最新 ready cache 的目录命中，保留画面/字幕/口播来源;
     唯一命中才落 detected，多命中必须员工选择。
  4. 与该 evidence 严格同视频身份的 Pool 已缓存 caption/description/snippet;
  5. 保守自动识别:标题归一化(product_aliases.normalize_alias)后按 token 边界
     唯一命中产品 SKU/型号/市场名(sku_source=auto);内容与标题冲突、零命中或多命中 →
     诚实返回 status=sku_required + 候选清单,绝不瞎猜落库。

落地路径 100% 复用 video_tracking.queue_tracked_video(行级权限围栏 / SKU 校验 /
关联落库 / vkpi_kol_video_metric_tracking 激活 / kol_video_metric_refresh 幂等入队),
本文件不重造任何围栏;sku_source=existing 时传空 SKU 列表,不重复写关联行。
红线:纯排队零 provider;绝不触 rule_v0 / fit 分;SQL 全 ? 占位。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.domains.kol import video_tracking
from app.domains.kol.pool_common import _table_columns
from app.domains.kol.video_url_identity import (
    VideoUrlIdentity,
    VideoUrlIdentityError,
    parse_supported_video_url,
)


MAX_CANDIDATES = 20
MAX_PRODUCTS_SCANNED = 2000
MAX_CACHED_CONTENT_ITEMS = 500
MAX_CACHED_CONTENT_CHARS = 12_000
# 与 sku_performance._aliases_for 同款保守闸:太短或纯字母短词的别名不参与匹配。
MIN_ALIAS_LEN = 5
MIN_ALIAS_LEN_NO_DIGIT = 10
STRUCTURED_EVIDENCE_SOURCE = "final_v1_lens_evidence_v2"
CACHED_CONTENT_SOURCE = "cached_content_alias_v1"
TITLE_ALIAS_SOURCE = "title_alias_v1"
MATCH_SOURCE_PRIORITY = {
    CACHED_CONTENT_SOURCE: 1,
    TITLE_ALIAS_SOURCE: 2,
}
STRUCTURED_EXPLICIT_MODALITIES = frozenset({"visual", "text", "voice"})
DETECTED_CONFIRMATION_SOURCE = "human_confirmed_detected_v1"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize(value: Any) -> str:
    from app.domains.products.product_aliases import normalize_alias

    return normalize_alias(value)


def _title_text(evidence: dict[str, Any]) -> str:
    return _text(evidence.get("video_title")) or _text(evidence.get("title"))


def _sku_name(row: dict[str, Any]) -> str:
    return (
        _text(row.get("marketing_name"))
        or _text(row.get("model_name"))
        or _text(row.get("sku"))
    )


def _candidate(row: dict[str, Any]) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "sku_code": _text(row.get("sku")),
        "sku_name": _sku_name(row),
    }
    if _text(row.get("match_source")):
        candidate["match_source"] = _text(row.get("match_source"))
    modalities = row.get("modalities") if isinstance(row.get("modalities"), list) else []
    if modalities:
        candidate["modalities"] = modalities
    if _text(row.get("evidence_excerpt")):
        candidate["evidence_excerpt"] = _text(row.get("evidence_excerpt"))[:200]
    return candidate


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, (str, bytes, bytearray)) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return [_text(item) for item in parsed if _text(item)] if isinstance(parsed, list) else []
    return []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, (str, bytes, bytearray)) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cached_post_items(raw: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Read a bounded set of already-cached post rows; never crawl or infer a profile."""

    roots: list[dict[str, Any]] = [raw]
    for key in ("profile", "raw"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            roots.append(nested)
            nested_raw = nested.get("raw")
            if isinstance(nested_raw, dict):
                roots.append(nested_raw)
    rows: list[tuple[str, dict[str, Any]]] = []
    seen: set[int] = set()
    for root in roots:
        for collection in ("videos", "posts", "items", "latest_posts", "latestPosts"):
            value = root.get(collection)
            if isinstance(value, dict):
                value = value.get("items") or value.get("results") or value.get("data") or []
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict) or id(item) in seen:
                    continue
                seen.add(id(item))
                rows.append((collection, item))
                if len(rows) >= MAX_CACHED_CONTENT_ITEMS:
                    return rows
    return rows


def _candidate_video_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in (
        "content_url", "video_url", "videoUrl", "post_url", "postUrl", "url",
        "webVideoUrl", "permalink", "link",
    ):
        value = _text(item.get(key))
        if value and value not in urls:
            urls.append(value)
    return urls


def _candidate_video_id(item: dict[str, Any], platform: str, collection: str) -> str:
    raw_id: Any = None
    if platform == "youtube":
        raw_id = item.get("video_id") or item.get("videoId")
        if not raw_id and isinstance(item.get("id"), dict):
            raw_id = (item.get("id") or {}).get("videoId")
        if not raw_id and collection == "videos" and "channel" not in _text(item.get("kind")).lower():
            raw_id = item.get("id")
    elif platform == "instagram":
        raw_id = item.get("shortCode") or item.get("shortcode") or item.get("code") or item.get("post_uid")
        if not raw_id and collection in {"videos", "posts", "latest_posts", "latestPosts"}:
            raw_id = item.get("id")
    elif platform == "tiktok":
        raw_id = item.get("video_id") or item.get("videoId")
        if not raw_id and collection in {"videos", "posts", "latest_posts", "latestPosts"}:
            raw_id = item.get("id")
    return _text(raw_id)


def _cached_item_matches_video(
    item: dict[str, Any],
    *,
    collection: str,
    target: VideoUrlIdentity,
) -> bool:
    saw_supported_url = False
    for candidate_url in _candidate_video_urls(item):
        try:
            candidate = parse_supported_video_url(candidate_url)
        except VideoUrlIdentityError:
            continue
        saw_supported_url = True
        if candidate.platform == target.platform and candidate.video_id == target.video_id:
            return True
    if saw_supported_url:
        return False
    return _candidate_video_id(item, target.platform, collection) == target.video_id


def _cached_item_content(item: dict[str, Any]) -> str:
    """Return caption/description/snippet prose only; title stays a separate provenance."""

    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    localized = snippet.get("localized") if isinstance(snippet.get("localized"), dict) else {}
    parts: list[str] = []
    for value in (
        item.get("caption"),
        item.get("description"),
        item.get("desc"),
        item.get("text"),
        snippet.get("description"),
        localized.get("description"),
    ):
        text = _text(value)
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)[:MAX_CACHED_CONTENT_CHARS]


def _cached_content_text(conn: Any, evidence: dict[str, Any]) -> str:
    """Find prose for this exact cached video in its exact linked Pool row.

    The evidence table intentionally has no description column.  This fallback is
    read-only and identity-gated: a different post from the same creator can never
    contribute product text to the clicked evidence.
    """

    if "raw_platform_data" not in _table_columns(conn, "vkpi_kol_pool"):
        return ""
    try:
        target = parse_supported_video_url(evidence.get("content_url"))
    except VideoUrlIdentityError:
        return ""
    row = conn.execute(
        "SELECT platform, raw_platform_data FROM vkpi_kol_pool WHERE id=? LIMIT 1",
        (_int(evidence.get("kol_pool_id")),),
    ).fetchone()
    if not row:
        return ""
    pool = dict(row)
    pool_platform = _text(pool.get("platform")).lower()
    if pool_platform and pool_platform != target.platform:
        return ""
    raw = _json_object(pool.get("raw_platform_data"))
    matches: list[str] = []
    used_chars = 0
    for collection, item in _cached_post_items(raw):
        if not _cached_item_matches_video(item, collection=collection, target=target):
            continue
        text = _cached_item_content(item)
        if not text or text in matches:
            continue
        separator_chars = 1 if matches else 0
        remaining = MAX_CACHED_CONTENT_CHARS - used_chars - separator_chars
        if remaining <= 0:
            break
        bounded = text[:remaining]
        matches.append(bounded)
        used_chars += separator_chars + len(bounded)
        if used_chars >= MAX_CACHED_CONTENT_CHARS:
            break
    return "\n".join(matches)


def _structured_product_matches(conn: Any, evidence_id: int) -> list[dict[str, Any]]:
    """Read auditable, catalog-resolved product mentions from the latest ready final_v1.

    The derived table is deliberately optional for migration/test compatibility.  A missing
    table or missing ready cache is evidence absence, not permission to guess from prose.
    """

    if not _table_columns(conn, "vkpi_kol_lens_evidence") or not _table_columns(
        conn, "vkpi_analysis_cache"
    ):
        return []
    rows = conn.execute(
        """
        SELECT le.product_sku AS sku, p.model_name, p.marketing_name,
               le.cache_id, le.modalities, le.source_fields, le.mention_text,
               le.extractor_version
        FROM vkpi_kol_lens_evidence le
        JOIN vkpi_analysis_cache c ON c.id = le.cache_id
        JOIN vkpi_products p ON p.sku = le.product_sku
        WHERE le.evidence_id = ?
          AND le.resolution = 'sku'
          AND le.product_sku IS NOT NULL
          AND c.target_type = 'video'
          AND c.target_id = CAST(? AS TEXT)
          AND c.derive_method = 'video_analysis_final_v1'
          AND c.status = 'ready'
          AND c.id = (
              SELECT c2.id
              FROM vkpi_analysis_cache c2
              WHERE c2.target_type = 'video'
                AND c2.target_id = CAST(? AS TEXT)
                AND c2.derive_method = 'video_analysis_final_v1'
                AND c2.status = 'ready'
              ORDER BY c2.id DESC
              LIMIT 1
          )
        ORDER BY le.product_sku, le.id DESC
        """,
        (int(evidence_id), int(evidence_id), int(evidence_id)),
    ).fetchall()
    by_sku: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        sku = _text(row.get("sku"))
        if not sku:
            continue
        item = by_sku.setdefault(
            sku,
            {
                "sku": sku,
                "model_name": row.get("model_name"),
                "marketing_name": row.get("marketing_name"),
                "match_source": STRUCTURED_EVIDENCE_SOURCE,
                "cache_id": _int(row.get("cache_id")),
                "modalities": [],
                "source_fields": [],
                "evidence_excerpt": "",
                "extractor_version": _text(row.get("extractor_version")),
            },
        )
        for modality in _json_list(row.get("modalities")):
            if modality not in item["modalities"]:
                item["modalities"].append(modality)
        for field in _json_list(row.get("source_fields")):
            if field not in item["source_fields"]:
                item["source_fields"].append(field)
        if not item["evidence_excerpt"]:
            item["evidence_excerpt"] = _text(row.get("mention_text"))[:200]
    return list(by_sku.values())


def _structured_confidence(match: dict[str, Any]) -> float:
    modalities = set(_json_list(match.get("modalities")))
    return 0.85 if modalities & STRUCTURED_EXPLICIT_MODALITIES else 0.72


def _alias_usable(alias_norm: str) -> bool:
    if len(alias_norm) < MIN_ALIAS_LEN:
        return False
    if not any(ch.isdigit() for ch in alias_norm) and len(alias_norm) < MIN_ALIAS_LEN_NO_DIGIT:
        return False
    return True


def _existing_links(conn: Any, evidence_id: int) -> list[dict[str, Any]]:
    """该视频已落库的产品关联，每 SKU 保留最强一条真值。

    confirmed/manual 优先于 detected，避免“已有关联”被笼统返回为
    requires_human_confirmation=false，把系统检测偷换成员工确认。
    """

    rows = conn.execute(
        """
        SELECT product_sku, relation_type, source, confidence
        FROM vkpi_kol_video_product_links
        WHERE evidence_id=?
        ORDER BY product_sku,
                 CASE relation_type WHEN 'confirmed' THEN 0 WHEN 'manual' THEN 1 ELSE 2 END,
                 confidence DESC, id DESC
        """,
        (int(evidence_id),),
    ).fetchall()
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        row = dict(raw)
        sku = _text(row.get("product_sku"))
        if not sku or sku in seen:
            continue
        seen.add(sku)
        links.append(
            {
                "sku": sku,
                "relation_type": _text(row.get("relation_type")),
                "source": _text(row.get("source")),
                "confidence": row.get("confidence"),
            }
        )
        if len(links) >= video_tracking.MAX_PRODUCT_SKUS:
            break
    return links


def _detected_links(conn: Any, evidence_id: int) -> list[dict[str, Any]]:
    """Return raw system detections for an explicit employee confirmation.

    Do not reuse ``_existing_links`` here: that read model intentionally folds a
    confirmed/manual row over a detected row for display.  Confirmation must
    prove that the exact SKU was previously written as ``detected`` and must
    reject a multi-SKU detection instead of silently choosing one.
    """

    rows = conn.execute(
        """
        SELECT product_sku, source, confidence
        FROM vkpi_kol_video_product_links
        WHERE evidence_id=? AND relation_type='detected'
        ORDER BY product_sku, id DESC
        """,
        (int(evidence_id),),
    ).fetchall()
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        row = dict(raw)
        sku = _text(row.get("product_sku"))
        if not sku or sku in seen:
            continue
        seen.add(sku)
        links.append(
            {
                "sku": sku,
                "source": _text(row.get("source")),
                "confidence": row.get("confidence"),
            }
        )
    return links


def _catalog_rows(conn: Any, *, limit: int = MAX_PRODUCTS_SCANNED) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sku, model_name, marketing_name
        FROM vkpi_products
        ORDER BY sku
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def _match_products_in_title(
    products: list[dict[str, Any]],
    title: str,
) -> list[dict[str, Any]]:
    """文本归一化后按 token 边界找产品;每个产品试 SKU/型号/市场名三个别名。"""

    title_norm = _normalize(title)
    if not title_norm:
        return []
    matched: list[dict[str, Any]] = []
    for row in products:
        sku = _text(row.get("sku"))
        if not sku:
            continue
        for alias in (sku, row.get("model_name"), row.get("marketing_name")):
            alias_norm = _normalize(alias)
            if not alias_norm or not _alias_usable(alias_norm):
                continue
            pattern = r"(?<![a-z0-9])" + re.escape(alias_norm) + r"(?![a-z0-9])"
            if re.search(pattern, title_norm):
                matched.append(row)
                break
    return matched


def _with_match_source(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return [{**row, "match_source": source} for row in rows]


def _merge_product_matches(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sku: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group:
            sku = _text(row.get("sku"))
            current = by_sku.get(sku)
            if sku and (
                current is None
                or MATCH_SOURCE_PRIORITY.get(_text(row.get("match_source")), 0)
                > MATCH_SOURCE_PRIORITY.get(_text(current.get("match_source")), 0)
            ):
                # Keep the stronger direct title signal when the same SKU also
                # appears in cached prose.  Replacing a value does not change its
                # insertion order, so truly conflicting SKU choices stay stable.
                by_sku[sku] = row
    return list(by_sku.values())


def _sku_required(conn: Any, evidence: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
    """零命中给全目录候选(截断),多命中只给撞上的几个,让用户点选拍板。"""

    if matches:
        candidates = [_candidate(row) for row in matches[:MAX_CANDIDATES]]
    else:
        candidates = [_candidate(row) for row in _catalog_rows(conn, limit=MAX_CANDIDATES)]
    return {
        "status": "sku_required",
        "evidence_id": _int(evidence.get("id")),
        "candidates": candidates,
    }


def data_watch(
    conn: Any,
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None,
    product_skus: Any = None,
    confirm_detected_skus: Any = None,
) -> dict[str, Any]:
    """一键数据关注:解析 SKU 后走既有追踪路径;解析不出诚实 sku_required。

    调用方把 VideoTrackingError 映射为对应 HTTP 状态,LookupError 映射 404。
    """

    if _int(kol_pool_id) <= 0 or _int(evidence_id) <= 0:
        raise video_tracking.VideoTrackingError("video_evidence_id_invalid")
    # 与追踪路径同一行级围栏先行:未授权者连候选清单也不该看到。
    video_tracking._assert_target_writable(
        conn,
        kol_pool_id=int(kol_pool_id),
        staff=staff,
    )
    evidence = video_tracking._load_evidence_by_id(conn, int(evidence_id))
    if not evidence or _int(evidence.get("kol_pool_id")) != int(kol_pool_id):
        raise LookupError("video_evidence_not_found")

    if product_skus is not None and confirm_detected_skus is not None:
        raise video_tracking.VideoTrackingError("data_watch_sku_intent_conflict")
    manual = video_tracking.normalize_product_skus(product_skus)
    confirmation = (
        video_tracking.normalize_product_skus(confirm_detected_skus)
        if confirm_detected_skus is not None
        else []
    )
    detected_detail: dict[str, Any] | None = None
    detected_source = ""
    existing_detail: list[dict[str, Any]] = []
    confirmed_detection: dict[str, Any] | None = None
    if confirm_detected_skus is not None:
        if len(confirmation) != 1:
            raise video_tracking.VideoTrackingError("detected_sku_confirmation_requires_one")
        detected_links = _detected_links(conn, _int(evidence.get("id")))
        if len(detected_links) != 1:
            raise video_tracking.VideoTrackingError(
                "detected_sku_confirmation_requires_unique_detection",
                409,
            )
        confirmed_detection = detected_links[0]
        if confirmation[0] != _text(confirmed_detection.get("sku")):
            raise video_tracking.VideoTrackingError(
                "detected_sku_confirmation_mismatch",
                409,
            )
        skus, sku_source = confirmation, "confirmation"
    elif manual:
        skus, sku_source = manual, "manual"
    else:
        existing_detail = _existing_links(conn, _int(evidence.get("id")))
        if existing_detail:
            skus, sku_source = [item["sku"] for item in existing_detail], "existing"
        else:
            structured = _structured_product_matches(conn, _int(evidence.get("id")))
            if len(structured) == 1:
                skus, sku_source = [_text(structured[0].get("sku"))], "auto"
                detected_detail = structured[0]
                detected_source = STRUCTURED_EVIDENCE_SOURCE
            elif len(structured) > 1:
                return _sku_required(conn, evidence, structured)
            else:
                products = _catalog_rows(conn)
                cached_matches = _with_match_source(
                    _match_products_in_title(products, _cached_content_text(conn, evidence)),
                    CACHED_CONTENT_SOURCE,
                )
                title_matches = _with_match_source(
                    _match_products_in_title(products, _title_text(evidence)),
                    TITLE_ALIAS_SOURCE,
                )
                # Cached prose and title are both creator-authored text signals.  A
                # disagreement is ambiguity, not permission to let either source win.
                matches = _merge_product_matches(cached_matches, title_matches)
                if len(matches) == 1:
                    skus, sku_source = [_text(matches[0].get("sku"))], "auto"
                    detected_source = _text(matches[0].get("match_source")) or TITLE_ALIAS_SOURCE
                else:
                    return _sku_required(conn, evidence, matches)

    if sku_source == "confirmation":
        link_relation_type = "confirmed"
        link_source = DETECTED_CONFIRMATION_SOURCE
        link_confidence = 1.0
    elif sku_source == "auto":
        link_relation_type = "detected"
        link_source = detected_source or (
            STRUCTURED_EVIDENCE_SOURCE if detected_detail else TITLE_ALIAS_SOURCE
        )
        link_confidence = (
            _structured_confidence(detected_detail)
            if detected_detail
            else 0.55 if link_source == CACHED_CONTENT_SOURCE else 0.6
        )
    else:
        link_relation_type = "manual"
        link_source = video_tracking.TRACKING_SOURCE
        link_confidence = 1.0

    # 关联已在库的不重复写;标题唯一命中只能落 detected,
    # 绝不得冒充员工手选的 manual/1.0 事实。
    queued = video_tracking.queue_tracked_video(
        conn,
        kol_pool_id=int(kol_pool_id),
        content_url=evidence.get("content_url"),
        product_skus=[] if sku_source == "existing" else skus,
        staff=staff,
        product_link_relation_type=link_relation_type,
        product_link_source=link_source,
        product_link_confidence=link_confidence,
    )
    if sku_source == "existing":
        relation_types = {_text(item.get("relation_type")) for item in existing_detail}
        detected_pending = any(_text(item.get("relation_type")) == "detected" for item in existing_detail)
        existing_provenance: dict[str, Any] = {
            "relation_type": next(iter(relation_types)) if len(relation_types) == 1 else "mixed",
            "source": _text(existing_detail[0].get("source")) if len(existing_detail) == 1 else "existing_link",
            "confidence": existing_detail[0].get("confidence") if len(existing_detail) == 1 else None,
            "requires_human_confirmation": detected_pending,
            "links": existing_detail,
        }
    else:
        existing_provenance = {}

    return {
        "status": "tracking",
        "evidence_id": _int(evidence.get("id")),
        "kol_pool_id": int(kol_pool_id),
        "skus": skus,
        "sku_source": sku_source,
        "sku_provenance": (
            {
                "relation_type": "detected",
                "source": link_source,
                "confidence": link_confidence,
                "requires_human_confirmation": True,
                **(
                    {
                        "cache_id": _int(detected_detail.get("cache_id")),
                        "modalities": list(detected_detail.get("modalities") or []),
                        "source_fields": list(detected_detail.get("source_fields") or []),
                        "evidence_excerpt": _text(detected_detail.get("evidence_excerpt"))[:200],
                        "extractor_version": _text(detected_detail.get("extractor_version")),
                    }
                    if detected_detail
                    else {}
                ),
            }
            if sku_source == "auto"
            else {
                "relation_type": "confirmed",
                "source": DETECTED_CONFIRMATION_SOURCE,
                "confidence": 1.0,
                "requires_human_confirmation": False,
                "confirmed_from": {
                    "relation_type": "detected",
                    "source": _text((confirmed_detection or {}).get("source")),
                    "confidence": (confirmed_detection or {}).get("confidence"),
                },
            }
            if sku_source == "confirmation"
            else existing_provenance if sku_source == "existing" else {
                "relation_type": link_relation_type if sku_source == "manual" else "existing",
                "source": link_source if sku_source == "manual" else "existing_link",
                "confidence": link_confidence if sku_source == "manual" else None,
                "requires_human_confirmation": False,
            }
        ),
        "tracking": _text(queued.get("metric_tracking_status")) or "active",
        "refresh": _text(queued.get("status")),
    }


__all__ = ["data_watch"]
