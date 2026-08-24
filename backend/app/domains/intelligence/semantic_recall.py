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


def _vector_recall_kol(
    query: str,
    limit: int,
    *,
    provider_free: bool = False,
) -> list[dict[str, Any]]:
    """KOL 向量召回(profile_recall / Qdrant + embedding)。

    解决词法对中文/跨语言/语义查询返 0 的硬伤(_tokenize 把整句压成一个 token 永不匹配)。
    任何失败(Qdrant 单进程锁被占 / 嵌入超时 / 空集)→ 返回 [],由调用方优雅降级到词法。
    分数按召回排名给(向量已排序),语义相关项自然排在词法之上。绝不并入 viltrox_fit_score。
    """
    try:
        from app.domains.kol import profile_recall

        r = profile_recall.recall_kol_profiles(
            query_text=str(query),
            limit=int(limit),
            provider_free=bool(provider_free),
        )
        items = r.get("items") if isinstance(r, dict) else None
        if not items:
            return []
        out: list[dict[str, Any]] = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            rid = it.get("kol_pool_id") or it.get("id")
            title = (str(it.get("handle") or it.get("display_name") or f"kol #{rid}").strip())[:120]
            out.append({
                "kind": "kol", "id": rid, "title": title,
                "score": round(max(0.1, 1.0 - i * 0.05), 4),  # 排名保序,向量项强于词法
                "recall": "provider_free_pool_text" if provider_free else "vector",
                "platform": it.get("platform"),
            })
        return out[: max(1, int(limit))]
    except Exception:
        logger.debug("semantic_recall.vector_kol_failed", exc_info=True)
        return []


def unified_recall(
    query: str,
    *,
    kinds: tuple[str, ...] = ("kol", "video", "project", "event"),
    limit: int = 10,
    staff: dict[str, Any] | None = None,
    provider_free: bool = False,
) -> dict[str, Any]:
    """一句话跨信号召回。返回各信号 top 命中 + 统一排序。

    KOL 走向量召回(语义,解中文/跨语言),失败优雅降级词法;其余信号词法。recall_method 如实标。
    """
    valid = [k for k in kinds if k in _KINDS]
    if staff is not None and any(kind != "kol" for kind in valid):
        from app.domains.staff import is_manager_staff

        if not is_manager_staff(staff):
            raise PermissionError(
                "cross-signal recall requires manager access until row-level scopes are complete"
            )
    q = str(query or "").strip()
    tokens = _tokenize(q)
    if not tokens:
        return {"status": "empty_query", "query": q, "results": [], "recall_method": "none"}
    per_kind: dict[str, list[dict[str, Any]]] = {}
    flat: list[dict[str, Any]] = []
    used_vector = False
    for k in valid:
        items: list[dict[str, Any]] = []
        if k == "kol":
            items = _vector_recall_kol(q, limit, provider_free=provider_free)
            if items and not provider_free:
                used_vector = True
        if not items:
            items = _recall_kind(k, tokens, limit)
        if k == "kol" and items:
            from app.domains.kol.pool_read_projection import project_pool_recall_items

            items = project_pool_recall_items(get_conn(), items)
        per_kind[k] = items
        flat.extend(items)
    flat.sort(key=lambda x: x["score"], reverse=True)
    return {
        "status": "ok",
        "query": q,
        "tokens": tokens,
        "recall_method": (
            "provider_free_pool_text+lexical"
            if provider_free
            else "vector_kol+lexical"
            if used_vector
            else "lexical_v0"
        ),
        "by_kind": {k: len(v) for k, v in per_kind.items()},
        "results": flat[: max(1, min(int(limit or 10), 50))],
        "note": (
            "KOL 本地池文本召回(provider-free)+其余词法兜底;不调用 embedding/Qdrant/LLM;绝不并入 viltrox_fit_score。"
            if provider_free
            else "KOL 向量召回(Qdrant/embedding 语义,解中文)+ 其余词法兜底;向量失败自动降级;绝不并入 viltrox_fit_score。"
        ),
    }
