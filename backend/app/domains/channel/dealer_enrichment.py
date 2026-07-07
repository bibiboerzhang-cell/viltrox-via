"""C3 Dealer 零售增强域层:vkpi_dealer_enrichment(迁移 226)的读写门面。

职责(全景规格 5.2 节 E 表):
  - record_enrichment:把地区/品类适配/渠道/库存/联系状态/线下转化代理等增强字段挂在
    dealer_id 上;(organization_id, dealer_id) 幂等 UPSERT——重录刷新 updated_at,
    给了新值就刷,没给的 COALESCE 保留旧值(identity 类字段绝不被空值抹掉)。
  - get_enrichment:按 dealer_id 纯读单行(无 → None)。
  - list_enriched:按 market / product_family 过滤纯读列出(updated_at 最新优先)。

数据等 sync:本地 vkpi_dealers 0 行,本表随之为空 —— 代码 + 评分先就位,数据到位即用。
诚实态:表未 apply(迁移 226 并行在建)或零行 → 读回空 / None、写回
  {ok: False, reason: 'table_missing'},绝不抛未捕获异常、绝不编数。

compat 约定:SQL 占位符 ?;零字面 percent(不用 LIKE);jsonb 写走 ?::jsonb + json.dumps。
红线:纯增强账本;零 LLM、零采集;绝不写 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TABLE = "vkpi_dealer_enrichment"

# 商业化前多租户安全字段占位(迁移 226 列默认一致,单租户先缺省)。
DEFAULT_ORG = "viltrox"

DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 1000

# record_enrichment 认识的可选列(未知 kw 只 debug 留痕,绝不静默进库)。
_OPTIONAL_KEYS = (
    "organization_id", "market", "region", "city", "product_family_fit",
    "channel_type", "website_url", "marketplace_url", "contact_status",
    "inventory_signal", "estimated_reach", "response_rate", "conversion_proxy",
    "last_contacted_at", "last_order_at", "source_quality_score",
)


# ── 小工具(compat 宽容层,与 signal_ledger 同款口径) ───────────────


def _text_or_none(value: Any, limit: int = 300) -> str | None:
    text = " ".join(str(value or "").replace("\x00", " ").split())[:limit]
    return text or None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _int0(value: Any) -> int:
    return _int_or_none(value) or 0


def _parse_dt(value: Any) -> datetime | None:
    """时间双态(datetime / ISO str,含尾 Z)→ aware UTC datetime;解析不了诚实 None。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _dt_text(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed else None


def _family_fit_map(value: Any) -> dict[str, Any]:
    """product_family_fit 宽容归一为 dict(读回可能是 dict 或 JSON 串)。"""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


# ── 写入:幂等 UPSERT ────────────────────────────────────────────────


def record_enrichment(dealer_id: int, **kw: Any) -> dict[str, Any]:
    """Dealer 增强入账:(organization_id, dealer_id) 幂等 UPSERT。

    重录语义:同 (org, dealer_id) 再来 → updated_at 刷新为 NOW,product_family_fit
    以最新为准;其余 identity/数值字段 COALESCE——新值给了就刷,没给保留旧值。
    返回 {ok, id|None, upserted: bool};表未建 → {ok: False, reason: 'table_missing'};
    dealer_id 非法 → {ok: False, reason: 'invalid_dealer_id'}。
    """
    did = _int_or_none(dealer_id)
    if did is None or did <= 0:
        return {"ok": False, "id": None, "upserted": False, "reason": "invalid_dealer_id"}

    unknown = sorted(set(kw) - set(_OPTIONAL_KEYS))
    if unknown:
        logger.debug("dealer_enrichment.record_enrichment unknown kwargs ignored: %s", unknown)

    org = _text_or_none(kw.get("organization_id"), 80) or DEFAULT_ORG
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return {"ok": False, "id": None, "upserted": False, "reason": "table_missing"}

        conn = get_conn()
        existing = conn.execute(
            f"SELECT id FROM {TABLE} WHERE organization_id = ? AND dealer_id = ?",
            (org, did),
        ).fetchone()

        row = conn.execute(
            f"""
            INSERT INTO {TABLE} (
                organization_id, dealer_id, market, region, city,
                product_family_fit, channel_type, website_url, marketplace_url,
                contact_status, inventory_signal, estimated_reach, response_rate,
                conversion_proxy, last_contacted_at, last_order_at,
                source_quality_score, updated_at
            ) VALUES (?,?,?,?,?, ?::jsonb,?,?,?, ?,?,?,?, ?,?,?, ?, NOW())
            ON CONFLICT (organization_id, dealer_id) DO UPDATE SET
                market = COALESCE(excluded.market, {TABLE}.market),
                region = COALESCE(excluded.region, {TABLE}.region),
                city = COALESCE(excluded.city, {TABLE}.city),
                product_family_fit = COALESCE(
                    NULLIF(excluded.product_family_fit, '{{}}'::jsonb),
                    {TABLE}.product_family_fit
                ),
                channel_type = COALESCE(excluded.channel_type, {TABLE}.channel_type),
                website_url = COALESCE(excluded.website_url, {TABLE}.website_url),
                marketplace_url = COALESCE(excluded.marketplace_url, {TABLE}.marketplace_url),
                contact_status = COALESCE(excluded.contact_status, {TABLE}.contact_status),
                inventory_signal = COALESCE(excluded.inventory_signal, {TABLE}.inventory_signal),
                estimated_reach = COALESCE(excluded.estimated_reach, {TABLE}.estimated_reach),
                response_rate = COALESCE(excluded.response_rate, {TABLE}.response_rate),
                conversion_proxy = COALESCE(excluded.conversion_proxy, {TABLE}.conversion_proxy),
                last_contacted_at = COALESCE(excluded.last_contacted_at, {TABLE}.last_contacted_at),
                last_order_at = COALESCE(excluded.last_order_at, {TABLE}.last_order_at),
                source_quality_score = COALESCE(excluded.source_quality_score, {TABLE}.source_quality_score),
                updated_at = NOW()
            RETURNING id
            """,
            (
                org, did,
                _text_or_none(kw.get("market"), 40),
                _text_or_none(kw.get("region"), 80),
                _text_or_none(kw.get("city"), 120),
                _dumps(_family_fit_map(kw.get("product_family_fit"))),
                _text_or_none(kw.get("channel_type"), 60),
                _text_or_none(kw.get("website_url"), 600),
                _text_or_none(kw.get("marketplace_url"), 600),
                _text_or_none(kw.get("contact_status"), 60),
                _text_or_none(kw.get("inventory_signal"), 60),
                _float_or_none(kw.get("estimated_reach")),
                _float_or_none(kw.get("response_rate")),
                _float_or_none(kw.get("conversion_proxy")),
                _dt_text(kw.get("last_contacted_at")),
                _dt_text(kw.get("last_order_at")),
                _float_or_none(kw.get("source_quality_score")),
            ),
        ).fetchone()
        conn.commit()
        new_id = _int_or_none(dict(row).get("id")) if row else None
        return {"ok": True, "id": new_id, "upserted": bool(existing)}
    except Exception:
        logger.warning("dealer_enrichment.record_enrichment failed dealer_id=%s", did, exc_info=True)
        return {"ok": False, "id": None, "upserted": False, "reason": "db_error"}


# ── 纯读:单行 / 列表 ───────────────────────────────────────────────


def _row_view(row: dict[str, Any]) -> dict[str, Any]:
    """读回归一:数值宽容、时间 ISO 文本、product_family_fit 归一 dict。"""
    return {
        "id": _int_or_none(row.get("id")),
        "organization_id": _text_or_none(row.get("organization_id"), 80),
        "dealer_id": _int_or_none(row.get("dealer_id")),
        "market": _text_or_none(row.get("market"), 40),
        "region": _text_or_none(row.get("region"), 80),
        "city": _text_or_none(row.get("city"), 120),
        "product_family_fit": _family_fit_map(row.get("product_family_fit")),
        "channel_type": _text_or_none(row.get("channel_type"), 60),
        "website_url": _text_or_none(row.get("website_url"), 600),
        "marketplace_url": _text_or_none(row.get("marketplace_url"), 600),
        "contact_status": _text_or_none(row.get("contact_status"), 60),
        "inventory_signal": _text_or_none(row.get("inventory_signal"), 60),
        "estimated_reach": _float_or_none(row.get("estimated_reach")),
        "response_rate": _float_or_none(row.get("response_rate")),
        "conversion_proxy": _float_or_none(row.get("conversion_proxy")),
        "last_contacted_at": _dt_text(row.get("last_contacted_at")),
        "last_order_at": _dt_text(row.get("last_order_at")),
        "source_quality_score": _float_or_none(row.get("source_quality_score")),
        "updated_at": _dt_text(row.get("updated_at")),
    }


_SELECT_COLS = (
    "id, organization_id, dealer_id, market, region, city, product_family_fit, "
    "channel_type, website_url, marketplace_url, contact_status, inventory_signal, "
    "estimated_reach, response_rate, conversion_proxy, last_contacted_at, "
    "last_order_at, source_quality_score, updated_at"
)


def get_enrichment(dealer_id: int, organization_id: str | None = None) -> dict[str, Any] | None:
    """按 dealer_id 纯读单行增强(无 / 缺表 / 异常 → None)。"""
    did = _int_or_none(dealer_id)
    if did is None:
        return None
    org = _text_or_none(organization_id, 80) or DEFAULT_ORG
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return None
        row = get_conn().execute(
            f"SELECT {_SELECT_COLS} FROM {TABLE} "
            "WHERE organization_id = ? AND dealer_id = ? LIMIT 1",
            (org, did),
        ).fetchone()
        return _row_view(dict(row)) if row else None
    except Exception:
        logger.warning("dealer_enrichment.get_enrichment failed dealer_id=%s", did, exc_info=True)
        return None


def list_enriched(
    market: str | None = None,
    family: str | None = None,
    organization_id: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """按 market / product_family 过滤列出增强行(updated_at 最新优先)。

    family 过滤在 Python 侧判 product_family_fit 是否含该家族键(jsonb 键存在即算命中),
    避开 jsonb 路径 SQL 的方言差异。缺表 / 异常 → []。
    """
    org = _text_or_none(organization_id, 80) or DEFAULT_ORG
    market_clean = _text_or_none(market, 40)
    family_clean = _text_or_none(family, 60)
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return []
        where = ["organization_id = ?"]
        params: list[Any] = [org]
        if market_clean:
            where.append("market = ?")
            params.append(market_clean)
        # family 过滤后仍要满额 → 先按更大的扫描上限取,再在 Python 侧筛。
        scan = max(1, min(_int0(limit) or DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT))
        params.append(scan if not family_clean else MAX_LIST_LIMIT)
        rows = get_conn().execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM {TABLE}
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        views = [_row_view(dict(r)) for r in rows]
        if family_clean:
            fam = family_clean.lower()
            views = [v for v in views if fam in {str(k).lower() for k in v["product_family_fit"]}]
            views = views[:scan]
        return views
    except Exception:
        logger.warning("dealer_enrichment.list_enriched failed market=%s family=%s",
                       market, family, exc_info=True)
        return []


def get_enrichment_map(
    dealer_ids: list[int] | None = None,
    organization_id: str | None = None,
) -> dict[int, dict[str, Any]]:
    """按 dealer_id 批量取增强(评分整合用),返回 {dealer_id: row_view}。

    dealer_ids=None → 拉本 org 全部增强行;缺表 / 异常 → {}(诚实空,评分退回 v0)。
    """
    org = _text_or_none(organization_id, 80) or DEFAULT_ORG
    ids = [i for i in (_int_or_none(x) for x in (dealer_ids or [])) if i is not None]
    if dealer_ids is not None and not ids:
        return {}
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return {}
        where = ["organization_id = ?"]
        params: list[Any] = [org]
        if ids:
            placeholders = ", ".join("?" for _ in ids)
            where.append(f"dealer_id IN ({placeholders})")
            params.extend(ids)
        params.append(MAX_LIST_LIMIT)
        rows = get_conn().execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM {TABLE}
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            view = _row_view(dict(r))
            did = view.get("dealer_id")
            if did is not None and did not in out:
                out[did] = view
        return out
    except Exception:
        logger.warning("dealer_enrichment.get_enrichment_map failed", exc_info=True)
        return {}


__all__ = [
    "record_enrichment",
    "get_enrichment",
    "list_enriched",
    "get_enrichment_map",
    "TABLE",
    "DEFAULT_ORG",
]
