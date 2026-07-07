"""W6 信号账本域层:vkpi_signal_ledger(迁移 219)的读写门面。

职责(执行路线第 2 刀 / 全景规格 5.2 节 A 表):
  - record_signal:所有 Reddit / YouTube 评论 / Google Trends / 竞品官网 / 官号 /
    内部数据先入账再消费;(organization_id, dedupe_key) 幂等 UPSERT,重采刷新
    captured_at / signal_value / momentum 等鲜度字段,identity 类字段 COALESCE 保底。
  - list_signals:按 sku / market / source_type 纯读列出(最新优先)。
  - summarize_for_preview:preview 共同契约——
    {"status": "ready"|"data_missing", "items": [...], "sources_count": int,
     "sample_size": int, "freshest_at": str|None};表缺失或零行时 status='data_missing'
    且绝不抛异常(诚实态宪法;gtm_plan_preview 的 market_opportunity 段消费)。

红线:
  - 表未 apply(迁移 219 并行在建)诚实降级不炸:写返回 {ok: False, reason: 'table_missing'},
    读返回空 / data_missing;
  - items 只出归纳信号(source_type/source_name/摘要/分值),raw_ref/normalized 原始
    情报沉在库里绝不上 preview;
  - 零 LLM、零采集;绝不写 viltrox_fit_score、不碰 rule_v0。
compat 约定:SQL 占位符 ?;零字面 percent(不用 LIKE);jsonb 写走 ?::jsonb + json.dumps;
时间读回宽容(_parse_dt),排序聚合全在 Python 算。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TABLE = "vkpi_signal_ledger"

# 商业化前多租户安全字段占位(迁移 219 列默认一致,单租户先缺省)。
DEFAULT_ORG = "viltrox"

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500
DEFAULT_PREVIEW_LIMIT = 20
MAX_PREVIEW_LIMIT = 100
# summarize 聚合的扫描上限(sources_count/sample_size 按这批算,排序在 Python)。
PREVIEW_SCAN_CAP = 500

# record_signal 认识的可选列(未知 kw 只 debug 留痕,绝不静默进库)。
_OPTIONAL_KEYS = (
    "organization_id", "source_url", "observed_at", "sku", "product_sku",
    "product_family", "market", "platform", "topic", "entity_type", "entity_id",
    "signal_value", "sentiment", "momentum", "sample_size", "freshness_score",
    "source_quality_score", "raw_ref", "normalized",
)


# ── 小工具(compat 宽容层,与 gtm_windows 同款口径) ────────────────


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


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


# ── 写入:幂等 UPSERT ────────────────────────────────────────────────


def record_signal(
    source_type: str,
    source_name: str,
    signal_kind: str,
    signal_text: str,
    dedupe_key: str,
    **kw: Any,
) -> dict[str, Any]:
    """信号入账:(organization_id, dedupe_key) 幂等 UPSERT。

    重采语义:同 dedupe_key 再来 → captured_at 刷新为 NOW,signal_kind/signal_text/
    raw_ref/normalized 以最新采集为准,数值分(signal_value/momentum 等)与 identity
    类字段(sku/market/url 等)COALESCE——新值给了就刷,没给保留旧值。
    返回 {ok, id|None, deduped: bool};表未建 → {ok: False, reason: 'table_missing'}。
    """
    required = {
        "source_type": _text_or_none(source_type, 80),
        "source_name": _text_or_none(source_name, 200),
        "signal_kind": _text_or_none(signal_kind, 80),
        "signal_text": _text_or_none(signal_text, 2000),
        "dedupe_key": _text_or_none(dedupe_key, 300),
    }
    missing = [key for key, val in required.items() if not val]
    if missing:
        return {"ok": False, "id": None, "deduped": False,
                "reason": "missing_required_field", "missing": missing}

    unknown = sorted(set(kw) - set(_OPTIONAL_KEYS))
    if unknown:
        logger.debug("signal_ledger.record_signal unknown kwargs ignored: %s", unknown)

    org = _text_or_none(kw.get("organization_id"), 80) or DEFAULT_ORG
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return {"ok": False, "id": None, "deduped": False, "reason": "table_missing"}

        conn = get_conn()
        existing = conn.execute(
            f"SELECT id FROM {TABLE} WHERE organization_id = ? AND dedupe_key = ?",
            (org, required["dedupe_key"]),
        ).fetchone()

        row = conn.execute(
            f"""
            INSERT INTO {TABLE} (
                organization_id, source_type, source_name, source_url,
                captured_at, observed_at,
                product_sku, product_family, market, platform, topic,
                entity_type, entity_id,
                signal_kind, signal_text, signal_value, sentiment, momentum,
                sample_size, freshness_score, source_quality_score,
                dedupe_key, raw_ref, normalized
            ) VALUES (?,?,?,?, NOW(),?, ?,?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?, ?, ?::jsonb, ?::jsonb)
            ON CONFLICT (organization_id, dedupe_key) DO UPDATE SET
                captured_at = NOW(),
                observed_at = COALESCE(excluded.observed_at, {TABLE}.observed_at),
                source_url = COALESCE(excluded.source_url, {TABLE}.source_url),
                product_sku = COALESCE(excluded.product_sku, {TABLE}.product_sku),
                product_family = COALESCE(excluded.product_family, {TABLE}.product_family),
                market = COALESCE(excluded.market, {TABLE}.market),
                platform = COALESCE(excluded.platform, {TABLE}.platform),
                topic = COALESCE(excluded.topic, {TABLE}.topic),
                entity_type = COALESCE(excluded.entity_type, {TABLE}.entity_type),
                entity_id = COALESCE(excluded.entity_id, {TABLE}.entity_id),
                signal_kind = excluded.signal_kind,
                signal_text = excluded.signal_text,
                signal_value = COALESCE(excluded.signal_value, {TABLE}.signal_value),
                sentiment = COALESCE(excluded.sentiment, {TABLE}.sentiment),
                momentum = COALESCE(excluded.momentum, {TABLE}.momentum),
                sample_size = COALESCE(excluded.sample_size, {TABLE}.sample_size),
                freshness_score = COALESCE(excluded.freshness_score, {TABLE}.freshness_score),
                source_quality_score = COALESCE(excluded.source_quality_score, {TABLE}.source_quality_score),
                raw_ref = excluded.raw_ref,
                normalized = excluded.normalized
            RETURNING id
            """,
            (
                org, required["source_type"], required["source_name"],
                _text_or_none(kw.get("source_url"), 600),
                _dt_text(kw.get("observed_at")),
                _text_or_none(kw.get("sku") or kw.get("product_sku"), 120),
                _text_or_none(kw.get("product_family"), 120),
                _text_or_none(kw.get("market"), 40),
                _text_or_none(kw.get("platform"), 60),
                _text_or_none(kw.get("topic"), 200),
                _text_or_none(kw.get("entity_type"), 60),
                _text_or_none(kw.get("entity_id"), 120),
                required["signal_kind"], required["signal_text"],
                _float_or_none(kw.get("signal_value")),
                _float_or_none(kw.get("sentiment")),
                _float_or_none(kw.get("momentum")),
                _int_or_none(kw.get("sample_size")),
                _float_or_none(kw.get("freshness_score")),
                _float_or_none(kw.get("source_quality_score")),
                required["dedupe_key"],
                _dumps(kw.get("raw_ref")),
                _dumps(kw.get("normalized")),
            ),
        ).fetchone()
        conn.commit()
        new_id = _int_or_none(dict(row).get("id")) if row else None
        return {"ok": True, "id": new_id, "deduped": bool(existing)}
    except Exception:
        logger.warning("signal_ledger.record_signal failed dedupe_key=%s",
                       required["dedupe_key"], exc_info=True)
        return {"ok": False, "id": None, "deduped": False, "reason": "db_error"}


# ── 纯读:列出 ───────────────────────────────────────────────────────


def list_signals(
    sku: str | None = None,
    market: str | None = None,
    source_type: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """按 sku / market / source_type 列出信号(captured_at 最新优先)。缺表/异常 → []。"""
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return []
        where = ["organization_id = ?"]
        params: list[Any] = [DEFAULT_ORG]
        for column, value, cap in (
            ("product_sku", sku, 120),
            ("market", market, 40),
            ("source_type", source_type, 80),
        ):
            cleaned = _text_or_none(value, cap)
            if cleaned:
                where.append(f"{column} = ?")
                params.append(cleaned)
        params.append(max(1, min(_int0(limit) or DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT)))
        rows = get_conn().execute(
            f"""
            SELECT id, organization_id, source_type, source_name, source_url,
                   captured_at, observed_at, product_sku, product_family, market,
                   platform, topic, entity_type, entity_id, signal_kind, signal_text,
                   signal_value, sentiment, momentum, sample_size,
                   freshness_score, source_quality_score, dedupe_key, created_at
            FROM {TABLE}
            WHERE {' AND '.join(where)}
            ORDER BY captured_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.warning("signal_ledger.list_signals failed sku=%s", sku, exc_info=True)
        return []


