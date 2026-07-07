"""V-KPI Agent 闭环串跑路由(B4 件②)。

- POST /api/admin/vkpi/agents/loop/run-demo?dry_run=true|false
    整链六步串跑一遍并留痕(inbox 建议 → 驾照判权 → 批准 → 执行 ledger → 结果登记 → 记忆),
    返回每步真实落点表与行 id。dry_run=true(默认)零执行零业务写;
    dry_run=false 仅放行零外部副作用白名单类(受理留痕型),白名单外诚实 blocked。
- GET  /api/admin/vkpi/agents/loop/trace?limit=
    最近串跑记录(读步⑥的 retrospective 记忆行,detail 内存整链落点表)。

实现在 app.domains.agents.loop_runner(懒 import)。诚实态:无建议 / 缺表回
{status:"empty", reason};内部异常不 500,回 {status:"error", reason}。
红线:零 LLM;绝不触 viltrox_fit_score / rule_v0;dry_run=False 也只允许白名单内
零外部副作用动作(不外呼、不花钱、不发信)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-agent-loop"])


@router.post("/agents/loop/run-demo")
def run_demo_loop(
    dry_run: bool = Query(True, description="true(默认)模拟串跑零执行;false 仅白名单零副作用动作真跑"),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """整链六步串跑一遍并留痕:返回每步真实落点表(表名+行 id),证明闭环链路通。"""
    from app.domains.agents import loop_runner

    try:
        return loop_runner.run_demo_loop(dry_run=bool(dry_run), staff=staff)
    except Exception as exc:  # noqa: BLE001 — 串跑失败诚实回原因,不 500
        logger.warning("run_demo_loop failed (dry_run=%s): %s", dry_run, exc, exc_info=True)
        return {"status": "error", "reason": str(exc)[:300], "dry_run": bool(dry_run), "steps": []}


@router.get("/agents/loop/trace")
def loop_trace(
    limit: int = Query(10, ge=1, le=50, description="最近 N 条串跑记录"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """最近串跑记录:每条含整链六步落点表与行 id(trace 行自身即步⑥落点)。"""
    del staff
    from app.domains.agents import loop_runner

    try:
        return loop_runner.recent_traces(limit=int(limit))
    except Exception as exc:  # noqa: BLE001 — 读失败诚实回原因,不 500
        logger.warning("loop_trace failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "items": []}
