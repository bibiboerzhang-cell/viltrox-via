"""V-KPI 产品 persona 知识库读取 helper(地基A)。

只读端:供搜索 planner 在 SKU→深度产品分析阶段取「产品是什么 / 理想创作者人群 /
推广方向」锚点。写端由离线批跑 backend/scripts_local/build_product_personas.py 负责
(走 llm_gateway.invoke,upsert vkpi_product_persona)。

红线:
- 本模块零写 fit、零写任何评分列;viltrox_fit_score 唯一写点仍是 pool.py enrich_item。
- rule_v0 打分公式冻结;本模块只读 LLM 生成的 persona 文本/标签。
- 本轮只建 helper,planner 接线留整合(避撞 w7fress05 的 smart_query_planner / profile_recall)。

Postgres 生产表来自 migration 119(JSONB)。本地 sqlite 仅作幂等 guard,避免离线/测试读崩。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

logger = get_logger(__name__)

_SQLITE_SCHEMA_READY = False

_JSON_COLUMNS = (
    "key_specs_json",
    "ideal_creator_types_json",
    "verticals_json",
    "promotion_angles_json",
    "avoid_types_json",
)


def _ensure_sqlite_guard() -> None:
    """Idempotent local-sqlite mirror of migration 119 so offline reads do not error.

    Postgres production runs migration 119 via connection._run_postgres_migrations; this
    guard only fires on the local sqlite runtime and never on Postgres.
    """
    global _SQLITE_SCHEMA_READY
    if _SQLITE_SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_product_persona (
            product_sku TEXT PRIMARY KEY,
            category TEXT,
            what_is TEXT,
            key_specs_json TEXT NOT NULL DEFAULT '{}',
            ideal_persona TEXT,
            ideal_creator_types_json TEXT NOT NULL DEFAULT '[]',
            verticals_json TEXT NOT NULL DEFAULT '[]',
            promotion_angles_json TEXT NOT NULL DEFAULT '[]',
            avoid_types_json TEXT NOT NULL DEFAULT '[]',
            model TEXT,
            source TEXT NOT NULL DEFAULT 'llm_persona_v1',
            generated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vkpi_product_persona_category ON vkpi_product_persona(category)"
    )
    conn.commit()
    _SQLITE_SCHEMA_READY = True


def _coerce_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _normalize_row(row: Any) -> dict[str, Any]:
    record = dict(row)
    record["key_specs_json"] = _coerce_json(record.get("key_specs_json"), {})
    for column in _JSON_COLUMNS[1:]:
        record[column] = _coerce_json(record.get(column), [])
    return record


def get_product_persona(sku: str) -> dict[str, Any] | None:
    """Return the persona record for a single SKU, or None when no persona exists yet.

    JSON columns are parsed into native dict/list; the caller never re-parses. Read-only:
    this helper opens no write path and touches no fit/score column.
    """
    normalized_sku = str(sku or "").strip()
    if not normalized_sku:
        return None
    _ensure_sqlite_guard()
    try:
        row = get_conn().execute(
            """
            SELECT product_sku, category, what_is, key_specs_json, ideal_persona,
                   ideal_creator_types_json, verticals_json, promotion_angles_json,
                   avoid_types_json, model, source, generated_at
            FROM vkpi_product_persona
            WHERE product_sku = ?
            """,
            (normalized_sku,),
        ).fetchone()
    except Exception:
        logger.warning("vkpi.product_persona.read_failed sku=%s", normalized_sku, exc_info=True)
        return None
    if row is None:
        return None
    return _normalize_row(row)


def get_product_personas(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Batch read personas for many SKUs, keyed by product_sku. Missing SKUs are omitted."""
    wanted = [str(item or "").strip() for item in (skus or []) if str(item or "").strip()]
    if not wanted:
        return {}
    _ensure_sqlite_guard()
    placeholders = ",".join("?" for _ in wanted)
    try:
        rows = get_conn().execute(
            f"""
            SELECT product_sku, category, what_is, key_specs_json, ideal_persona,
                   ideal_creator_types_json, verticals_json, promotion_angles_json,
                   avoid_types_json, model, source, generated_at
            FROM vkpi_product_persona
            WHERE product_sku IN ({placeholders})
            """,
            tuple(wanted),
        ).fetchall()
    except Exception:
        logger.warning("vkpi.product_persona.batch_read_failed", exc_info=True)
        return {}
    return {str(row["product_sku"]): _normalize_row(row) for row in rows}


def persona_coverage() -> dict[str, Any]:
    """Operator helper: how many SKUs have a persona vs how many products exist."""
    _ensure_sqlite_guard()
    conn = get_conn()
    try:
        persona_total = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_product_persona").fetchone()["n"])
    except Exception:
        persona_total = 0
    try:
        product_total = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_products").fetchone()["n"])
    except Exception:
        product_total = 0
    return {
        "products": product_total,
        "personas": persona_total,
        "missing": max(0, product_total - persona_total),
    }


__all__ = ["get_product_persona", "get_product_personas", "persona_coverage"]
