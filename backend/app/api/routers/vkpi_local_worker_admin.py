"""V-KPI 本地算力 Worker 管理路由(W2 件:校验桥手动触发 + 派发策略查看 + 过期回收)。

- POST /api/admin/vkpi/local-workers/lease/{lease_id}/validate
  → 手动触发校验桥:staging 深校验 -> 通过则经既有函数落库(metadata 走
    ensure_video_evidence_from_url,自带分数守卫);body 可带 dry_run=true 只演不写、
    task_token(可选,带了就顺手验一次 W1 契约 token)。
- GET  /api/admin/vkpi/local-workers/policy
  → 派发策略 v0 快照(四类白名单/敏感字段口径/TTL/回收规则/表就绪态)。
- POST /api/admin/vkpi/local-workers/reclaim
  → 过期租约回收 + 孤儿 claimed 标记清扫(任务回归可领),幂等可重复按。

与 W1 路由(vkpi_local_workers.py / vkpi_local_worker_board.py)同 prefix 不同路径,
互不覆盖;三条路由都不改 W1 文件,收口接线见交付 anchors。
红线:纯管理面,绝不写 viltrox_fit_score、绝不触 rule_v0。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi/local-workers", tags=["vkpi-local-workers"])


def _verify_token_best_effort(lease_id: int, task_token: str) -> dict[str, Any]:
    """可选的 W1 契约 token 复验(registry 缺席时诚实说明,绝不装验过)。"""
    try:
        from app.domains.local_workers.registry import verify_task_token
    except ImportError as exc:
        return {"checked": False, "reason": f"registry_unavailable:{exc.__class__.__name__}", "dev_insecure": True}
    try:
        return {"checked": True, "valid": bool(verify_task_token(int(lease_id), str(task_token)))}
    except Exception as exc:  # noqa: BLE001 — 复验崩不拦校验主流程,诚实带原因
        return {"checked": True, "valid": False, "reason": str(exc)[:200]}


@router.post("/lease/{lease_id}/validate")
def validate_lease_submission(
    lease_id: int,
    body: dict[str, Any] | None = Body(default=None),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """手动触发校验桥:深校验 staging 结果,通过则走既有函数落库并回填 lease 行。"""
    del staff
    from app.domains.local_workers import validation

    payload = body if isinstance(body, dict) else {}
    dry_run = bool(payload.get("dry_run"))
    token_check: dict[str, Any] | None = None
    task_token = str(payload.get("task_token") or "")
    if task_token:
        token_check = _verify_token_best_effort(int(lease_id), task_token)

    try:
        out = validation.apply_validation(int(lease_id), dry_run=dry_run)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if token_check is not None:
        out["token_check"] = token_check
    return out


@router.get("/policy")
def get_dispatch_policy(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """派发策略 v0 只读快照(不写库)。"""
    del staff
    from app.domains.local_workers import dispatch_policy

    return dispatch_policy.policy_snapshot()


@router.post("/reclaim")
def reclaim_expired_local_leases(
    body: dict[str, Any] | None = Body(default=None),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """过期租约回收 + 孤儿标记清扫(任务回归可领)。幂等。"""
    del staff
    from app.domains.local_workers import dispatch_policy

    payload = body if isinstance(body, dict) else {}
    try:
        limit = int(payload.get("limit") or 200)
    except (TypeError, ValueError):
        limit = 200
    return dispatch_policy.reclaim_expired_leases(limit=max(1, min(1000, limit)))
