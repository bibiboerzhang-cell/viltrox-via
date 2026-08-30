"""B4 · Agent 闭环串跑器(demo loop)—— 零件全在,缺的是整链一次真串跑,本模块补上。

run_demo_loop(dry_run=True) -> dict:把「建议 inbox → 驾照 → 批准 → 执行 ledger → 复盘 → 记忆」
整链六步真串一遍并留痕,每步输出真实落点表与行 id,证明链路通:

  ①取建议   vkpi_action_inbox              读一条真实 suggested 建议(优先零外部副作用白名单类)
  ②验驾照   vkpi_autonomy_licenses         按 category 查 current_level;L0/L1 → 记「需人审」
  ③批准     vkpi_agent_actions             dry_run=模拟批准(不改 inbox 状态)留 approve 信号行;
                                           dry_run=False 且白名单 → inbox.approve_action 真批准
  ④执行留痕 vkpi_action_execution_ledger   dry_run=只记一行 demo 标记(mode='dry_run',零执行);
                                           dry_run=False 且白名单 → executors.execute_action 真跑
  ⑤结果登记 vkpi_agent_outcome_evaluations record_outcome(evidence 带 demo_loop 标记)
  ⑥入记忆   vkpi_agent_actions             record_signal(kind='retrospective', entity_type='demo_loop')
                                           detail 存整链六步落点表 → recent_traces 由此回读

红线:dry_run=True 绝不执行任何外部动作、绝不改任何业务表/inbox 状态(只写 agent 自留痕表);
dry_run=False 也只允许 _ZERO_SIDE_EFFECT_CATEGORIES(受理提醒类,executor 只留痕:
不外呼、不花钱、不发信、不写业务表),白名单外诚实 blocked 拒跑;零 LLM;
绝不读写 viltrox_fit_score、绝不碰 rule_v0。
compat:SQL 占位符 ?;SQL 零字面 percent(不用 LIKE);BOOLEAN _truthy 容错;缺表诚实降级不炸。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_INBOX = "vkpi_action_inbox"
_LEDGER = "vkpi_action_execution_ledger"
_LICENSES = "vkpi_autonomy_licenses"
_AGENT_ACTIONS = "vkpi_agent_actions"
_OUTCOMES = "vkpi_agent_outcome_evaluations"

# trace 行标识(步⑥ record_signal 的 entity_type;recent_traces 按它精确等值回读,零 LIKE)。
TRACE_ENTITY_TYPE = "demo_loop"

# dry_run=False 唯一放行的动作类别:executor 均为「受理留痕」型,零外部副作用
# (不外呼 API、不花钱、不发信、不写任何业务表;见 actions/executors.py 对应 handler)。
_ZERO_SIDE_EFFECT_CATEGORIES: tuple[str, ...] = (
    "event_followup",
    "inventory_low",
    "project_shared_to_you",
)

# inbox category → 驾照 action_type 族映射(仅补全增强:enrich 系动作归 pool_enrich 驾照;
# 映射外用 category 原名查,无驾照行则 current_level 诚实回 L0 观察)。
_LICENSE_FAMILY: dict[str, str] = {
    "discovery_enroll": "pool_enrich",
    "kol_profile": "pool_enrich",
    "deep_missing": "pool_enrich",
}

# 需人审线:级别 <= 1(L0 观察 / L1 建议)只许建议,执行必须过人审。
_HUMAN_REVIEW_MAX_LEVEL = 1


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — 回滚失败不再连环炸
        logger.debug("回滚失败(best-effort)", exc_info=True)


def _step(
    n: int,
    key: str,
    title: str,
    table: str,
    op: str,
    *,
    row_id: int | None,
    ok: bool,
    summary: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """单步落点记录:真实表名 + 行 id + 读/写 + 一句话结果(空则诚实 None + 原因)。"""
    return {
        "step": n,
        "key": key,
        "title": title,
        "table": table,
        "op": op,
        "row_id": row_id,
        "ok": bool(ok),
        "summary": str(summary or "")[:300],
        "detail": detail or {},
    }


# ── 步① 取建议(真实 suggested 行,零臆造)────────────────────────────


def _pick_suggested_action(*, whitelist_only: bool) -> dict[str, Any] | None:
    """取一条真实 suggested 建议。whitelist_only=True 只在零副作用白名单类里挑,
    且用真实 validators 预筛(实体仍存在等,纯只读)——保证真跑路径不撞 stale 建议;
    否则白名单类优先(demo 链路稳定可复跑),再按 priority / 新旧排序。取不到回 None。"""
    from app.db.connection import get_conn

    conn = get_conn()
    marks = ",".join(["?"] * len(_ZERO_SIDE_EFFECT_CATEGORIES))
    cols = (
        "id, dedupe_key, category, title, priority, entity_type, entity_id, "
        "suggested_endpoint, requires_approval, owner_staff_id, status, "
        "touches_v6_fit, estimated_cost_cents, uses_llm"
    )
    try:
        if whitelist_only:
            rows = conn.execute(
                f"""
                SELECT {cols} FROM {_INBOX}
                WHERE status = 'suggested' AND category IN ({marks})
                ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC
                LIMIT 10
                """,
                tuple(_ZERO_SIDE_EFFECT_CATEGORIES),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT {cols} FROM {_INBOX}
                WHERE status = 'suggested'
                ORDER BY
                  CASE WHEN category IN ({marks}) THEN 0 ELSE 1 END,
                  CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  id DESC
                LIMIT 1
                """,
                tuple(_ZERO_SIDE_EFFECT_CATEGORIES),
            ).fetchone()
            rows = [rows] if rows else []
    except Exception:
        _safe_rollback(conn)
        logger.warning("loop_runner.pick_suggested_failed", exc_info=True)
        return None

    candidates = [dict(r) for r in rows if r is not None]
    if not candidates:
        return None
    if not whitelist_only:
        return candidates[0]

    # 真跑路径预筛:复用真实 validators(只读)。status 覆写成 approved 仅为让闸1
    # 通过预演(不写库),其余检查(touches_v6_fit / budget / 实体仍存在)按真逻辑跑,
    # 挑第一条能过闸的 —— stale 建议(实体已删)不进真跑演示,由闸如实拦。
    from app.domains.actions import validators

    for cand in candidates:
        try:
            probe = validators.validate_action({**cand, "status": "approved"})
        except Exception:  # noqa: BLE001 — 预筛炸按不过闸处理,继续看下一条
            logger.warning("loop_runner.precheck_failed", extra={"action_id": cand.get("id")}, exc_info=True)
            continue
        if isinstance(probe, dict) and probe.get("ok"):
            return cand
    return None


