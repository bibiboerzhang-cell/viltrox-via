"""S3 · Agent 记忆写入器 —— 把"有价值动作"沉淀进学习闭环表(vkpi_agent_actions)。

每次 收藏 / 拒绝 / 加项目 / 复盘 → record_signal() 留一行(who/why/cost/detail),
让里程碑② 的"权重回写 / 预测"有真历史数据可学。best-effort:写失败只 warning,绝不阻断主流程。
红线:仅写学习留痕表,零触 viltrox_fit_score / rule_v0 / 业务评分域。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_agent_actions"

# 允许的动作类型(白名单,防脏数据)。
_KINDS = {"favorite", "reject", "add_to_project", "retrospective", "approve", "search", "outreach"}


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _actor(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("id", "staff_id", "user_id"):
        try:
            v = int(staff.get(key) or 0)
            if v:
                return v
        except (TypeError, ValueError):
            continue
    return None


def record_signal(
    *,
    action_kind: str,
    entity_type: str,
    entity_id: Any,
    staff: dict[str, Any] | None = None,
    reason: str = "",
    cost_cents: int = 0,
    detail: dict[str, Any] | None = None,
) -> int | None:
    """写一行 vkpi_agent_actions(学习闭环留痕)。返回 id 或 None。best-effort,绝不抛。

    缺表(迁移182未跑)/非白名单动作 → 静默跳过(诚实)。零触评分域。
    """
    kind = str(action_kind or "").strip()
    if kind not in _KINDS or not table_exists(_TABLE):
        return None
    try:
        conn = get_conn()
        row = conn.execute(
            f"""
            INSERT INTO {_TABLE}
              (action_kind, entity_type, entity_id, actor_staff_id, reason, cost_cents, detail_json)
            VALUES (?,?,?,?,?,?,?::jsonb)
            RETURNING id
            """,
            (
                kind,
                str(entity_type or ""),
                str(entity_id or ""),
                _actor(staff),
                str(reason or "")[:500],
                max(0, int(cost_cents or 0)),
                _dumps(detail or {}),
            ),
        ).fetchone()
        conn.commit()
        return int(dict(row)["id"]) if row else None
    except Exception:
        logger.warning("agent_memory_writer.record_failed", extra={"kind": kind, "entity_id": entity_id}, exc_info=True)
        return None


def record_kol_signal(kol_pool_id: Any, action_kind: str, *, staff: dict[str, Any] | None = None, reason: str = "", detail: dict[str, Any] | None = None) -> int | None:
    """便捷:KOL 维度的动作信号(收藏/拒绝/加项目…)。"""
    return record_signal(
        action_kind=action_kind, entity_type="kol", entity_id=kol_pool_id,
        staff=staff, reason=reason, detail=detail,
    )


# ── B5/H4 学习闭环:结果回写 ──────────────────────────────────────────────
_OUTCOME_TABLE = "vkpi_agent_outcome_evaluations"
_OUTCOMES = {"success", "fail", "partial"}


def record_outcome(
    *,
    entity_type: str,
    entity_id: Any,
    outcome: str,
    recommend_again: bool | None = None,
    agent_action_id: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> int | None:
    """写一行 vkpi_agent_outcome_evaluations(动作结果评估)→ 喂推荐权重回流。

    outcome: success|fail|partial。recommend_again 缺省:fail→False,其余 True。
    best-effort,缺表/非法 outcome → 静默跳过,绝不抛。零触 viltrox_fit_score。
    """
    oc = str(outcome or "").strip().lower()
    if oc not in _OUTCOMES or not table_exists(_OUTCOME_TABLE):
        return None
    success = True if oc == "success" else (False if oc == "fail" else None)
    if recommend_again is None:
        recommend_again = oc != "fail"
    try:
        conn = get_conn()
        row = conn.execute(
            f"""
            INSERT INTO {_OUTCOME_TABLE}
              (agent_action_id, entity_type, entity_id, outcome, success, recommend_again, evidence_json)
            VALUES (?,?,?,?,?,?,?::jsonb)
            RETURNING id
            """,
            (
                int(agent_action_id) if agent_action_id else None,
                str(entity_type or ""),
                str(entity_id or ""),
                oc,
                success,
                bool(recommend_again),
                _dumps(evidence or {}),
            ),
        ).fetchone()
        conn.commit()
        return int(dict(row)["id"]) if row else None
    except Exception:
        logger.warning("agent_memory_writer.record_outcome_failed", extra={"entity_id": entity_id}, exc_info=True)
        return None


def _truthy(v: Any) -> bool | None:
    """兼容 BOOLEAN 跨适配器读回(可能是 bool / int 1·0 / 't'·'f')。None 保持 None(partial)。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    if s in {"t", "true", "1", "yes"}:
        return True
    if s in {"f", "false", "0", "no", ""}:
        return False
    return None


def recent_outcome_stats(entity_type: str, entity_id: Any, *, lookback: int = 20) -> dict[str, Any]:
    """读某实体近期结果:{total, success, fail, recommend_again_ratio}。缺表/无记录 → total=0。"""
    if not table_exists(_OUTCOME_TABLE):
        return {"total": 0, "success": 0, "fail": 0, "recommend_again_ratio": None}
    try:
        rows = get_conn().execute(
            f"""
            SELECT outcome, success, recommend_again
            FROM {_OUTCOME_TABLE}
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (str(entity_type or ""), str(entity_id or ""), max(1, min(int(lookback or 20), 200))),
        ).fetchall()
    except Exception:
        logger.debug("agent_memory_writer.recent_outcome_failed", exc_info=True)
        return {"total": 0, "success": 0, "fail": 0, "recommend_again_ratio": None}
    n = len(rows)
    if not n:
        return {"total": 0, "success": 0, "fail": 0, "recommend_again_ratio": None}
    succ = sum(1 for r in rows if _truthy(dict(r).get("success")) is True)
    fail = sum(1 for r in rows if _truthy(dict(r).get("success")) is False)
    again = sum(1 for r in rows if _truthy(dict(r).get("recommend_again")) is True)
    return {"total": n, "success": succ, "fail": fail, "recommend_again_ratio": round(again / n, 4)}
