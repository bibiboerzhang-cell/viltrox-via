"""发现 → 落库(链1 KOL 自增长)—— 联邦发现的外部候选自动落 vkpi_kol_pool + 去重。

口径(见决策记忆):搜到自动落 Pool;进 MY KOL 仍需手动勾选(落 Pool ≠ 归我)。
红线:新档 source_type=discovered,data 薄诚实;绝不臆造指标;零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.repositories.kol_pool_repo import KolPoolRepository

logger = get_logger(__name__)


def enroll_candidates(candidates: list[dict[str, Any]], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """把外部发现候选落 Pool(已在池/缺键 → 跳过;按 platform+handle 去重)。L2:走 KolPoolRepository。"""
    del staff
    repo = KolPoolRepository()
    if not repo.exists():
        return {"status": "unavailable", "enrolled": 0, "skipped": 0}
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
            if repo.find_id_by_platform_handle(platform, handle) is not None:
                skipped += 1
                continue
            new_id = repo.insert_discovered(
                platform=platform, handle=handle,
                name=str(c.get("name") or ""), profile_url=str(c.get("handle") or ""),
                source_ref=str(c.get("source") or "federation"),
            )
            if new_id:
                enrolled += 1
                ids.append(new_id)
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
