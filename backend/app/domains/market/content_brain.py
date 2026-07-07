"""P6 content brain deterministic dry-run preview."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.domains.costs.budget_guard import check_budget, get_budget_status


BUDGET_SCOPE = "cron:p6_content_brain_analysis"
ANALYSIS_VERSION = "p6_content_brain_rule_v0"
FORBIDDEN_WRITE_FLAGS = {
    "--commit",
    "--write",
    "--persist",
    "--save",
    "--apply",
    "--with-llm",
    "--provider",
    "--record-cost",
}

VILTROX_TERMS = {"viltrox", "weeylite"}
COMPETITOR_BRANDS = {
    "sony",
    "canon",
    "nikon",
    "sigma",
    "tamron",
    "fujifilm",
    "fuji",
    "godox",
    "zeiss",
    "leica",
    "panasonic",
    "lumix",
    "blackmagic",
    "dji",
    "sirui",
    "ttartisan",
    "laowa",
}

TAG_KEYWORDS = {
    "review": ("review", "hands-on", "hands on", "测试", "评测"),
    "tutorial": ("tutorial", "how to", "tips", "technique", "技巧", "教程"),
    "unboxing": ("unboxing", "开箱"),
    "comparison": ("comparison", "versus", " vs ", "compare", "对比"),
    "launch": ("launch", "new release", "released", "新品", "发布"),
    "cinematic": ("cinematic", "cinema", "filmmaking", "video applications"),
    "portrait": ("portrait", "人像"),
    "street": ("street", "街拍"),
    "event": ("nab", "show", "booth", "expo", "event", "展会"),
    "giveaway": ("giveaway", "抽奖"),
    "lighting": ("light", "lighting", "flash", "led"),
}

RISK_KEYWORDS = {
    "negative_sentiment": ("bad", "worst", "terrible", "scam", "fake", "broken", "差评", "翻车"),
    "pricing_sensitive": ("price", "rrp", "usd", "tax", "vat", "budget", "折扣", "价格"),
    "competitor_focus": tuple(sorted(COMPETITOR_BRANDS)),
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


from app.core.coerce import _text


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_limit(value: int, *, default: int = 50, ceiling: int = 500) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(ceiling, parsed))


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def _list_posts(*, platform: str = "", account_id: int = 0, post_id: int = 0, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if platform:
        where.append("LOWER(p.platform)=LOWER(?)")
        params.append(platform)
    if account_id:
        where.append("p.account_id=?")
        params.append(int(account_id))
    if post_id:
        where.append("p.id=?")
        params.append(int(post_id))
    if query:
        where.append("(LOWER(p.title) LIKE ? OR LOWER(p.caption) LIKE ? OR LOWER(p.post_url) LIKE ?)")
        token = f"%{query.lower()}%"
        params.extend([token, token, token])
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT p.*,
               a.handle AS account_handle,
               a.display_name AS account_display_name,
               a.brand_group AS account_brand_group,
               a.account_role AS account_role,
               a.region AS account_region,
               a.category AS account_category
        FROM vkpi_industry_posts p
        LEFT JOIN vkpi_industry_accounts a ON a.id=p.account_id
        {clause}
        ORDER BY p.published_at DESC, p.id DESC
        LIMIT ?
        """,
        (*params, _safe_limit(limit)),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _media_for_posts(post_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not post_ids or not _table_exists("vkpi_industry_post_media"):
        return {}
    placeholders = ",".join("?" for _ in post_ids)
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_industry_post_media WHERE post_id IN ({placeholders}) ORDER BY post_id, id",
        tuple(post_ids),
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = _row_to_dict(row)
        grouped.setdefault(_safe_int(item.get("post_id")), []).append(item)
    return grouped


def _combined_text(post: dict[str, Any]) -> str:
    pieces = [
        post.get("title"),
        post.get("caption"),
        " ".join(_loads(post.get("hashtags_json"), [])),
        json.dumps(_loads(post.get("raw_platform_data"), {}), ensure_ascii=False)[:2000],
    ]
    return " ".join(_text(piece) for piece in pieces if _text(piece))


def _extract_products(text: str, detected_products: list[Any]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in detected_products:
        value = _text(raw if not isinstance(raw, dict) else raw.get("name") or raw.get("product"))
        if value and value.lower() not in seen:
            seen.add(value.lower())
            products.append({"product": value, "source": "detected_products_json", "confidence": 0.8})
    patterns = [
        r"\b(?:AF|XF|Z|FE|RF)?\s*\d{1,3}\s*mm\s*(?:F|f)?\s*/?\s*\d(?:\.\d)?\b",
        r"\b\d{1,3}\s*mm\b",
        r"\bFL\d{2}[A-Za-z]*\b",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = re.sub(r"\s+", " ", match).strip()
            key = value.lower()
            if key and key not in seen:
                seen.add(key)
                products.append({"product": value, "source": "text_regex", "confidence": 0.6})
    return products[:12]


def _keyword_hits(text_lower: str, mapping: dict[str, tuple[str, ...]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for key, keywords in mapping.items():
        matched = sorted({keyword for keyword in keywords if keyword in text_lower})
        if matched:
            hits.append({"tag": key, "matched_terms": matched[:6], "confidence": min(0.95, 0.55 + len(matched) * 0.1)})
    return hits


def _brand_mentions(text_lower: str) -> list[dict[str, Any]]:
    brands: list[dict[str, Any]] = []
    for brand in sorted(VILTROX_TERMS | COMPETITOR_BRANDS):
        if brand in text_lower:
            brands.append({
                "brand": brand,
                "brand_type": "own_brand" if brand in VILTROX_TERMS else "competitor",
                "confidence": 0.8,
            })
    return brands


def _risk_flags(text_lower: str, brands: list[dict[str, Any]], post: dict[str, Any]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for item in _keyword_hits(text_lower, RISK_KEYWORDS):
        severity = "medium"
        if item["tag"] == "negative_sentiment":
            severity = "high"
        if item["tag"] == "competitor_focus" and any(brand.get("brand_type") == "own_brand" for brand in brands):
            severity = "low"
        flags.append({
            "flag_key": item["tag"],
            "severity": severity,
            "matched_terms": item["matched_terms"],
            "source": "rule_v0",
        })
    if not _text(post.get("post_url")):
        flags.append({"flag_key": "missing_post_url", "severity": "low", "source": "rule_v0"})
    if len(_combined_text(post)) < 80:
        flags.append({"flag_key": "low_text_context", "severity": "low", "source": "rule_v0"})
    return flags


def _summary(post: dict[str, Any], tags: list[dict[str, Any]], products: list[dict[str, Any]], brands: list[dict[str, Any]]) -> str:
    title = _text(post.get("title")) or _text(post.get("caption"))[:90] or "Untitled post"
    tag_text = ", ".join(item["tag"] for item in tags[:3]) or "general content"
    product_text = ", ".join(item["product"] for item in products[:3]) or "no explicit product"
    brand_text = ", ".join(item["brand"] for item in brands[:3]) or "no explicit brand"
    return f"{title[:120]} | tags: {tag_text}; products: {product_text}; brands: {brand_text}."


def _analyze_post(post: dict[str, Any], media_items: list[dict[str, Any]]) -> dict[str, Any]:
    text = _combined_text(post)
    text_lower = text.lower()
    products = _extract_products(text, _loads(post.get("detected_products_json"), []))
    tags = _keyword_hits(text_lower, TAG_KEYWORDS)
    brands = _brand_mentions(text_lower)
    risks = _risk_flags(text_lower, brands, post)
    evidence = [
        {"type": "source_post", "source_table": "vkpi_industry_posts", "source_id": post.get("id"), "detail": _text(post.get("post_url")) or "no url"},
        {"type": "text_context", "detail": f"{len(text)} characters evaluated"},
        {"type": "media_count", "detail": f"{len(media_items)} media rows linked"},
    ]
    confidence = min(0.95, 0.4 + len(tags) * 0.08 + len(products) * 0.08 + len(brands) * 0.05)
    return {
        "post_id": post.get("id"),
        "post_uid": post.get("post_uid"),
        "platform": post.get("platform"),
        "account_id": post.get("account_id"),
        "account_handle": post.get("account_handle"),
        "account_display_name": post.get("account_display_name"),
        "post_url": post.get("post_url"),
        "published_at": post.get("published_at"),
        "source_analysis_status": post.get("analysis_status") or "pending",
        "analysis_version": ANALYSIS_VERSION,
        "analysis_status": "previewed",
        "confidence": round(confidence, 3),
        "content_tags_json": tags,
        "product_intents_json": products,
        "risk_flags_json": risks,
        "brand_mentions_json": brands,
        "ai_summary": _summary(post, tags, products, brands),
        "evidence": evidence,
        "media_preview": [
            {
                "media_id": item.get("id"),
                "media_uid": item.get("media_uid"),
                "media_url": item.get("media_url"),
                "media_type": item.get("media_type"),
                "analysis_status": "previewed",
            }
            for item in media_items[:6]
        ],
    }


def _persist_analysis(items: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    posts_updated = 0
    media_updated = 0
    skipped_done = 0
    try:
        for item in items:
            post_id = _safe_int(item.get("post_id"))
            if not post_id:
                continue
            source_status = _text(item.get("source_analysis_status")).lower()
            if source_status == "done" and not force:
                skipped_done += 1
                continue
            conn.execute(
                """
                UPDATE vkpi_industry_posts
                SET content_tags_json=?,
                    product_intents_json=?,
                    risk_flags_json=?,
                    brand_mentions_json=?,
                    ai_summary=?,
                    analyzed_at=?,
                    analysis_version=?,
                    analysis_status=?,
                    analysis_error=?
                WHERE id=?
                """,
                (
                    json.dumps(item.get("content_tags_json") or [], ensure_ascii=False, default=str),
                    json.dumps(item.get("product_intents_json") or [], ensure_ascii=False, default=str),
                    json.dumps(item.get("risk_flags_json") or [], ensure_ascii=False, default=str),
                    json.dumps(item.get("brand_mentions_json") or [], ensure_ascii=False, default=str),
                    item.get("ai_summary") or "",
                    now,
                    ANALYSIS_VERSION,
                    "done",
                    "",
                    post_id,
                ),
            )
            posts_updated += 1
            for media in item.get("media_preview") or []:
                media_id = _safe_int(media.get("media_id"))
                if not media_id:
                    continue
                conn.execute(
                    """
                    UPDATE vkpi_industry_post_media
                    SET content_tags_json=?,
                        product_intents_json=?,
                        risk_flags_json=?,
                        brand_mentions_json=?,
                        ai_summary=?,
                        analyzed_at=?,
                        analysis_version=?,
                        analysis_status=?,
                        analysis_error=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        json.dumps(item.get("content_tags_json") or [], ensure_ascii=False, default=str),
                        json.dumps(item.get("product_intents_json") or [], ensure_ascii=False, default=str),
                        json.dumps(item.get("risk_flags_json") or [], ensure_ascii=False, default=str),
                        json.dumps(item.get("brand_mentions_json") or [], ensure_ascii=False, default=str),
                        f"Media asset under post #{post_id}: {item.get('ai_summary') or ''}"[:1000],
                        now,
                        ANALYSIS_VERSION,
                        "done",
                        "",
                        now,
                        media_id,
                    ),
                )
                media_updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "posts_updated": posts_updated,
        "media_updated": media_updated,
        "skipped_done": skipped_done,
        "applied_at": now,
    }


def _write_outputs(payload: dict[str, Any], *, json_out: str = "", md_out: str = "") -> None:
    if json_out:
        Path(json_out).write_text(_json({key: value for key, value in payload.items() if key != "markdown"}), encoding="utf-8")
    if md_out:
        Path(md_out).write_text(payload.get("markdown") or "", encoding="utf-8")


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P6 Content Brain Dry-Run",
        "",
        f"Generated at: {payload.get('generated_at')}",
        f"Posts evaluated: {payload.get('posts_evaluated')}",
        f"Returned: {len(payload.get('items') or [])}",
        f"Budget scope: {(payload.get('budget_guard') or {}).get('scope', '')}",
        f"Budget allowed: {str(bool((payload.get('budget_guard') or {}).get('allowed'))).lower()}",
        f"Provider calls: {str(bool(payload.get('provider_calls'))).lower()}",
        f"Writes enabled: {str(bool(payload.get('writes_enabled'))).lower()}",
        f"Write mode: {(payload.get('write_result') or {}).get('mode', 'dry_run')}",
        "",
        "## Tag Distribution",
        "",
    ]
    tag_counts = payload.get("tag_distribution") or {}
    if tag_counts:
        for tag, count in sorted(tag_counts.items(), key=lambda item: (-int(item[1]), item[0])):
            lines.append(f"- {tag}: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Preview Items", ""])
    for item in payload.get("items") or []:
        tags = ", ".join(tag.get("tag", "") for tag in item.get("content_tags_json") or []) or "-"
        products = ", ".join(product.get("product", "") for product in item.get("product_intents_json") or []) or "-"
        risks = ", ".join(flag.get("flag_key", "") for flag in item.get("risk_flags_json") or []) or "-"
        lines.extend([
            f"### Post #{item.get('post_id')} {item.get('platform') or ''}",
            "",
            f"- Account: {item.get('account_display_name') or item.get('account_handle') or '-'}",
            f"- URL: {item.get('post_url') or '-'}",
            f"- Tags: {tags}",
            f"- Products: {products}",
            f"- Risks: {risks}",
            f"- Summary: {item.get('ai_summary') or '-'}",
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def build_content_brain_preview(
    *,
    platform: str = "",
    account_id: int = 0,
    post_id: int = 0,
    query: str = "",
    include_media: bool = False,
    limit: int = 50,
    json_out: str = "",
    md_out: str = "",
    commit_analysis: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    cost_ok = check_budget(BUDGET_SCOPE, 0.0)
    budget_status = get_budget_status(BUDGET_SCOPE, estimated_cost=0.0)
    if not cost_ok:
        raise RuntimeError("budget_guard_blocked")

    posts = _list_posts(platform=platform, account_id=account_id, post_id=post_id, query=query, limit=safe_limit)
    media_map = _media_for_posts([_safe_int(post.get("id")) for post in posts]) if include_media else {}
    items = [_analyze_post(post, media_map.get(_safe_int(post.get("id")), [])) for post in posts]
    tag_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    brand_counts: Counter[str] = Counter()
    for item in items:
        tag_counts.update(tag.get("tag") for tag in item.get("content_tags_json") or [] if tag.get("tag"))
        risk_counts.update(flag.get("flag_key") for flag in item.get("risk_flags_json") or [] if flag.get("flag_key"))
        brand_counts.update(brand.get("brand") for brand in item.get("brand_mentions_json") or [] if brand.get("brand"))

    write_result = {"mode": "dry_run", "posts_updated": 0, "media_updated": 0, "skipped_done": 0}
    if commit_analysis:
        write_result = {"mode": "commit_analysis", **_persist_analysis(items, force=force)}

    payload = {
        "scenario": "p6_content_brain_commit_analysis" if commit_analysis else "p6_content_brain_dry_run",
        "analysis_version": ANALYSIS_VERSION,
        "generated_at": _utcnow(),
        "filters": {
            "platform": platform,
            "account_id": int(account_id or 0),
            "post_id": int(post_id or 0),
            "query": query,
            "include_media": bool(include_media),
            "limit": safe_limit,
            "force": bool(force),
        },
        "budget_guard": {
            "scope": BUDGET_SCOPE,
            "allowed": bool(cost_ok),
            "status": budget_status,
            "estimated_cost_usd": 0.0,
            "recorded_cost": False,
        },
        "writes_enabled": bool(commit_analysis),
        "provider_calls": False,
        "write_result": write_result,
        "posts_evaluated": len(posts),
        "items": items,
        "tag_distribution": dict(sorted(tag_counts.items())),
        "risk_distribution": dict(sorted(risk_counts.items())),
        "brand_distribution": dict(sorted(brand_counts.items())),
    }
    payload["markdown"] = _markdown(payload)
    _write_outputs(payload, json_out=json_out, md_out=md_out)
    return payload


def format_preview_summary(payload: dict[str, Any]) -> str:
    budget = payload.get("budget_guard") or {}
    return "\n".join([
        f"scenario={payload.get('scenario', '')}",
        f"analysis_version={payload.get('analysis_version', '')}",
        f"posts_evaluated={int(payload.get('posts_evaluated') or 0)}",
        f"returned={len(payload.get('items') or [])}",
        f"budget_scope={budget.get('scope', '')}",
        f"budget_allowed={str(bool(budget.get('allowed'))).lower()}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"writes_enabled={str(bool(payload.get('writes_enabled'))).lower()}",
        f"write_mode={(payload.get('write_result') or {}).get('mode', 'dry_run')}",
        f"posts_updated={int((payload.get('write_result') or {}).get('posts_updated') or 0)}",
        f"media_updated={int((payload.get('write_result') or {}).get('media_updated') or 0)}",
        f"skipped_done={int((payload.get('write_result') or {}).get('skipped_done') or 0)}",
        f"tag_types={len(payload.get('tag_distribution') or {})}",
        f"risk_types={len(payload.get('risk_distribution') or {})}",
    ])


def _distribution_from_rows(rows: list[dict[str, Any]], column: str, key_name: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        values = _loads(row.get(column), [])
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                value = _text(item.get(key_name) or item.get("tag") or item.get("flag_key") or item.get("brand") or item.get("product"))
            else:
                value = _text(item)
            if value:
                counter[value] += 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def get_content_brain_status() -> dict[str, Any]:
    """Return read-only P6 analysis coverage and distributions."""
    if not _table_exists("vkpi_industry_posts"):
        return {
            "schema_ready": False,
            "post_count": 0,
            "status_counts": {},
            "tag_distribution": {},
            "risk_distribution": {},
            "brand_distribution": {},
            "budget_guard": get_budget_status(BUDGET_SCOPE, estimated_cost=0.0),
        }
    conn = get_conn()
    status_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(analysis_status, ''), 'pending') AS status,
               COUNT(*) AS count
        FROM vkpi_industry_posts
        GROUP BY COALESCE(NULLIF(analysis_status, ''), 'pending')
        ORDER BY count DESC, status
        """
    ).fetchall()
    status_counts = {str(row["status"]): int(row["count"] or 0) for row in status_rows}
    analyzed_rows = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT content_tags_json, risk_flags_json, brand_mentions_json, product_intents_json
            FROM vkpi_industry_posts
            WHERE COALESCE(NULLIF(analysis_status, ''), 'pending')='done'
            ORDER BY analyzed_at DESC, id DESC
            LIMIT 1000
            """
        ).fetchall()
    ]
    media_count = 0
    if _table_exists("vkpi_industry_post_media"):
        media_row = conn.execute("SELECT COUNT(*) AS count FROM vkpi_industry_post_media").fetchone()
        media_count = int(media_row["count"] or 0) if media_row else 0
    total = sum(status_counts.values())
    done = status_counts.get("done", 0)
    return {
        "schema_ready": True,
        "analysis_version": ANALYSIS_VERSION,
        "budget_scope": BUDGET_SCOPE,
        "budget_guard": get_budget_status(BUDGET_SCOPE, estimated_cost=0.0),
        "post_count": total,
        "media_count": media_count,
        "analyzed_count": done,
        "coverage_ratio": round(done / total, 4) if total else 0.0,
        "status_counts": status_counts,
        "tag_distribution": _distribution_from_rows(analyzed_rows, "content_tags_json", "tag"),
        "risk_distribution": _distribution_from_rows(analyzed_rows, "risk_flags_json", "flag_key"),
        "brand_distribution": _distribution_from_rows(analyzed_rows, "brand_mentions_json", "brand"),
        "product_distribution": _distribution_from_rows(analyzed_rows, "product_intents_json", "product"),
    }


def list_content_brain_posts(
    *,
    status: str = "",
    platform: str = "",
    query: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """List post-level content brain analysis rows for review surfaces."""
    if not _table_exists("vkpi_industry_posts"):
        return {"posts": [], "count": 0, "schema_ready": False}
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("COALESCE(NULLIF(p.analysis_status, ''), 'pending')=?")
        params.append(status)
    if platform:
        where.append("LOWER(p.platform)=LOWER(?)")
        params.append(platform)
    if query:
        token = f"%{query.lower()}%"
        where.append("(LOWER(p.title) LIKE ? OR LOWER(p.caption) LIKE ? OR LOWER(p.post_url) LIKE ? OR LOWER(a.handle) LIKE ?)")
        params.extend([token, token, token, token])
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT p.id, p.post_uid, p.account_id, p.platform, p.platform_post_id,
               p.post_url, p.thumbnail_url, p.media_type, p.title, p.caption,
               p.published_at, p.views, p.likes, p.comments, p.shares,
               COALESCE(NULLIF(p.analysis_status, ''), 'pending') AS analysis_status,
               p.analysis_version, p.analyzed_at, p.analysis_error,
               p.content_tags_json, p.product_intents_json, p.risk_flags_json,
               p.brand_mentions_json, p.ai_summary,
               a.handle AS account_handle,
               a.display_name AS account_display_name,
               a.brand_group AS account_brand_group,
               a.account_role AS account_role
        FROM vkpi_industry_posts p
        LEFT JOIN vkpi_industry_accounts a ON a.id=p.account_id
        {clause}
        ORDER BY
          CASE WHEN p.analyzed_at IS NULL THEN 1 ELSE 0 END,
          p.analyzed_at DESC,
          CASE WHEN p.published_at IS NULL THEN 1 ELSE 0 END,
          p.published_at DESC,
          p.id DESC
        LIMIT ?
        """,
        (*params, _safe_limit(limit, default=100, ceiling=500)),
    ).fetchall()
    posts: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row)
        item["content_tags"] = _loads(item.get("content_tags_json"), [])
        item["product_intents"] = _loads(item.get("product_intents_json"), [])
        item["risk_flags"] = _loads(item.get("risk_flags_json"), [])
        item["brand_mentions"] = _loads(item.get("brand_mentions_json"), [])
        posts.append(item)
    return {
        "posts": posts,
        "count": len(posts),
        "schema_ready": True,
        "filters": {
            "status": status,
            "platform": platform,
            "query": query,
            "limit": _safe_limit(limit, default=100, ceiling=500),
        },
    }
