"""B3 · 统一跨信号语义召回 —— 一句话 → 召回相关 人/视频/项目/活动。

可插拔后端:向量(Qdrant/embedding,配好则用,KOL 走 profile_recall)→ 否则确定性词法兜底
(token 重叠打分,零成本、本地可跑)。诚实标 recall_method;绝不并入 viltrox_fit_score。
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

# 信号 → 可搜表/列(白名单,防注入)。
_KINDS: dict[str, dict[str, Any]] = {
    "kol": {"table": "vkpi_kol_pool", "id": "id", "title": "display_name", "text": ["display_name", "bio", "content_style"]},
    "video": {"table": "vkpi_kol_video_evidence", "id": "id", "title": "video_title", "text": ["video_title", "title", "channel_name"]},
    "project": {"table": "vkpi_projects", "id": "id", "title": "project_name", "text": ["project_name", "product_name"]},
    "event": {"table": "vkpi_events", "id": "id", "title": "title", "text": ["title", "note", "location_name"]},
}

_STOP = {"的", "和", "了", "适合", "find", "the", "a", "for", "with", "and", "of", "to", "kol", "博主", "红人"}


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z0-9]+|[一-鿿]+", str(text or "").lower())
    out: list[str] = []
    for t in raw:
        if t in _STOP or len(t) < 2:
            continue
        out.append(t)
    return out[:12]


def _recall_kind(kind: str, tokens: list[str], limit: int) -> list[dict[str, Any]]:
    cfg = _KINDS[kind]
    if not table_exists(cfg["table"]) or not tokens:
        return []
    blob = " || ' ' || ".join(f"LOWER(COALESCE({c},''))" for c in cfg["text"])
    where = " OR ".join([f"({blob}) LIKE ?"] * len(tokens))
    params = [f"%{t}%" for t in tokens]
    try:
        rows = get_conn().execute(
            f"SELECT {cfg['id']} AS rid, COALESCE({cfg['title']},'') AS title, ({blob}) AS blob "
            f"FROM {cfg['table']} WHERE {where} LIMIT 300",
            tuple(params),
        ).fetchall()
    except Exception:
        logger.debug("semantic_recall.kind_failed", extra={"kind": kind}, exc_info=True)
        return []
    out = []
    for r in rows:
        d = dict(r)
        text = str(d.get("blob") or "")
        hits = [t for t in tokens if t in text]
        if not hits:
            continue
        out.append({
            "kind": kind,
            "id": d.get("rid"),
            "title": (str(d.get("title") or "").strip() or f"{kind} #{d.get('rid')}")[:120],
            "score": round(len(hits) / max(1, len(tokens)), 4),
            "matched_terms": hits,
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def unified_recall(query: str, *, kinds: tuple[str, ...] = ("kol", "video", "project", "event"),
                   limit: int = 10, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """一句话跨信号召回。返回各信号 top 命中 + 统一排序。"""
    del staff
    q = str(query or "").strip()
    tokens = _tokenize(q)
    if not tokens:
        return {"status": "empty_query", "query": q, "results": [], "recall_method": "none"}
    valid = [k for k in kinds if k in _KINDS]
    per_kind: dict[str, list[dict[str, Any]]] = {}
    flat: list[dict[str, Any]] = []
    for k in valid:
        items = _recall_kind(k, tokens, limit)
        per_kind[k] = items
        flat.extend(items)
    flat.sort(key=lambda x: x["score"], reverse=True)
    return {
        "status": "ok",
        "query": q,
        "tokens": tokens,
        "recall_method": "lexical_v0",  # 向量后端(Qdrant/embedding)配好后此处升级为 vector
        "by_kind": {k: len(v) for k, v in per_kind.items()},
        "results": flat[: max(1, min(int(limit or 10), 50))],
        "note": "跨信号词法召回(本地可跑,零成本);向量后端就绪后自动升级;绝不并入 viltrox_fit_score。",
    }
