"""W2 · Action 执行前置校验(双闸之一)。

validate_action(action) -> {"ok":bool, "reason":str, "checks":{...}}
全部检查通过才 ok=True;任一失败立刻短路返回对应 reason。

红线:
- 仅放行已审批(status=='approved')动作 → 未审批的写库/LLM 永不进执行路径。
- 防御性二次校验 touches_v6_fit 必须为 False(表已有 CHECK,这里再兜一层)。
- 成本超 single_call per-request ceiling → 拦(budget_hard_stop)。
- 实体仍存在(按 entity_type 反查)→ 缺失即拦(entity_missing)。
只读 DB,绝不写任何表、绝不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard

logger = get_logger(__name__)

# entity_type → (反查表, id 是否按 str 传)
_ENTITY_TABLES: dict[str, tuple[str, bool]] = {
    "kol": ("vkpi_kol_pool", False),
    "project": ("vkpi_projects", False),
    "job": ("apify_jobs", False),
    "event": ("vkpi_events", True),       # vkpi_events.id 为 VARCHAR/TEXT
    "inventory": ("vkpi_inventory", False),
}


def _fail(reason: str, checks: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "checks": checks}


def validate_action(action: dict[str, Any]) -> dict[str, Any]:
    """执行前置双闸校验。返回 {"ok":bool,"reason":str,"checks":{...}}。"""
    checks: dict[str, Any] = {}

    # 1. 已审批
    status = str((action or {}).get("status") or "")
    approved = status == "approved"
    checks["approved"] = approved
    if not approved:
        return _fail("not_approved", checks)

    # 2. 红线:touches_v6_fit 必须 False(防御性二次校验)
    touches = bool((action or {}).get("touches_v6_fit"))
    checks["touches_v6_fit_false"] = not touches
    if touches:
        return _fail("touches_v6_fit_violation", checks)

    # 3. 成本未超 budget(仅当 est>0 且 uses_llm 才查 single_call per-request ceiling)
    try:
        est = int((action or {}).get("estimated_cost_cents") or 0)
    except (TypeError, ValueError):
        est = 0
    uses_llm = bool((action or {}).get("uses_llm"))
    checks["estimated_cost_cents"] = est
    if est > 0 and uses_llm:
        try:
            within = budget_guard.check_budget("single_call", est / 100.0, require_configured=False)
        except Exception:
            # 预算系统坏 → require_configured=False 语义即「未配置则放行」,这里也放行(不误杀)。
            logger.warning("validators.budget_check_failed", exc_info=True)
            within = True
        checks["budget_ok"] = bool(within)
        if not within:
            return _fail("budget_hard_stop", checks)
    else:
        checks["budget_ok"] = True

    # 4. 实体仍存在(按 entity_type 反查)
    entity_type = str((action or {}).get("entity_type") or "").strip().lower()
    entity_id_raw = (action or {}).get("entity_id")
    if entity_type in _ENTITY_TABLES and entity_id_raw not in (None, ""):
        table, as_str = _ENTITY_TABLES[entity_type]
        if as_str:
            entity_id: Any = str(entity_id_raw)
        else:
            try:
                entity_id = int(str(entity_id_raw).strip())
            except (TypeError, ValueError):
                checks["entity_exists"] = False
                return _fail("entity_missing", checks)
        try:
            row = get_conn().execute(
                f"SELECT 1 FROM {table} WHERE id = ?",  # noqa: S608 - table 来自白名单映射
                (entity_id,),
            ).fetchone()
        except Exception:
            # 反查坏(表缺/列漂移)→ 保守拦(不让一个无法确认存在的实体被执行)。
            logger.warning("validators.entity_lookup_failed", extra={"entity_type": entity_type}, exc_info=True)
            row = None
        exists = row is not None
        checks["entity_exists"] = exists
        if not exists:
            return _fail("entity_missing", checks)
    else:
        # 无实体维度(或未知 entity_type)的动作 → 跳过存在性校验(诚实:无可查)。
        checks["entity_exists"] = True

    return {"ok": True, "reason": "", "checks": checks}
