"""发现 → 落库(链1 KOL 自增长)—— 联邦发现的外部候选自动落 vkpi_kol_pool + 去重。

口径(见决策记忆):搜到自动落 Pool;进 MY KOL 仍需手动勾选(落 Pool ≠ 归我)。
红线:新档 source_type=discovered,data 薄诚实;绝不臆造指标;零触 viltrox_fit_score。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def _pool_uid(platform: str, handle: str) -> str:
    return "disc_" + hashlib.sha1(f"{platform}:{handle}".encode("utf-8")).hexdigest()[:16]


def enroll_candidates(candidates: list[dict[str, Any]], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """把外部发现候选落 Pool(已在池/缺键 → 跳过;按 platform+handle 去重)。"""
    del staff
    if not table_exists("vkpi_kol_pool"):
        return {"status": "unavailable", "enrolled": 0, "skipped": 0}
    conn = get_conn()
    enrolled, skipped, ids = 0, 0, []
    for c in candidates or []:
        if c.get("in_pool") or c.get("kol_pool_id"):
            skipped += 1
            continue
        platform = str(c.get("platform") or "").strip().lower()
        handle = str(c.get("handle") or c.get("external_id") or "").strip()
        if not platform or not handle:
            skipped += 1
            continue
        try:
            exists = conn.execute(
                "SELECT id FROM vkpi_kol_pool WHERE platform = ? AND handle = ? LIMIT 1", (platform, handle)
            ).fetchone()
            if exists:
                skipped += 1
                continue
            row = conn.execute(
                "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, display_name, profile_url, source_type, source_ref) "
                "VALUES (?,?,?,?,?,?,?) RETURNING id",
                (_pool_uid(platform, handle), platform, handle,
                 str(c.get("name") or "")[:200], str(c.get("handle") or "")[:500],
                 "discovered", str(c.get("source") or "federation")[:80]),
            ).fetchone()
            conn.commit()
            if row:
                enrolled += 1
                ids.append(int(dict(row)["id"]))
        except Exception:
            logger.warning("enroll.insert_failed", extra={"platform": platform, "handle": handle}, exc_info=True)
            skipped += 1
    return {"status": "ok", "enrolled": enrolled, "skipped": skipped, "enrolled_ids": ids,
            "note": "外部候选已落 Pool(source_type=discovered,数据薄);进 MY KOL 仍需手动勾选;零触 viltrox_fit_score。"}


def federated_discover_and_enroll(query: str, *, limit: int = 20, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """联邦发现 + 自动落库:搜 → 外部候选落 Pool → 返回汇总。"""
    from app.domains.discovery import federation

    found = federation.federated_search(query, limit=limit, staff=staff)
    results = found.get("results", []) if found.get("status") == "ok" else []
    enroll = enroll_candidates(results, staff=staff)
    return {
        "status": "ok",
        "query": query,
        "sources": found.get("sources", {}),
        "found": len(results),
        "enrolled": enroll.get("enrolled", 0),
        "skipped": enroll.get("skipped", 0),
        "enrolled_ids": enroll.get("enrolled_ids", []),
        "note": "联邦发现→自动落 Pool+去重;商业源未配置则只有自有源结果(诚实)。",
    }
