"""V-KPI Agent 自治驾照路由(件A,作战地图廿一节「挣来的自治」)。

- GET  /api/admin/vkpi/autonomy/licenses                          全部驾照 + 台账实时读数(read)
- POST /api/admin/vkpi/autonomy/evaluate?dry_run=true|false       升降评估;dry_run=true 只出建议(write)
- POST /api/admin/vkpi/autonomy/licenses/{action_type}/override   人工调级,必须带 reason(write)

实现在 app.domains.agents.autonomy_license(懒 import):升降纯规则判定零 LLM;
台账数据守卫 import 兄弟件D prediction_ledger,缺席诚实 hold;表未建不炸。

诚实态:校验失败 400;聚合内部异常不 500,回 {status:"error", reason}(前端安静降级)。
红线:「影响评分」维度永久 False 且不可经本路由任何端点修改;L4 绝不自动授予(仅人工
override);本轮只建判定与展示,绝不真执行任何外部动作(发邮件/花钱/写第三方);
零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-autonomy"])


@router.get("/autonomy/licenses")
def list_autonomy_licenses(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """全部驾照(每类动作:当前级/五维度/命中率快照/最近变更)+ 台账实时读数。"""
    del staff
    from app.domains.agents import autonomy_license

    try:
        return autonomy_license.list_licenses()
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("list_licenses failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "items": []}


@router.post("/autonomy/evaluate")
def evaluate_autonomy(
    dry_run: bool = Query(True, description="true 只出建议不落库;false 才把 promote/demote 写库"),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """全量升降评估:命中率>=0.85 且样本>=20 升1级(封顶L3);连续5次未命中降1级(保底L0)。"""
    del staff
    from app.domains.agents import autonomy_license

    try:
        return autonomy_license.evaluate_promotions(dry_run=bool(dry_run))
    except Exception as exc:  # noqa: BLE001 — 评估失败诚实回原因,不 500
        logger.warning("evaluate_promotions failed (dry_run=%s): %s", dry_run, exc)
        return {"status": "error", "reason": str(exc)[:300], "dry_run": bool(dry_run), "items": []}


@router.post("/autonomy/licenses/{action_type}/override")
def override_autonomy_license(
    action_type: str,
    body: dict[str, Any],
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """人工调级(唯一能到 L4 的路径):body 必须带 level(0-4)与 reason;
    「影响评分」维度不在可调范围,永久 False。"""
    from app.domains.agents import autonomy_license

    staff_id = None
    try:
        staff_id = int((staff or {}).get("id") or 0) or None
    except Exception:  # noqa: BLE001 — staff 形态兜底,调级不因取 id 失败而挡
        staff_id = None

    payload = body or {}
    try:
        return autonomy_license.manual_override(
            action_type,
            payload.get("level"),
            str(payload.get("reason") or ""),
            staff_id=staff_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 写失败(如迁移212未 apply)诚实回原因,不 500
        logger.warning("manual_override failed for %s: %s", action_type, exc)
        return {"status": "error", "reason": str(exc)[:300], "action_type": str(action_type)}
