"""件③ SKU×内容表现 + SKU 360° 档案 —— 纯聚合已有数据,零新采集、零 LLM。

回答两个问题:
  1. sku_content_performance(sku):谁的内容提到/评测了该 SKU(final_v1 layer1 产品识别
     + evidence 标题匹配 + content_fit_v1 缓存),按互动排序的内容清单 + 聚合指标。
  2. sku_profile(sku):360° 档案 = vkpi_products 基础信息 + 内容表现
     + 相关评论抽样(vkpi_comments 文本匹配型号)+ B&H reviews(表存在才读)。

数据源(全只读):
  - vkpi_products / vkpi_product_aliases   SKU 目录 + 归一化别名(alias_norm)
  - vkpi_analysis_cache                    video_analysis_final_v1(layer1 产品识别)
                                           + content_fit_v1(KOL×SKU 契合判断缓存)
  - vkpi_kol_video_evidence                标题/曝光/互动/发布时间
  - vkpi_kol_pool                          创作者展示字段(handle/display_name/platform)
  - vkpi_comments                          评论文本 + language_detected
  - vkpi_bh_reviews                        B&H 用户评论(迁移 207;表存在才读)

匹配口径:双端归一化(product_aliases.normalize_alias)后按 token 边界子串命中;
命中来源分级 final_v1_products > final_v1_presence > evidence_title
> final_v1_brand_exposure > final_v1_summary,逐条回报 matched_alias / matched_field。

红线:零触 viltrox_fit_score / rule_v0;缺数据诚实空态(matched=0 照实返回),绝不杜撰。
compat 注意:SQL 全 ? 占位;ILIKE 模式必须走绑定参数(SQL 字面量里的百分号会炸兼容层);
SQL 内不写注释(避 ASCII 问号陷阱)。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.products.sku_performance")

MIN_ALIAS_CONFIDENCE = 0.5
MAX_ALIASES = 40
MAX_CONTENT_ITEMS = 100
MAX_COMMENT_SAMPLE = 20
MAX_BH_SAMPLE = 10
MAX_FIT_MATCHES = 20

_MATCH_FIELD_PRIORITY = (
    "final_v1_products",
    "final_v1_presence",
    "evidence_title",
    "final_v1_brand_exposure",
    "final_v1_summary",
)


# ── 基础工具 ─────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any, limit: int = 600) -> str:
    return str(value or "").strip()[:limit]


def _loads(value: Any) -> Any:
    if value in (None, "", b""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_str(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _norm(value: Any) -> str:
    from app.domains.products.product_aliases import normalize_alias

    return normalize_alias(value)


def _table_exists(table_name: str) -> bool:
    from app.domains.products.product_aliases import _table_exists as impl

    try:
        return bool(impl(table_name))
    except Exception:
        return False


# ── SKU 解析 + 别名 ──────────────────────────────────────────────────


_PRODUCT_COLS = (
    "sku, category_main, category_detail, model_name, marketing_name, price_usd, "
    "status, description, series, mount, product_url, specs_json, fit_tags_json, updated_at"
)


def _product_basic(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": _text(row.get("sku"), 120),
        "model_name": _text(row.get("model_name"), 300),
        "marketing_name": _text(row.get("marketing_name"), 300),
        "category_main": _text(row.get("category_main"), 80),
        "category_detail": _text(row.get("category_detail"), 120),
        "series": _text(row.get("series"), 80),
        "mount": _text(row.get("mount"), 80),
        "price_usd": _float_or_none(row.get("price_usd")),
        "status": _text(row.get("status"), 40),
    }


def resolve_sku(sku_or_code: str) -> dict[str, Any] | None:
    """SKU 码 / 别名 / 型号片段 → vkpi_products 单行;找不到诚实返回 None。"""
    from app.db.connection import get_conn

    needle = _text(sku_or_code, 200)
    if not needle:
        return None
    conn = get_conn()

    row = conn.execute(
        f"SELECT {_PRODUCT_COLS} FROM vkpi_products WHERE LOWER(sku)=LOWER(?) LIMIT 1",
        (needle,),
    ).fetchone()
    if row:
        return dict(row)

    norm = _norm(needle)
    if norm and _table_exists("vkpi_product_aliases"):
        row = conn.execute(
            """
            SELECT p.sku, p.category_main, p.category_detail, p.model_name, p.marketing_name,
                   p.price_usd, p.status, p.description, p.series, p.mount, p.product_url,
                   p.specs_json, p.fit_tags_json, p.updated_at
            FROM vkpi_product_aliases a
            JOIN vkpi_products p ON p.sku = a.sku
            WHERE a.alias_norm = ?
            ORDER BY a.confidence DESC, p.sku ASC
            LIMIT 1
            """,
            (norm,),
        ).fetchone()
        if row:
            return dict(row)

    pattern = f"%{needle}%"
    row = conn.execute(
        f"""
        SELECT {_PRODUCT_COLS} FROM vkpi_products
        WHERE sku ILIKE ? OR model_name ILIKE ? OR marketing_name ILIKE ?
        ORDER BY LENGTH(model_name) ASC, sku ASC
        LIMIT 1
        """,
        (pattern, pattern, pattern),
    ).fetchone()
    if row:
        return dict(row)

    if not norm:
        return None
    rows = conn.execute(
        f"SELECT {_PRODUCT_COLS} FROM vkpi_products LIMIT 1000",
    ).fetchall()
    best: dict[str, Any] | None = None
    best_len = 10**9
    for r in rows:
        data = dict(r)
        hay = _norm(
            " | ".join(
                str(data.get(k) or "")
                for k in ("sku", "model_name", "marketing_name")
            )
        )
        if norm in hay and len(hay) < best_len:
            best, best_len = data, len(hay)
    return best


def _aliases_for(product: dict[str, Any]) -> list[dict[str, Any]]:
    """该 SKU 的匹配别名(alias_norm 去重,置信度降序);表缺失时现算。"""
    from app.db.connection import get_conn

    sku = _text(product.get("sku"), 120)
    raw: list[dict[str, Any]] = []
    if _table_exists("vkpi_product_aliases"):
        rows = get_conn().execute(
            """
            SELECT alias, alias_norm, alias_type, confidence
            FROM vkpi_product_aliases
            WHERE sku = ?
            ORDER BY confidence DESC, LENGTH(alias_norm) DESC
            """,
            (sku,),
        ).fetchall()
        raw = [dict(r) for r in rows]
    if not raw:
        try:
            from app.domains.products.product_aliases import generated_aliases_for_product

            raw = generated_aliases_for_product(dict(product))
        except Exception:
            logger.warning("sku_performance.alias_generation_failed sku=%s", sku, exc_info=True)
            raw = []

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        alias_norm = _text(item.get("alias_norm"), 200)
        conf = _float_or_none(item.get("confidence")) or 0.0
        if not alias_norm or alias_norm in seen:
            continue
        if conf < MIN_ALIAS_CONFIDENCE:
            continue
        if len(alias_norm) < 5:
            continue
        if not any(ch.isdigit() for ch in alias_norm) and len(alias_norm) < 10:
            continue
        seen.add(alias_norm)
        out.append(
            {
                "alias": _text(item.get("alias"), 200),
                "alias_norm": alias_norm,
                "alias_type": _text(item.get("alias_type"), 40),
                "confidence": round(conf, 2),
            }
        )
        if len(out) >= MAX_ALIASES:
            break
    return out


class _AliasMatcher:
    """归一化后按 token 边界找别名;置信度降序 + 长度降序优先报最具体命中。"""

    def __init__(self, aliases: list[dict[str, Any]]) -> None:
        ordered = sorted(
            aliases,
            key=lambda a: (-float(a.get("confidence") or 0.0), -len(str(a.get("alias_norm") or ""))),
        )
        self._compiled: list[tuple[dict[str, Any], re.Pattern[str]]] = []
        for item in ordered:
            alias_norm = str(item.get("alias_norm") or "")
            if not alias_norm:
                continue
            pattern = re.compile(r"(?<![a-z0-9])" + re.escape(alias_norm) + r"(?![a-z0-9])")
            self._compiled.append((item, pattern))

    @property
    def empty(self) -> bool:
        return not self._compiled

    def match(self, text_norm: str) -> dict[str, Any] | None:
        if not text_norm:
            return None
        for item, pattern in self._compiled:
            if pattern.search(text_norm):
                return item
        return None


# ── SKU 列表(前端选择器) ────────────────────────────────────────────


def list_skus(*, query: str = "", limit: int = 30) -> dict[str, Any]:
    from app.db.connection import get_conn

    q = _text(query, 120)
    lim = max(1, min(200, int(limit or 30)))
    conn = get_conn()
    if q:
        pattern = f"%{q}%"
        rows = conn.execute(
            """
            SELECT sku, model_name, marketing_name, category_main, category_detail,
                   series, mount, price_usd, status
            FROM vkpi_products
            WHERE sku ILIKE ? OR model_name ILIKE ? OR marketing_name ILIKE ? OR series ILIKE ?
            ORDER BY LENGTH(model_name) ASC, sku ASC
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, lim),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT sku, model_name, marketing_name, category_main, category_detail,
                   series, mount, price_usd, status
            FROM vkpi_products
            ORDER BY updated_at DESC, sku ASC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    items = [_product_basic(dict(r)) for r in rows]
    return {"status": "ok", "query": q, "count": len(items), "items": items}


