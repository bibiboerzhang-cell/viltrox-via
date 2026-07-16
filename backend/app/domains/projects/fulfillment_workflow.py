"""履约链 Durable Workflow(P2 落业务)—— 把"物流同步→签收开窗→扫内容"跑成可恢复流程。

每步复用既有 service;跑在 workflow_engine 上:失败停在该步、可从该步续跑、每步入 Event Ledger。
红线:步骤只编排既有逻辑,零自动裁决业务、零触 viltrox_fit_score;无真数据时各步诚实返回 0/awaiting。
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

Step = tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]


def _require_fence() -> dict[str, Any]:
    from app.domains.platform import workflow_engine

    return workflow_engine.require_workflow_fence()


def build_fulfillment_steps(staff: dict[str, Any] | None = None) -> list[Step]:
    """履约链 3 步(复用既有 service)。"""

    def s_logistics(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.logistics import seventeen_track

        execution = _require_fence()
        r = seventeen_track.enqueue_logistics_sync_job(project_id=None, staff=staff)
        return {
            "logistics_sync": {
                "status": r.get("status"),
                "reason": r.get("reason", ""),
                "side_effect_key": execution["side_effect_key"],
                "external_exactly_once": False,
            }
        }

    def s_delivered(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.projects import observation_windows

        execution = _require_fence()
        r = observation_windows.scan_delivered_into_windows(staff)
        return {
            "scan_delivered": {
                "opened": r.get("opened") or r.get("created") or 0,
                "scanned": r.get("scanned", 0),
                "side_effect_key": execution["side_effect_key"],
                "external_exactly_once": False,
            }
        }

    def s_content(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.projects import observation_windows

        execution = _require_fence()
        r = observation_windows.scan_windows_for_content(staff)
        return {
            "scan_content": {
                "windows": r.get("windows_scanned") or r.get("scanned") or 0,
                "candidates": r.get("candidates") or r.get("matched") or 0,
                "side_effect_key": execution["side_effect_key"],
                "external_exactly_once": False,
            }
        }

    return [("logistics_sync", s_logistics), ("scan_delivered", s_delivered), ("scan_content", s_content)]


def start_fulfillment_sweep(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """起一轮履约扫:建 run → 跑三步(可恢复)。返回 run 结果 + run_id。"""
    from app.domains.platform import workflow_engine

    started = workflow_engine.start_run("fulfillment_sweep", input={}, entity_type="fulfillment")
    run_id = started.get("run_id")
    if not run_id:
        return {"status": "unavailable", "detail": started}
    res = workflow_engine.run(int(run_id), build_fulfillment_steps(staff))
    return {"run_id": run_id, **res}


def resume_fulfillment_sweep(run_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """续跑一个失败/暂停的履约 run(从 current_step 接上)。"""
    from app.domains.platform import workflow_engine

    return workflow_engine.run(int(run_id), build_fulfillment_steps(staff))