# ── 纯读:preview 共同契约 ───────────────────────────────────────────


def _preview_item(row: dict[str, Any]) -> dict[str, Any]:
    """items 只出归纳信号;raw_ref/normalized 原始情报绝不上 preview。"""
    return {
        "id": _int_or_none(row.get("id")),
        "source_type": _text_or_none(row.get("source_type"), 80),
        "source_name": _text_or_none(row.get("source_name"), 200),
        "source_url": _text_or_none(row.get("source_url"), 600),
        "signal_kind": _text_or_none(row.get("signal_kind"), 80),
        "signal_text": _text_or_none(row.get("signal_text"), 500),
        "signal_value": _float_or_none(row.get("signal_value")),
        "sentiment": _float_or_none(row.get("sentiment")),
        "momentum": _float_or_none(row.get("momentum")),
        "sample_size": _int_or_none(row.get("sample_size")),
        "market": _text_or_none(row.get("market"), 40),
        "platform": _text_or_none(row.get("platform"), 60),
        "topic": _text_or_none(row.get("topic"), 200),
        "captured_at": _dt_text(row.get("captured_at")),
    }


def _preview_sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
    """momentum 降序(None 沉底),同分按 captured_at 新→旧。"""
    momentum = _float_or_none(row.get("momentum"))
    captured = _parse_dt(row.get("captured_at"))
    ts = captured.timestamp() if captured else float("-inf")
    return (1 if momentum is None else 0, -(momentum or 0.0), -ts)


