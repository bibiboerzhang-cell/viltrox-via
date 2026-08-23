"""推荐反馈写口路由(学习闭环 L 车道 · L→F 契约)。

  POST /api/admin/vkpi/recommendations/search-feedback          写:发现墙 / KOL 详情 有用·没用+原因(幂等)
  POST /api/vkpi/recommendations/search-feedback                同上(契约原文路径别名,免前端 404 回退)
  GET  /api/admin/vkpi/recommendations/search-feedback/count    读:已标注数(总量/up/down/按原因/按来源/本人)
  GET  /api/admin/vkpi/recommendations/search-feedback/reasons  读:拒绝原因闭集(稳定 key + 中文默认标签)

权限:写走 require_tab('vkpi','write'),读走 require_tab('vkpi','read')。
闭集校验失败 → 400;迁移 290 未 apply → 200 但 ok=False + reason=migration_290_missing(诚实空态,前端可禁用按钮)。
POST 响应额外带 labeled_count(全局已标注数,前端质量卡直接用)。
零 LLM、零 provider;唯一写表 vkpi_recommendation_feedback(+ outcome 节点促升);绝不写 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.recommendations import search_feedback

# 无 prefix:同一 handler 同时挂契约路径(/api/vkpi)与仓库惯例路径(/api/admin/vkpi)。
router = APIRouter(tags=["vkpi-recommendations"])

ADMIN_PATH = "/api/admin/vkpi/recommendations/search-feedback"
CONTRACT_PATH = "/api/vkpi/recommendations/search-feedback"


def _post_search_feedback(body: dict[str, Any], staff: Any) -> dict[str, Any]:
    try:
        result = search_feedback.record_search_feedback(body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("ok"):
        try:
            result["labeled_count"] = int(search_feedback.count_search_feedback(staff=staff).get("total") or 0)
        except Exception:  # noqa: BLE001 — 计数是增益件,不拖垮写口
            result["labeled_count"] = None
    return result


@router.post(ADMIN_PATH)
def post_search_feedback(
    body: dict[str, Any] = Body(default={}),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    return _post_search_feedback(body, staff)


@router.post(CONTRACT_PATH)
def post_search_feedback_contract_alias(
    body: dict[str, Any] = Body(default={}),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    return _post_search_feedback(body, staff)


@router.get(ADMIN_PATH + "/count")
def get_search_feedback_count(
    source: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    try:
        return search_feedback.count_search_feedback(staff=staff, source=source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(ADMIN_PATH + "/reasons")
def get_search_feedback_reasons(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    del staff
    return {
        "sources": list(search_feedback.SOURCES),
        "verdicts": list(search_feedback.VERDICTS),
        "reasons": search_feedback.reason_options(),
        "reason_required_for": ["down"],
    }