# ── 内容表现:final_v1 产品识别 + evidence 标题匹配 ───────────────────


def _presence_texts(product_presence_raw: Any) -> tuple[str, str]:
    """product_presence 可能是自由文本或 dict(products/notes);拆成(产品清单文本, 描述文本)。"""
    parsed = _loads(product_presence_raw)
    if isinstance(parsed, dict):
        products = parsed.get("products")
        products_text = " ".join(str(x) for x in products if x) if isinstance(products, list) else ""
        note_bits = [
            str(parsed.get("notes") or ""),
            " ".join(str(x) for x in parsed.get("selling_points_shown") or [] if x)
            if isinstance(parsed.get("selling_points_shown"), list)
            else "",
        ]
        return products_text, " ".join(bit for bit in note_bits if bit)
    return "", _text(product_presence_raw, 2000)


def _marketing_value(raw: Any) -> float | None:
    direct = _float_or_none(raw)
    if direct is not None:
        return direct
    parsed = _loads(raw)
    if isinstance(parsed, dict):
        return _float_or_none(parsed.get("score"))
    return None


def _deep_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            ac.id AS cache_id,
            e.id AS evidence_id,
            e.kol_pool_id,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), e.content_url) AS title,
            e.content_url,
            e.platform,
            e.posted_at,
            e.publish_date,
            e.view_count,
            e.like_count,
            e.comment_count,
            p.handle,
            p.display_name,
            p.platform AS kol_platform,
            ac.result #>> '{layer1_visual_content,product_presence}' AS product_presence,
            ac.result #>> '{layer1_visual_content,brand_exposure}' AS brand_exposure,
            ac.result #>> '{layer1_visual_content,content_summary}' AS content_summary,
            ac.result #>> '{layer6_flags_and_scores,scores,marketing_value_score}' AS marketing_value_raw
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE ac.target_type = 'video'
          AND ac.derive_method = 'video_analysis_final_v1'
          AND ac.status = 'ready'
        ORDER BY ac.id DESC
        LIMIT 2000
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def _title_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            e.kol_pool_id,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, '')) AS title,
            e.content_url,
            e.platform,
            e.posted_at,
            e.publish_date,
            e.view_count,
            e.like_count,
            e.comment_count,
            p.handle,
            p.display_name,
            p.platform AS kol_platform
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.is_active = TRUE
          AND (NULLIF(e.title, '') IS NOT NULL OR NULLIF(e.video_title, '') IS NOT NULL)
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        LIMIT 8000
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def _build_item(row: dict[str, Any], matched: dict[str, Any], field: str, *, deep: bool) -> dict[str, Any]:
    likes = _int_or_none(row.get("like_count"))
    comments = _int_or_none(row.get("comment_count"))
    engagement = (likes or 0) + (comments or 0) if (likes is not None or comments is not None) else None
    return {
        "evidence_id": _int_or_none(row.get("evidence_id")),
        "title": _text(row.get("title"), 300),
        "content_url": _text(row.get("content_url"), 500),
        "platform": _text(row.get("platform") or row.get("kol_platform"), 40),
        "posted_at": _date_str(row.get("publish_date") or row.get("posted_at")),
        "view_count": _int_or_none(row.get("view_count")),
        "like_count": likes,
        "comment_count": comments,
        "engagement": engagement,
        "kol": {
            "kol_pool_id": _int_or_none(row.get("kol_pool_id")),
            "handle": _text(row.get("handle"), 120),
            "display_name": _text(row.get("display_name"), 160),
            "platform": _text(row.get("kol_platform"), 40),
        },
        "has_deep_analysis": bool(deep),
        "marketing_value_score": _marketing_value(row.get("marketing_value_raw")) if deep else None,
        "match": {
            "alias": _text(matched.get("alias"), 200),
            "field": field,
            "confidence": _float_or_none(matched.get("confidence")),
        },
    }


