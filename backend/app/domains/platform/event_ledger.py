"""统一事件总线(P1)—— emit 关键业务事件,让系统靠事件流理解世界。

两轴共用:trace_id(轴A 可追溯)+ organization_id(轴B 多租户就绪,默认 1=Viltrox)。
红线:emit 全 best-effort(写失败只 warning,绝不阻断调用方);纯留痕,零触 viltrox_fit_score。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_event_ledger"


def new_trace_id(*parts: Any) -> str:
    """据输入生成确定 trace_id(同输入同 trace,便于一条流程串联;不用随机)。"""
    raw = "|".join(str(p) for p in parts if p is not None) or "trace"
    return "tr_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def emit(
    event_type: str,
    *,
    entity_type: str = "",
    entity_id: Any = "",
    actor_type: str = "system",
    actor_id: Any = "",
    source: str = "",
    payload: dict[str, Any] | None = None,
    trace_id: str = "",
    confidence: float | None = None,
    provenance: dict[str, Any] | None = None,
    organization_id: int = 1,
) -> int | None:
    """写一条事件(best-effort)。缺表/异常 → 静默跳过,绝不抛。返回 id 或 None。"""
    et = str(event_type or "").strip()
    if not et or not table_exists(_TABLE):
        return None
    try:
        conn = get_conn()
        row = conn.execute(
            f"""
            INSERT INTO {_TABLE}
              (organization_id, event_type, entity_type, entity_id, actor_type, actor_id,
               source, payload_json, trace_id, confidence, provenance_json)
            VALUES (?,?,?,?,?,?,?,?::jsonb,?,?,?::jsonb)
            RETURNING id
            """,
            (
                int(organization_id or 1), et, str(entity_type or ""), str(entity_id or ""),
                str(actor_type or "system"), str(actor_id or ""), str(source or ""),
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                str(trace_id or ""),
                float(confidence) if confidence is not None else None,
                json.dumps(provenance or {}, ensure_ascii=False, default=str),
            ),
        ).fetchone()
        conn.commit()
        return int(dict(row)["id"]) if row else None
    except Exception:
        logger.warning("event_ledger.emit_failed", extra={"event_type": et}, exc_info=True)
        return None


def by_entity(entity_type: str, entity_id: Any, *, limit: int = 50, organization_id: int = 1) -> list[dict[str, Any]]:
    """某实体的事件流(时间倒序)。"""
    if not table_exists(_TABLE):
        return []
    try:
        rows = get_conn().execute(
            f"SELECT id, event_type, actor_type, actor_id, source, payload_json, trace_id, confidence, occurred_at "
            f"FROM {_TABLE} WHERE organization_id = ? AND entity_type = ? AND entity_id = ? "
            f"ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (int(organization_id or 1), str(entity_type or ""), str(entity_id or ""), max(1, min(int(limit or 50), 200))),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("event_ledger.by_entity_failed", exc_info=True)
        return []


def recent(*, limit: int = 50, event_type: str = "", organization_id: int = 1) -> dict[str, Any]:
    """近期事件流(可按 event_type 过滤)。"""
    if not table_exists(_TABLE):
        return {"available": False, "items": []}
    where = ["organization_id = ?"]
    params: list[Any] = [int(organization_id or 1)]
    if event_type:
        where.append("event_type = ?")
        params.append(str(event_type))
    params.append(max(1, min(int(limit or 50), 200)))
    try:
        rows = get_conn().execute(
            f"SELECT id, event_type, entity_type, entity_id, actor_type, source, trace_id, occurred_at "
            f"FROM {_TABLE} WHERE {' AND '.join(where)} ORDER BY occurred_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return {"available": True, "count": len(rows), "items": [dict(r) for r in rows]}
    except Exception:
        logger.debug("event_ledger.recent_failed", exc_info=True)
        return {"available": False, "items": []}
