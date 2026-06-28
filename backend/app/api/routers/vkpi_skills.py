"""Marketing Brain① Skills HTTP 端点 —— run / list / runs。

三个端点(契约与 VOS 页共用,务必一致):
  - POST /api/admin/vkpi/skills/{skill_name}/run   body {input:{...}}
        → {status, output, skill_run_id?}
  - GET  /api/admin/vkpi/skills
        → [{skill_name, version, runs, acceptance_rate, avg_cost_cents, avg_latency_ms}]
  - GET  /api/admin/vkpi/skills/runs?skill_name=&limit=
        → [{id, skill_name, model_used, cost_cents, latency_ms, accepted, created_at}]

双闸:
  ① require_tab("vkpi", ...) —— 读用 read,run 用 write,统一 admin 权限模型;
  ② 绝不触 viltrox_fit_score —— 本路由只做分发 + 只读统计,底层 skills 自身也只读不写 fit。

run 端点把请求按 skill_name 分发到 app.domains.marketing_brain.skills.*.run(input, record=True),
默认 model_fn=None → 不真烧 LLM(走规则/模板),account 安全。落账本由 skill 内部 best-effort 完成。

注册:本文件【不】改 main.py。需主控在 main.py 加一行:
    from app.api.routers import vkpi_skills
    app.include_router(vkpi_skills.router)
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.marketing_brain import skill_registry
from app.domains.marketing_brain.skills import (
    brief_generate_v1,
    campaign_plan,
    content_score,
    creator_match,
    roi_review,
)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-skills"])


# canonical skill_name(对齐各 skill 模块的 SKILL_NAME 常量)→ run 函数。
# 注意:模块名与 SKILL_NAME 不一定同名(brief_generate_v1.py 的 SKILL_NAME 是 "brief_generate"),
# 以 SKILL_NAME 为准,这样 run / runs / list 三端口口径一致。
_SKILL_MODULES = (
    creator_match,
    brief_generate_v1,
    content_score,
    roi_review,
    campaign_plan,
)


from app.core.logging import get_logger
logger = get_logger(__name__)

def _build_dispatch() -> dict[str, Callable[..., dict[str, Any]]]:
    table: dict[str, Callable[..., dict[str, Any]]] = {}
    for mod in _SKILL_MODULES:
        name = str(getattr(mod, "SKILL_NAME", "") or "").strip()
        run_fn = getattr(mod, "run", None)
        if name and callable(run_fn):
            table[name] = run_fn
    return table


_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = _build_dispatch()


def _meta_for(skill_name: str) -> dict[str, Any]:
    """取一个 skill 的版本(只读声明,经 skill_registry.register_skill 合并 tool_registry 元数据)。"""
    try:
        reg = skill_registry.register_skill(skill_name)
        skill = reg.get("skill") if isinstance(reg, dict) else None
        if isinstance(skill, dict):
            return skill
    except Exception:
        logger.debug("suppressed exception (hardening): best-effort", exc_info=True)
        pass
    return {}


def _status_of(output: Any) -> str:
    """从 skill 输出里提取标准 status。skills 输出形态不一(有的用 status,有的用 ok 布尔)。"""
    if isinstance(output, dict):
        st = output.get("status")
        if isinstance(st, str) and st:
            return st
        ok = output.get("ok")
        if ok is True:
            return "ok"
        if ok is False:
            return "error"
        # 无显式 status/ok 字段:有内容即视为 ok。
        return "ok"
    return "ok"


def _latest_run_id(skill_name: str) -> int | None:
    """run 端点落账后回查最近一条 run id(best-effort;缺表/异常返回 None)。

    skills 内部 record_skill_run 不向上回传 run_id,这里读侧补一次最近 id,口径与 runs 端口一致。
    """
    try:
        res = skill_registry.list_skill_runs(skill_name=skill_name, limit=1)
    except Exception:
        return None
    if not isinstance(res, dict) or res.get("status") != "ok":
        return None
    runs = res.get("runs") or []
    if runs and isinstance(runs[0], dict):
        rid = runs[0].get("id")
        try:
            return int(rid) if rid is not None else None
        except (TypeError, ValueError):
            return None
    return None


@router.post("/skills/{skill_name}/run")
def run_skill(
    skill_name: str,
    body: dict[str, Any] | None = None,
    _staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """按 skill_name 分发到对应 skill 的 run(input, record=True)。

    body: {"input": {...}};默认 model_fn=None → 不真烧 LLM。
    返回 {status, output, skill_run_id?}。
    """
    name = str(skill_name or "").strip()
    run_fn = _DISPATCH.get(name)
    if run_fn is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown skill '{name}'; available: {sorted(_DISPATCH.keys())}",
        )
    payload = body if isinstance(body, dict) else {}
    skill_input = payload.get("input")
    if not isinstance(skill_input, dict):
        # 容忍把字段直接平铺在 body 里(无 input 包裹)。
        skill_input = {k: v for k, v in payload.items() if k != "input"}

    try:
        # model_fn 默认 None(skill 自身默认参数),record=True 落账本。
        output = run_fn(skill_input, record=True)
    except Exception as exc:  # skill 内部异常不应 500 裸抛
        raise HTTPException(status_code=500, detail=f"skill '{name}' failed: {exc}") from exc

    resp: dict[str, Any] = {"status": _status_of(output), "output": output}
    run_id = _latest_run_id(name)
    if run_id is not None:
        resp["skill_run_id"] = run_id
    return resp


@router.get("/skills")
def list_skills(_staff=Depends(require_tab("vkpi", "read"))) -> list[dict[str, Any]]:
    """列出所有 skill + 聚合统计(runs / acceptance_rate / avg_cost_cents / avg_latency_ms)。"""
    out: list[dict[str, Any]] = []
    for name in _DISPATCH:
        meta = _meta_for(name)
        version = str(meta.get("skill_version") or meta.get("version") or "v1")
        try:
            stats = skill_registry.skill_acceptance_stats(skill_name=name)
        except Exception:
            stats = {}
        if not isinstance(stats, dict) or stats.get("status") != "ok":
            stats = {}
        out.append(
            {
                "skill_name": name,
                "version": version,
                "runs": int(stats.get("total") or 0),
                "acceptance_rate": stats.get("acceptance_rate"),
                "avg_cost_cents": stats.get("avg_cost_cents"),
                "avg_latency_ms": stats.get("avg_latency_ms"),
            }
        )
    return out


@router.get("/skills/runs")
def list_skill_runs(
    skill_name: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    _staff=Depends(require_tab("vkpi", "read")),
) -> list[dict[str, Any]]:
    """读回运行明细(可按 skill_name 过滤)。投影成契约字段子集。"""
    try:
        res = skill_registry.list_skill_runs(skill_name=skill_name, limit=limit)
    except Exception:
        res = {}
    if not isinstance(res, dict) or res.get("status") != "ok":
        return []
    rows = res.get("runs") or []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "id": r.get("id"),
                "skill_name": r.get("skill_name"),
                "model_used": r.get("model_used"),
                "cost_cents": r.get("cost_cents"),
                "latency_ms": r.get("latency_ms"),
                "accepted": r.get("accepted"),
                "created_at": r.get("created_at"),
            }
        )
    return out