def _match_deep_row(row: dict[str, Any], matcher: _AliasMatcher) -> tuple[dict[str, Any], str] | None:
    products_text, presence_text = _presence_texts(row.get("product_presence"))
    haystacks = {
        "final_v1_products": products_text,
        "final_v1_presence": presence_text,
        "evidence_title": _text(row.get("title"), 400),
        "final_v1_brand_exposure": _text(row.get("brand_exposure"), 2000),
        "final_v1_summary": _text(row.get("content_summary"), 2000),
    }
    for field in _MATCH_FIELD_PRIORITY:
        hay = haystacks.get(field) or ""
        if not hay:
            continue
        matched = matcher.match(_norm(hay))
        if matched:
            return matched, field
    return None


def _aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "content_count": 0,
            "creator_count": 0,
            "total_views": 0,
            "total_likes": 0,
            "total_comments": 0,
            "avg_engagement_rate": None,
            "top_creators": [],
        }
    total_views = sum(i["view_count"] or 0 for i in items)
    total_likes = sum(i["like_count"] or 0 for i in items)
    total_comments = sum(i["comment_count"] or 0 for i in items)
    rate = round((total_likes + total_comments) / total_views, 4) if total_views > 0 else None

    by_kol: dict[int, dict[str, Any]] = {}
    for item in items:
        kol = item.get("kol") or {}
        kid = kol.get("kol_pool_id")
        if kid is None:
            continue
        slot = by_kol.setdefault(
            int(kid),
            {
                "kol_pool_id": int(kid),
                "handle": kol.get("handle") or "",
                "display_name": kol.get("display_name") or "",
                "platform": kol.get("platform") or "",
                "content_count": 0,
                "total_views": 0,
                "total_engagement": 0,
            },
        )
        slot["content_count"] += 1
        slot["total_views"] += item["view_count"] or 0
        slot["total_engagement"] += item["engagement"] or 0
    top = sorted(by_kol.values(), key=lambda s: (-s["total_views"], -s["total_engagement"]))[:5]
    return {
        "content_count": len(items),
        "creator_count": len(by_kol),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "avg_engagement_rate": rate,
        "top_creators": top,
    }


