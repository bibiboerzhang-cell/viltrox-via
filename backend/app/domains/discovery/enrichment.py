"""KOL 富集即证据 —— 外部受众/刷粉/画像/历史数据存为证据(带来源+置信)。

红线:富集只入 vkpi_kol_enrichment,作为独立展示证据;**绝不**并入 viltrox_fit_score / fit 评分。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_kol_enrichment"
_KINDS = {"audience", "fake_follower", "demographics", "historical"}


def record_enrichment(kol_pool_id: int, source: str, kind: str, payload: dict[str, Any],
                      *, confidence: float | None = None, fetched_at: str | None = None) -> int | None:
    """落一行富集证据。非白名单 kind / 缺表 → 静默跳过(诚实),不抛。"""
    k = str(kind or "").strip()
    if k not in _KINDS or not table_exists(_TABLE) or int(kol_pool_id or 0) <= 0:
        return None
    try:
        conn = get_conn()
        row = conn.execute(
            f"INSERT INTO {_TABLE} (kol_pool_id, source, kind, payload_json, confidence, fetched_at) "
            f"VALUES (?,?,?,?::jsonb,?,?) RETURNING id",
            (int(kol_pool_id), str(source or ""), k,
             json.dumps(payload or {}, ensure_ascii=False, default=str),
             float(confidence) if confidence is not None else None, fetched_at),
        ).fetchone()
        conn.commit()
        return int(dict(row)["id"]) if row else None
    except Exception:
        logger.warning("enrichment.record_failed", extra={"kol_pool_id": kol_pool_id, "kind": k}, exc_info=True)
        return None


def get_enrichment(kol_pool_id: int) -> dict[str, Any]:
    """读某 KOL 的全部富集证据,按 kind 分组(每 kind 取最新一条)。"""
    kid = int(kol_pool_id or 0)
    if kid <= 0 or not table_exists(_TABLE):
        return {"kol_pool_id": kid, "available": False, "by_kind": {}}
    try:
        rows = get_conn().execute(
            f"SELECT kind, source, payload_json, confidence, fetched_at, created_at FROM {_TABLE} "
            f"WHERE kol_pool_id = ? ORDER BY created_at DESC",
            (kid,),
        ).fetchall()
    except Exception:
        logger.debug("enrichment.get_failed", exc_info=True)
        return {"kol_pool_id": kid, "available": False, "by_kind": {}}
    by_kind: dict[str, Any] = {}
    for r in rows:
        d = dict(r)
        k = str(d.get("kind") or "")
        if k in by_kind:  # 已有更新的,跳过旧的
            continue
        payload = d.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        by_kind[k] = {"source": d.get("source"), "confidence": d.get("confidence"),
                      "fetched_at": str(d.get("fetched_at") or ""), "data": payload}
    return {
        "kol_pool_id": kid,
        "available": bool(by_kind),
        "by_kind": by_kind,
        "note": "富集证据:外部受众/刷粉/画像/历史;独立展示信号,绝不并入 viltrox_fit_score。",
    }