# ── 步② 驾照读数(row id 单独查,current_level 契约不含 id)───────────


def _license_row_id(action_type: str) -> int | None:
    from app.db.connection import get_conn

    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT id FROM {_LICENSES} WHERE action_type = ?", (str(action_type),)
        ).fetchone()
    except Exception:
        _safe_rollback(conn)
        return None
    return int(dict(row)["id"]) if row else None


# ── 步④ dry_run demo ledger 行 ───────────────────────────────────────


def _write_demo_ledger(action: dict[str, Any], staff: dict[str, Any] | None, *, dry_run: bool) -> int | None:
    """dry_run 路径:只在 vkpi_action_execution_ledger 记一行 demo 标记,零执行。
    mode='dry_run'(表 CHECK 约束合法值),endpoint='internal:demo_loop' 供人肉排查辨认。"""
    from app.db.connection import get_conn, table_exists
    from app.domains.access import scope

    if not table_exists(_LEDGER):
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            INSERT INTO {_LEDGER}
              (action_id, category, dedupe_key, actor_staff_id, mode, outcome,
               endpoint, cost_cents, error, detail_json, created_at)
            VALUES (?,?,?,?,'dry_run','success','internal:demo_loop',0,'',?::jsonb,NOW())
            RETURNING id
            """,
            (
                int(action.get("id") or 0) or None,
                str(action.get("category") or ""),
                str(action.get("dedupe_key") or ""),
                int(scope.actor_staff_id(staff)) or None,
                _dumps(
                    {
                        "demo_loop": True,
                        "dry_run": bool(dry_run),
                        "simulated": True,
                        "note": "demo loop 串跑留痕:未执行任何外部动作,仅证明 ledger 落点可写",
                    }
                ),
            ),
        ).fetchone()
        conn.commit()
        return int(dict(row)["id"]) if row else None
    except Exception:
        _safe_rollback(conn)
        logger.warning("loop_runner.demo_ledger_failed", extra={"action_id": action.get("id")}, exc_info=True)
        return None


# ── 整链串跑:六步各自的落点构建器 ───────────────────────────────────


def _empty_inbox_result(dry: bool) -> dict[str, Any]:
    """inbox 无可串建议时的诚实空结果(dry 与否给不同原因)。"""
    reason = (
        "白名单类(零外部副作用)当前无 suggested 建议,或候选均未过执行前置校验"
        "(如实体已不存在的 stale 建议),dry_run=False 诚实拒跑"
        if not dry
        else "inbox 当前无 suggested 建议(先跑每日生成或等 producer 产出)"
    )
    return {
        "status": "empty",
        "dry_run": dry,
        "reason": reason,
        "whitelist": list(_ZERO_SIDE_EFFECT_CATEGORIES),
        "steps": [],
        "chain_ok": False,
        "generated_at": _now_iso(),
    }


def _pick_step(action: dict[str, Any], action_id: int, category: str) -> dict[str, Any]:
    """步① 取建议的落点行。"""
    return _step(
        1,
        "inbox_pick",
        "取建议",
        _INBOX,
        "read",
        row_id=action_id,
        ok=True,
        summary=f"取到真实 suggested 建议 #{action_id}({category}):{str(action.get('title') or '')[:80]}",
        detail={"priority": action.get("priority"), "dedupe_key": action.get("dedupe_key")},
    )


def _license_step(category: str) -> tuple[dict[str, Any], int, bool]:
    """步② 驾照 current_level 判权限(L0/L1 → 需人审)。"""
    from app.domains.agents import autonomy_license

    license_type = _LICENSE_FAMILY.get(category, category)
    try:
        lic = autonomy_license.current_level(license_type)
    except Exception as exc:  # noqa: BLE001 — 驾照读数炸不拖垮整链,诚实降级 L0
        logger.warning("loop_runner.license_check_failed", exc_info=True)
        lic = {"status": "error", "reason": str(exc)[:200], "level": 0, "level_label": "观察"}
    level = int(lic.get("level") or 0)
    needs_review = level <= _HUMAN_REVIEW_MAX_LEVEL
    lic_row_id = _license_row_id(license_type)
    lic_status = str(lic.get("status") or "")
    step = _step(
        2,
        "license_check",
        "验驾照",
        _LICENSES,
        "read",
        row_id=lic_row_id,
        ok=True,
        summary=(
            f"驾照 {license_type} = L{level} {str(lic.get('level_label') or '')}"
            +("(需人审:该级只许建议,执行须人批)" if needs_review else "(级别足以内部执行)")
            + ("" if lic_status == "ready" else f";驾照读数 {lic_status}:{str(lic.get('reason') or '')[:80]}")
        ),
        detail={
            "license_action_type": license_type,
            "mapped_from_category": category,
            "level": level,
            "needs_human_review": needs_review,
            "license_status": lic_status,
        },
    )
    return step, level, needs_review


def _real_approve(action_id: int, staff: dict[str, Any] | None) -> tuple[bool, str]:
    """dry_run=False 的真批准:approve_action 结果与失败原因如实带回。"""
    from app.domains.actions import inbox as action_inbox

    real_approved = False
    try:
        ap = action_inbox.approve_action(
            action_id, staff, reason="demo_loop:白名单零外部副作用动作,整链验证真批准"
        )
        real_approved = bool(isinstance(ap, dict) and ap.get("ok"))
        approve_note = "" if real_approved else f"approve_action 未成功:{str((ap or {}).get('reason') or '')[:120]}"
    except Exception as exc:  # noqa: BLE001 — 批准失败如实记录,后续步骤诚实降级
        approve_note = f"approve_action 异常:{str(exc)[:120]}"
    return real_approved, approve_note


def _approval_step(
    action_id: int,
    category: str,
    dry: bool,
    staff: dict[str, Any] | None,
    level: int,
    needs_review: bool,
    real_approved: bool,
    approve_note: str,
) -> dict[str, Any]:
    """步③ 批准信号留痕(dry=模拟批准不改 inbox;真批准结果由调用方传入)。"""
    from app.domains.memory import agent_memory_writer

    signal_id = agent_memory_writer.record_signal(
        action_kind="approve",
        entity_type="action",
        entity_id=action_id,
        staff=staff,
        reason=(
            f"demo_loop {'真批准(白名单零副作用)' if real_approved else '模拟批准(dry_run,不改 inbox 状态)'};"
            f"驾照 L{level} {'需人审' if needs_review else ''}"
        ),
        detail={
            "demo_loop": True,
            "dry_run": dry,
            "real_approved": real_approved,
            "needs_human_review": needs_review,
            "category": category,
        },
    )
    approve_ok = signal_id is not None and (dry or real_approved)
    return _step(
        3,
        "approval",
        "批准",
        _AGENT_ACTIONS,
        "write",
        row_id=signal_id,
        ok=approve_ok,
        summary=(
            ("真批准成功(inbox 行状态 → approved)+ approve 信号留痕" if real_approved else "模拟批准留痕(dry_run:inbox 状态原样不动)")
            if approve_ok
            else (approve_note or "approve 信号行写入失败(表缺或写库异常)")
        ),
        detail={"real_approved": real_approved, "note": approve_note},
    )


def _execution_step(
    action: dict[str, Any], action_id: int, dry: bool, staff: dict[str, Any] | None
) -> tuple[dict[str, Any], str, int | None]:
    """步④ 执行留痕:dry_run=demo ledger 行(零执行);dry_run=False=真 executor(双闸守门)。"""
    exec_outcome = "success"
    ledger_id: int | None = None
    exec_detail: dict[str, Any] = {}
    if dry:
        ledger_id = _write_demo_ledger(action, staff, dry_run=True)
        exec_ok = ledger_id is not None
        exec_summary = (
            f"demo 标记行落 ledger(mode=dry_run, endpoint=internal:demo_loop),未执行任何外部动作"
            if exec_ok
            else "demo ledger 行写入失败(表缺或写库异常)"
        )
    else:
        from app.domains.actions import executors

        try:
            res = executors.execute_action(action_id, staff)
        except Exception as exc:  # noqa: BLE001 — executor 本身不抛,此处双保险
            res = {"ok": False, "outcome": "failed", "reason": str(exc)[:200]}
        exec_outcome = str(res.get("outcome") or "failed")
        ledger_id = res.get("ledger_id") if isinstance(res.get("ledger_id"), int) else None
        exec_ok = bool(res.get("ok")) and ledger_id is not None
        exec_detail = {"executor_outcome": exec_outcome, "executor_reason": str(res.get("reason") or "")}
        exec_summary = (
            f"真执行(白名单受理型):outcome={exec_outcome},ledger 行 #{ledger_id}"
            if exec_ok
            else f"执行未成功:outcome={exec_outcome} {str(res.get('reason') or '')[:120]}"
        )
    step = _step(
        4,
        "execute_ledger",
        "执行留痕",
        _LEDGER,
        "write",
        row_id=ledger_id,
        ok=exec_ok,
        summary=exec_summary,
        detail={"mode": "dry_run" if dry else "executed", **exec_detail},
    )
    return step, exec_outcome, ledger_id


def _outcome_step(
    action: dict[str, Any],
    action_id: int,
    dry: bool,
    exec_outcome: str,
    ledger_id: int | None,
    category: str,
) -> dict[str, Any]:
    """步⑤ 结果登记 → vkpi_agent_outcome_evaluations(喂学习闭环)。"""
    from app.domains.memory import agent_memory_writer

    outcome_word = "success" if (dry or exec_outcome == "success") else ("fail" if exec_outcome == "failed" else "partial")
    outcome_id = agent_memory_writer.record_outcome(
        entity_type=str(action.get("entity_type") or "") or "action",
        entity_id=str(action.get("entity_id") or "") or str(action_id),
        outcome=outcome_word,
        evidence={
            "demo_loop": True,
            "dry_run": dry,
            "action_id": action_id,
            "ledger_id": ledger_id,
            "category": category,
        },
    )
    return _step(
        5,
        "outcome_register",
        "结果登记",
        _OUTCOMES,
        "write",
        row_id=outcome_id,
        ok=outcome_id is not None,
        summary=(
            f"结果 {outcome_word} 登记入学习闭环(evidence 带 demo_loop 标记)"
            if outcome_id is not None
            else "结果登记失败(表缺或写库异常)"
        ),
        detail={"outcome": outcome_word},
    )


def _memory_step(
    steps: list[dict[str, Any]],
    action: dict[str, Any],
    action_id: int,
    category: str,
    dry: bool,
    staff: dict[str, Any] | None,
) -> tuple[dict[str, Any], int | None]:
    """步⑥ record_signal 入记忆:detail 存整链落点表 → trace 端点由此回读。"""
    from app.domains.memory import agent_memory_writer

    chain_ok_so_far = all(bool(s.get("ok")) for s in steps)
    trace_steps = [dict(s) for s in steps]
    trace_steps.append(
        _step(
            6,
            "memory",
            "入记忆",
            _AGENT_ACTIONS,
            "write",
            row_id=None,  # 本行即步⑥落点;行 id 即 trace_id,回读时由 recent_traces 注入
            ok=True,
            summary="整链复盘信号入记忆(本 trace 行自身即步6落点,行 id=trace_id)",
            detail={"self_row": True},
        )
    )
    trace_id = agent_memory_writer.record_signal(
        action_kind="retrospective",
        entity_type=TRACE_ENTITY_TYPE,
        entity_id=action_id,
        staff=staff,
        reason=f"demo_loop 整链串跑({'dry_run 模拟' if dry else '白名单真执行'}):{category} #{action_id}",
        detail={
            "demo_loop": True,
            "dry_run": dry,
            "mode": "simulated" if dry else "real_whitelisted",
            "chain_ok": chain_ok_so_far,
            "steps": trace_steps,
            "action": {
                "id": action_id,
                "category": category,
                "title": str(action.get("title") or "")[:120],
                "priority": action.get("priority"),
            },
        },
    )
    memory_ok = trace_id is not None
    step = _step(
        6,
        "memory",
        "入记忆",
        _AGENT_ACTIONS,
        "write",
        row_id=trace_id,
        ok=memory_ok,
        summary=(
            f"整链复盘信号入记忆(trace_id={trace_id},detail 存六步落点表)"
            if memory_ok
            else "记忆信号写入失败(表缺或写库异常)"
        ),
        detail={"trace_entity_type": TRACE_ENTITY_TYPE},
    )
    return step, trace_id


# ── 整链串跑 ─────────────────────────────────────────────────────────


def run_demo_loop(dry_run: bool = True, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """整链六步串跑一遍并留痕。返回每步真实落点表与行 id(证明链路通)。

    dry_run=True(默认):模拟批准 + demo ledger 行,不真执行、不改 inbox 状态。
    dry_run=False:仅放行零外部副作用白名单类(受理留痕型 executor),真批准 + 真执行。
    任何单步失败不抛:该步 ok=False 带原因,chain_ok 如实为 False。
    """
    from app.db.connection import table_exists

    dry = bool(dry_run)
    if not table_exists(_INBOX):
        return {
            "status": "empty",
            "dry_run": dry,
            "reason": "vkpi_action_inbox 未建(迁移141未 apply),无建议可串",
            "steps": [],
            "chain_ok": False,
            "generated_at": _now_iso(),
        }

    # ① 取一条真实 suggested 建议。
    action = _pick_suggested_action(whitelist_only=not dry)
    if action is None:
        return _empty_inbox_result(dry)

    action_id = int(action.get("id") or 0)
    category = str(action.get("category") or "")
    steps: list[dict[str, Any]] = [_pick_step(action, action_id, category)]

    # ② 驾照 current_level 判权限(L0/L1 → 需人审)。
    license_step, level, needs_review = _license_step(category)
    steps.append(license_step)

    # ③ 批准:dry_run=模拟批准留 approve 信号行;dry_run=False 走真 approve_action。
    real_approved = False
    approve_note = ""
    if not dry:
        real_approved, approve_note = _real_approve(action_id, staff)
    steps.append(
        _approval_step(action_id, category, dry, staff, level, needs_review, real_approved, approve_note)
    )

    # ④ 执行留痕 → ⑤ 结果登记 → ⑥ 入记忆(循环推进/退出条件与原实现逐字一致)。
    execution_step, exec_outcome, ledger_id = _execution_step(action, action_id, dry, staff)
    steps.append(execution_step)
    steps.append(_outcome_step(action, action_id, dry, exec_outcome, ledger_id, category))
    memory_step, trace_id = _memory_step(steps, action, action_id, category, dry, staff)
    steps.append(memory_step)

    chain_ok = all(bool(s.get("ok")) for s in steps)
    return {
        "status": "ready",
        "dry_run": dry,
        "mode": "simulated" if dry else "real_whitelisted",
        "whitelist": list(_ZERO_SIDE_EFFECT_CATEGORIES),
        "chain_ok": chain_ok,
        "trace_id": trace_id,
        "action": {
            "id": action_id,
            "category": category,
            "title": str(action.get("title") or "")[:120],
            "priority": action.get("priority"),
            "entity_type": action.get("entity_type"),
            "entity_id": action.get("entity_id"),
        },
        "steps": steps,
        "generated_at": _now_iso(),
        "note": (
            "六步链路:inbox 建议 → 驾照判权 → 批准 → 执行 ledger → 结果登记 → 记忆;"
            "dry_run 零执行零业务写;dry_run=False 仅白名单受理型动作;零 LLM;不触 viltrox_fit_score / rule_v0。"
        ),
    }


# ── trace 回读(最近串跑记录,只读)──────────────────────────────────


def recent_traces(limit: int = 10) -> dict[str, Any]:
    """最近 demo loop 串跑记录:读步⑥落的 retrospective 信号行(detail 内存整链落点表)。

    精确等值过滤 action_kind + entity_type(零 LIKE 零 percent);缺表诚实空。
    """
    from app.db.connection import get_conn, table_exists

    if not table_exists(_AGENT_ACTIONS):
        return {"status": "empty", "reason": "vkpi_agent_actions 未建(迁移182未 apply)", "items": []}
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 10), 50))
    try:
        rows = conn.execute(
            f"""
            SELECT id, entity_id, actor_staff_id, reason, detail_json, created_at
            FROM {_AGENT_ACTIONS}
            WHERE action_kind = ? AND entity_type = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            ("retrospective", TRACE_ENTITY_TYPE, safe_limit),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 读失败诚实降级
        _safe_rollback(conn)
        logger.warning("loop_runner.recent_traces_failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:200], "items": []}

    items: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        detail = _loads(row.get("detail_json"))
        detail = detail if isinstance(detail, dict) else {}
        steps = detail.get("steps") if isinstance(detail.get("steps"), list) else []
        # 步⑥自引用:trace 行自身即步6落点,回读时把行 id 注入 row_id。
        for s in steps:
            if isinstance(s, dict) and s.get("key") == "memory" and s.get("row_id") is None:
                s["row_id"] = int(row["id"])
        items.append(
            {
                "trace_id": int(row["id"]),
                "action_id": str(row.get("entity_id") or ""),
                "actor_staff_id": row.get("actor_staff_id"),
                "reason": str(row.get("reason") or ""),
                "dry_run": bool(detail.get("dry_run", True)),
                "mode": str(detail.get("mode") or ""),
                "chain_ok": bool(detail.get("chain_ok")),
                "action": detail.get("action") or {},
                "steps": steps,
                "created_at": _iso(row.get("created_at")),
            }
        )
    if not items:
        return {
            "status": "empty",
            "reason": "尚无串跑记录:POST /agents/loop/run-demo 跑一遍即有",
            "items": [],
        }
    return {"status": "ready", "items": items, "count": len(items), "generated_at": _now_iso()}