def _content_fit_matches(conn: Any, sku: str) -> list[dict[str, Any]]:
    """content_fit_v1 缓存里 product_context.sku 命中该 SKU 的 KOL 级契合判断(只读)。"""
    rows = conn.execute(
        """
        SELECT ac.target_id, ac.updated_at, ac.result AS result,
               p.handle, p.display_name, p.platform
        FROM vkpi_analysis_cache ac
        LEFT JOIN vkpi_kol_pool p ON p.id::text = ac.target_id
        WHERE ac.target_type = 'kol'
          AND ac.derive_method = 'content_fit_v1'
          AND ac.status = 'ready'
          AND LOWER(COALESCE(ac.result #>> '{product_context,sku}', '')) = LOWER(?)
        ORDER BY ac.updated_at DESC
        LIMIT ?
        """,
        (sku, MAX_FIT_MATCHES),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        data = dict(r)
        result = _loads(data.get("result")) or {}
        if not isinstance(result, dict):
            result = {}
        reasons = result.get("fit_reasons")
        out.append(
            {
                "kol_pool_id": _int_or_none(data.get("target_id")),
                "handle": _text(data.get("handle"), 120),
                "display_name": _text(data.get("display_name"), 160),
                "platform": _text(data.get("platform"), 40),
                "fit_verdict": _text(result.get("fit_verdict"), 40),
                "confidence": _float_or_none(result.get("confidence")),
                "fit_reasons": [_text(x, 300) for x in reasons if str(x).strip()][:4]
                if isinstance(reasons, list)
                else [],
                "updated_at": _text(data.get("updated_at"), 40),
            }
        )
    return out


def _content_performance(product: dict[str, Any], aliases: list[dict[str, Any]]) -> dict[str, Any]:
    from app.db.connection import get_conn

    conn = get_conn()
    matcher = _AliasMatcher(aliases)
    diagnostics: dict[str, Any] = {"aliases_used": len(aliases), "deep_rows_scanned": 0, "title_rows_scanned": 0}
    items: list[dict[str, Any]] = []
    seen_evidence: set[int] = set()

    if not matcher.empty:
        deep_rows = _deep_rows(conn)
        diagnostics["deep_rows_scanned"] = len(deep_rows)
        for row in deep_rows:
            eid = _int_or_none(row.get("evidence_id"))
            if eid is None or eid in seen_evidence:
                continue
            hit = _match_deep_row(row, matcher)
            if not hit:
                continue
            matched, field = hit
            seen_evidence.add(eid)
            items.append(_build_item(row, matched, field, deep=True))

        title_rows = _title_rows(conn)
        diagnostics["title_rows_scanned"] = len(title_rows)
        for row in title_rows:
            eid = _int_or_none(row.get("evidence_id"))
            if eid is None or eid in seen_evidence:
                continue
            matched = matcher.match(_norm(_text(row.get("title"), 400)))
            if not matched:
                continue
            seen_evidence.add(eid)
            items.append(_build_item(row, matched, "evidence_title", deep=False))

    items.sort(key=lambda i: (-(i["engagement"] if i["engagement"] is not None else -1), -(i["view_count"] or 0)))
    aggregate = _aggregate(items)
    fit_matches = _content_fit_matches(conn, _text(product.get("sku"), 120))

    return {
        "items": items[:MAX_CONTENT_ITEMS],
        "aggregate": aggregate,
        "content_fit_matches": fit_matches,
        "diagnostics": diagnostics,
        "note": ""
        if items
        else "该 SKU 暂无匹配内容(视频深析与标题均未命中别名);数据为诚实空态,非杜撰。",
    }


def sku_content_performance(sku_or_code: str) -> dict[str, Any]:
    """谁的内容提到/评测该 SKU:按互动排序的内容清单 + 聚合 + content_fit 命中。"""
    product = resolve_sku(sku_or_code)
    if not product:
        return {"status": "not_found", "query": _text(sku_or_code, 200), "generated_at": _utcnow()}
    aliases = _aliases_for(product)
    perf = _content_performance(product, aliases)
    return {
        "status": "ok",
        "product": _product_basic(product),
        "aliases_sample": [a["alias"] for a in aliases[:10]],
        "content": perf,
        "generated_at": _utcnow(),
    }


# ── 评论 + B&H reviews ───────────────────────────────────────────────


def _matched_comments(conn: Any, matcher: _AliasMatcher) -> dict[str, Any]:
    if matcher.empty:
        return {"matched_total": 0, "sample": [], "scanned": 0}
    rows = conn.execute(
        """
        SELECT id, platform, comment_text, author_handle, likes_count,
               language_detected, created_at, post_table
        FROM vkpi_comments
        WHERE comment_text IS NOT NULL AND comment_text <> ''
        ORDER BY likes_count DESC NULLS LAST, id DESC
        LIMIT 5000
        """,
    ).fetchall()
    matched: list[dict[str, Any]] = []
    for r in rows:
        data = dict(r)
        hit = matcher.match(_norm(_text(data.get("comment_text"), 2000)))
        if not hit:
            continue
        matched.append(
            {
                "comment_id": _int_or_none(data.get("id")),
                "platform": _text(data.get("platform"), 40),
                "comment_text": _text(data.get("comment_text"), 400),
                "author_handle": _text(data.get("author_handle"), 120),
                "likes_count": _int_or_none(data.get("likes_count")),
                "language_detected": _text(data.get("language_detected"), 16),
                "created_at": _date_str(data.get("created_at")),
                "matched_alias": _text(hit.get("alias"), 200),
            }
        )
    matched.sort(key=lambda c: -(c["likes_count"] or 0))
    return {"matched_total": len(matched), "sample": matched[:MAX_COMMENT_SAMPLE], "scanned": len(rows)}


def _bh_reviews(conn: Any, sku: str, matcher: _AliasMatcher) -> dict[str, Any]:
    if not _table_exists("vkpi_bh_reviews"):
        return {"table_present": False, "matched_total": 0, "avg_rating": None, "sample": []}
    rows = conn.execute(
        """
        SELECT id, product_url, product_name, sku, rating, title, body, author, review_date
        FROM vkpi_bh_reviews
        ORDER BY id DESC
        LIMIT 3000
        """,
    ).fetchall()
    matched: list[dict[str, Any]] = []
    for r in rows:
        data = dict(r)
        row_sku = _text(data.get("sku"), 120)
        is_hit = bool(row_sku) and row_sku.lower() == sku.lower()
        matched_alias = ""
        if not is_hit and not matcher.empty:
            hit = matcher.match(_norm(_text(data.get("product_name"), 300)))
            if hit:
                is_hit = True
                matched_alias = _text(hit.get("alias"), 200)
        if not is_hit:
            continue
        matched.append(
            {
                "review_id": _int_or_none(data.get("id")),
                "product_name": _text(data.get("product_name"), 300),
                "rating": _float_or_none(data.get("rating")),
                "title": _text(data.get("title"), 200),
                "body": _text(data.get("body"), 500),
                "author": _text(data.get("author"), 120),
                "review_date": _text(data.get("review_date"), 40),
                "matched_alias": matched_alias or "sku_exact",
            }
        )
    ratings = [m["rating"] for m in matched if m["rating"] is not None]
    avg = round(sum(ratings) / len(ratings), 2) if ratings else None
    return {
        "table_present": True,
        "matched_total": len(matched),
        "avg_rating": avg,
        "sample": matched[:MAX_BH_SAMPLE],
    }


# ── 产品知识库画像(vkpi_product_persona 只读透传) ───────────────────


def sku_persona(sku_or_code: str) -> dict[str, Any]:
    """产品知识库画像:vkpi_product_persona 单行只读透传(本函数零模型调用)。

    该表由离线批跑 build_product_personas.py 走 llm_gateway 生成;
    model / source / generated_at 原样回传给前端做来源标注。
    表无数值置信度列 → 不编造 confidence;未生成画像 → persona=None 诚实空态。
    红线:零触 viltrox_fit_score / rule_v0,纯读零写。
    """
    product = resolve_sku(sku_or_code)
    if not product:
        return {"status": "not_found", "query": _text(sku_or_code, 200), "generated_at": _utcnow()}

    from app.domains.costs.product_persona import get_product_persona

    try:
        persona = get_product_persona(_text(product.get("sku"), 120))
    except Exception:
        logger.warning("sku_performance.persona_read_failed sku=%s", product.get("sku"), exc_info=True)
        persona = None
    return {
        "status": "ok",
        "product": _product_basic(product),
        "persona": persona,
        "generated_at": _utcnow(),
    }


# ── 360° 档案 ────────────────────────────────────────────────────────


def sku_profile(sku_or_code: str) -> dict[str, Any]:
    """SKU 360°:目录基础信息 + 内容表现 + 相关评论抽样 + B&H reviews(表存在才读)。"""
    from app.db.connection import get_conn

    product = resolve_sku(sku_or_code)
    if not product:
        return {"status": "not_found", "query": _text(sku_or_code, 200), "generated_at": _utcnow()}

    specs = _loads(product.get("specs_json"))
    fit_tags = _loads(product.get("fit_tags_json"))
    basic = _product_basic(product)
    basic.update(
        {
            "description": _text(product.get("description"), 1500),
            "product_url": _text(product.get("product_url"), 500),
            "specs": specs if isinstance(specs, dict) else {},
            "fit_tags": fit_tags if isinstance(fit_tags, list) else [],
            "catalog_updated_at": _date_str(product.get("updated_at")),
        }
    )

    aliases = _aliases_for(product)
    matcher = _AliasMatcher(aliases)
    conn = get_conn()

    perf = _content_performance(product, aliases)
    comments = _matched_comments(conn, matcher)
    bh = _bh_reviews(conn, basic["sku"], matcher)

    return {
        "status": "ok",
        "product": basic,
        "aliases_sample": [a["alias"] for a in aliases[:10]],
        "content": perf,
        "comments": comments,
        "bh_reviews": bh,
        "generated_at": _utcnow(),
    }
