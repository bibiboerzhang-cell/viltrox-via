"""件C · 发射台六输出组装路由。

- GET /api/admin/vkpi/launch/assemble?sku=&max_roster=
  → 新品 SKU 一键全案:① KOL 名单(招牌拍法+焦段覆盖) ② 每人预算 ③ 排期
    ④ 每人打法 ⑤ 官号协同 v0 ⑥ 覆盖最大化 + 每人预测战绩。
  实现在 app.domains.projects.launch_assembly(六成是编排已有件,零 LLM、零写库)。

诚实态:SKU 不存在 404;兄弟件(A预测/B报价/D组合)未落地段 status=module_pending;
聚合内部异常不 500,回 {status:"error", reason}(前端诚实展示,不假装有数据)。
路由:GET /launch/assemble 与既有 vkpi_launch.py 的 POST /launch/{project_id}/plan
方法+路径均不冲突。红线:纯读编排,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-launch-assembly"])


@router.get("/launch/assemble")
def assemble_launch(
    sku: str = Query(..., min_length=1, max_length=200),
    max_roster: int = Query(default=8, ge=1, le=12),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """新品 SKU 一键全案(六段编排,全只读,不写库)。"""
    del staff
    from app.domains.projects import launch_assembly

    try:
        return launch_assembly.assemble_launch_plan(sku, max_roster=int(max_roster))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 编排失败不炸接口,诚实回原因
        logger.warning("assemble_launch_plan failed for sku=%s: %s", sku, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(sku)[:120]}
