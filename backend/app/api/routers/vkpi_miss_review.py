"""V-KPI 低命中复盘路由(L 轨道 · 失败入记忆)。

- GET  /api/admin/vkpi/learning/miss-review
  → 未命中/失败条目按 action_type 分组 + 词表失败原因聚类 + needs_review 标记。
    实现在 app.domains.learning.miss_review(纯聚合已有失败留痕,零 LLM、零新采集)。
- POST /api/admin/vkpi/learning/miss-review/persist?dry_run=
  → 把每组失败原因摘要经既有 agent_memory_writer.record_signal 写入记忆
    (vkpi_agent_actions,kind=retrospective);dry_run 默认 true 只预览;
    幂等:同组同日不重复写。

诚实态:domain 层永不 raise,聚合失败回 {status:"error", reason};路由不 500。
红线:读端纯聚合;写只经既有 record_signal 落学习留痕表;零触 viltrox_fit_score、
不碰 rule_v0;复盘结论只入记忆供人看,绝不自动改线上规则。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-miss-review"])


@router.get("/learning/miss-review")
def get_miss_review(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """低命中复盘清单(全只读,不写库)。"""
    del staff
    from app.domains.learning import miss_review

    try:
        return miss_review.miss_review_list()
    except Exception as exc:  # noqa: BLE001 — domain 已永不 raise,这里兜底不炸接口
        logger.warning("miss_review_list route failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "groups": []}


@router.post("/learning/miss-review/persist")
def persist_miss_review(
    dry_run: bool = Query(True, description="true=只预览零写库(默认);false=真写入记忆(同组同日幂等跳过)"),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """失败原因入记忆:写只经既有 record_signal(仅学习留痕表 vkpi_agent_actions)。"""
    del staff
    from app.domains.learning import miss_review

    try:
        return miss_review.persist_review_findings(dry_run=bool(dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist_review_findings route failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "dry_run": bool(dry_run), "entries": []}
