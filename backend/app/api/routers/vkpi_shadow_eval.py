"""V-KPI 影子评测路由(L 轨道:「挑战者赢旧版才上线」)。

- GET  /api/admin/vkpi/learning/shadow-evals
  → 注册表里全部影子评测的元数据列表(名称/口径/挑战者/对照组)。
- POST /api/admin/vkpi/learning/shadow-evals/{eval_name}/run
  → 跑一轮该评测(纯读端回测计算,零写库、零 LLM、零外部动作),
    返回 challenger vs baseline 同指标对比 + verdict(双赢才建议上线)。
  实现在 app.domains.learning.shadow_eval(决定性遍历,两次运行 fingerprint 一致)。

诚实态:未注册评测 404;评测内部异常不 500,回 {status:"error", reason}
(评测是增益能力,失败不炸面板)。
红线:全只读,POST 只触发计算不落库;影子结论只输出建议,绝不自动切换线上规则;
零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-shadow-eval"])


@router.get("/learning/shadow-evals")
def list_shadow_evals(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """影子评测注册表:有哪些评测可跑(元数据,不触发计算)。"""
    del staff
    from app.domains.learning import shadow_eval

    return {"status": "ready", "evals": shadow_eval.list_shadow_evals()}


@router.post("/learning/shadow-evals/{eval_name}/run")
def run_shadow_eval(
    eval_name: str,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """跑一轮影子评测(纯读端计算,不写库);未注册 404,内部异常诚实回 error。"""
    del staff
    from app.domains.learning import shadow_eval

    try:
        return shadow_eval.run_shadow_eval(str(eval_name))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 评测失败不炸接口,诚实回原因
        logger.warning("shadow eval failed for eval_name=%s: %s", eval_name, exc)
        return {"status": "error", "reason": str(exc)[:300], "eval": str(eval_name)}