def summarize_for_preview(sku: str, market: str | None = None, limit: int = DEFAULT_PREVIEW_LIMIT) -> dict[str, Any]:
    """preview 共同契约:该 sku(可选 market)的信号归纳摘要。

    返回 {"status": "ready"|"data_missing", "items": [...], "sources_count": int,
    "sample_size": int, "freshest_at": str|None}。表未建 / 零行 / 任何异常一律
    status='data_missing' 且绝不抛异常(诚实态宪法);items 按 momentum 降序取 top。
    """
    empty = {"status": "data_missing", "items": [], "sources_count": 0,
             "sample_size": 0, "freshest_at": None}
    sku_clean = _text_or_none(sku, 120)
    if not sku_clean:
        return {**empty, "reason": "sku 为空——无法按产品聚合信号。"}
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(TABLE):
            return {**empty, "reason": f"{TABLE} 未建(迁移 219 并行在建);apply 后本段自动点亮。"}

        where = ["organization_id = ?", "product_sku = ?"]
        params: list[Any] = [DEFAULT_ORG, sku_clean]
        market_clean = _text_or_none(market, 40)
        if market_clean:
            where.append("market = ?")
            params.append(market_clean)
        params.append(PREVIEW_SCAN_CAP)
        rows = [dict(r) for r in get_conn().execute(
            f"""
            SELECT id, source_type, source_name, source_url, captured_at,
                   market, platform, topic, signal_kind, signal_text,
                   signal_value, sentiment, momentum, sample_size
            FROM {TABLE}
            WHERE {' AND '.join(where)}
            ORDER BY captured_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()]
    except Exception:
        logger.warning("signal_ledger.summarize_for_preview failed sku=%s", sku, exc_info=True)
        return {**empty, "reason": "信号账本读取失败(见服务端日志)——诚实降级,不拖垮 preview。"}

    if not rows:
        return {**empty, "reason": f"{TABLE} 里没有 sku={sku_clean} 的信号行——零信号诚实空态。"}

    sources = {(_text_or_none(r.get("source_type"), 80), _text_or_none(r.get("source_name"), 200))
               for r in rows}
    # 每行至少代表 1 条观测;自带 sample_size 的按其样本量计。
    sample_size = sum(_int0(r.get("sample_size")) or 1 for r in rows)
    freshest = max((d for d in (_parse_dt(r.get("captured_at")) for r in rows) if d), default=None)
    cap = max(1, min(_int0(limit) or DEFAULT_PREVIEW_LIMIT, MAX_PREVIEW_LIMIT))
    items = [_preview_item(r) for r in sorted(rows, key=_preview_sort_key)[:cap]]

    return {
        "status": "ready",
        "items": items,
        "sources_count": len(sources),
        "sample_size": sample_size,
        "freshest_at": freshest.isoformat() if freshest else None,
    }


__all__ = ["record_signal", "list_signals", "summarize_for_preview", "TABLE", "DEFAULT_ORG"]
