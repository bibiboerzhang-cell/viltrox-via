"""检测体系③ · Action 验收守卫:codify「execute 成功且实体齐 → 必写一行 outcome_evaluations」。

学习闭环的真路径(executors.execute_action 第 508-513 行):
    outcome == "success" → inbox.set_status(executed)
                         → _record_execution_feedback(...)   (memory_feedback 影子)
                         → _record_outcome_eval(action, outcome="success")
                              → agent_memory_writer.record_outcome(...)
                                   → INSERT vkpi_agent_outcome_evaluations

这道守卫锁三件事:
  1) 跑「双闸 approved + validator ok → handler success」的真链路,断言 record_outcome 被调一次,
     entity_type/entity_id 透传正确、agent_action_id 恒 None(不触 mig182 FK)。
  2) 早退闸:not entity_type or not entity_id → 绝不写 outcome 行(record_outcome 不被调)。
  3) 集成:真打 DB,_record_outcome_eval + record_outcome 端到端落一行可读回(无 DB/表则 skip,不绿造假)。

hermetic 优先:1)2) 全 monkeypatch DB 边界,不依赖真库;3) 需要真库,缺则 skip。
红线:全程零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _silence_event_bus(monkeypatch) -> None:
    """事件总线 best-effort,屏蔽真 DB 写(_record_outcome_eval 内部会 emit)。"""
    from app.domains.platform import event_ledger

    monkeypatch.setattr(event_ledger, "emit", lambda *a, **k: None)
    monkeypatch.setattr(event_ledger, "new_trace_id", lambda *a, **k: "trace-test")


# ── 1) 真链路:execute 成功且实体齐 → 必写一行 outcome_evaluations ──────────────
def test_execute_action_success_with_entity_writes_outcome(monkeypatch):
    """端到端 execute_action(双闸 + handler success)→ record_outcome 恰被调一次,实体透传。

    用 event_followup 类目:其 handler 仅凭 entity_id 即返回 success(不烧 LLM / 不写业务表),
    是「success 且实体齐」最干净的真路径载体。只 monkeypatch DB 边界,业务判定走真代码。
    """
    from app.domains.actions import executors
    from app.domains.memory import agent_memory_writer

    _silence_event_bus(monkeypatch)

    inbox_action_id = 990011  # vkpi_action_inbox 行号,绝不能当 agent_action_id
    action = {
        "id": inbox_action_id,
        "status": "approved",          # 闸1:已审批
        "category": "event_followup",  # handler 仅凭 entity_id 即 success
        "entity_type": "event",
        "entity_id": "evt-777",
        "touches_v6_fit": False,       # 闸2:红线不触 v6 fit
        "estimated_cost_cents": 0,
        "uses_llm": False,
        "suggested_endpoint": "PATCH /events/evt-777",
        "payload_json": {"missing": ["roi"]},
        "affected_tables_json": [],
    }

    # scope 取 action → 直接给已审批的真 action(绕真库取数)。
    monkeypatch.setattr(executors.inbox, "get_action", lambda aid, staff=None: dict(action))
    # validator 第 4 步会反查 vkpi_events 实体是否存在 → 让它命中(实体齐)。
    monkeypatch.setattr(
        executors.validators, "validate_action",
        lambda a: {"ok": True, "reason": "", "checks": {"entity_exists": True}},
    )
    # ledger / 终态写回 / before-after 快照都是 DB 边界 → 屏蔽,不依赖真库。
    monkeypatch.setattr(executors, "_write_ledger", lambda **k: 12345)
    monkeypatch.setattr(
        executors.inbox,
        "claim_action_execution",
        lambda aid, staff=None: {"ok": True, "status": "executing", "action_id": aid},
    )
    monkeypatch.setattr(
        executors,
        "_finalize_claimed_execution",
        lambda **k: {"ok": True, "status": "executed", "ledger_id": 12345},
    )
    monkeypatch.setattr(executors.inbox, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(executors.inbox, "set_result_checklist", lambda *a, **k: None)
    monkeypatch.setattr(executors, "_snapshot_table_counts", lambda tables: {})
    # memory_feedback 影子写(R8)best-effort,屏蔽以聚焦 outcome 写。
    monkeypatch.setattr(executors, "_record_execution_feedback", lambda *a, **k: None)

    # 关键:捕获真 record_outcome 入参(这是学习闭环的落点)。
    calls: list[dict] = []

    def _capture_record_outcome(**kwargs):
        calls.append(kwargs)
        return 555  # 模拟落行成功返回 id

    monkeypatch.setattr(agent_memory_writer, "record_outcome", _capture_record_outcome)

    res = executors.execute_action(inbox_action_id, staff={"id": 1})

    # 主路径必须成功。
    assert res["ok"] is True
    assert res["outcome"] == "success"
    assert res["category"] == "event_followup"

    # 守卫核心:success + 实体齐 → record_outcome 恰被调一次。
    assert len(calls) == 1, "execute 成功且实体齐时必写一行 outcome_evaluations"
    kw = calls[0]
    assert kw["outcome"] == "success"
    assert kw["entity_type"] == "event"
    assert kw["entity_id"] == "evt-777"
    # 红线:inbox 行号只进 evidence,agent_action_id 恒 None(不触 mig182 FK)。
    assert kw["agent_action_id"] is None
    assert kw["evidence"]["action_id"] == inbox_action_id


# ── 2) 早退闸:无 entity_type/entity_id → 绝不写 outcome 行 ──────────────────────
def test_record_outcome_eval_early_returns_without_entity(monkeypatch):
    """早退闸 `not entity_type or not entity_id`:缺实体 → record_outcome 不被调(诚实不臆造)。"""
    from app.domains.actions import executors
    from app.domains.memory import agent_memory_writer

    _silence_event_bus(monkeypatch)

    calls: list[dict] = []
    monkeypatch.setattr(
        agent_memory_writer, "record_outcome",
        lambda **k: calls.append(k),
    )

    # 缺 entity_id(空串)→ 早退,事件仍 emit 但不落 outcome 行。
    executors._record_outcome_eval(
        {"id": 1, "category": "retrospective", "entity_type": "project", "entity_id": ""},
        outcome="success",
    )
    # 缺 entity_type 同理。
    executors._record_outcome_eval(
        {"id": 2, "category": "retrospective", "entity_type": "", "entity_id": "42"},
        outcome="success",
    )

    assert calls == [], "无实体的动作绝不写 outcome_evaluations(早退闸)"


# ── 3) 集成:真打 DB,_record_outcome_eval → record_outcome 端到端落一行可读回 ──────
@pytest.mark.pg
def test_record_outcome_eval_persists_real_row(monkeypatch):
    """真库集成:_record_outcome_eval(success, 实体齐)→ vkpi_agent_outcome_evaluations 多一行。

    无 DB / 无表 → skip(诚实不绿造假)。读回校验 entity/outcome/success/recommend_again。
    """
    from app.db.connection import get_conn, table_exists
    from app.domains.actions import executors

    _silence_event_bus(monkeypatch)

    try:
        if not table_exists("vkpi_agent_outcome_evaluations"):
            pytest.skip("vkpi_agent_outcome_evaluations 表不存在(mig182 未跑)")
        conn = get_conn()
    except Exception as exc:  # 无库连接 → 诚实 skip。
        pytest.skip(f"DB 不可达,跳过真库集成断言: {exc}")

    entity_id = "guard-test-evt-90021"  # 唯一探针 id,避免与既有数据混。
    before = int(dict(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_agent_outcome_evaluations WHERE entity_type = ? AND entity_id = ?",
        ("event", entity_id),
    ).fetchone())["n"])

    action = {
        "id": 880022,
        "category": "event_followup",
        "entity_type": "event",
        "entity_id": entity_id,
    }
    # 走真写路径(内部真调 agent_memory_writer.record_outcome → 真 INSERT)。
    executors._record_outcome_eval(action, outcome="success")

    row = dict(conn.execute(
        """
        SELECT entity_type, entity_id, outcome, success, recommend_again, agent_action_id, evidence_json
        FROM vkpi_agent_outcome_evaluations
        WHERE entity_type = ? AND entity_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        ("event", entity_id),
    ).fetchone())
    after = int(dict(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_agent_outcome_evaluations WHERE entity_type = ? AND entity_id = ?",
        ("event", entity_id),
    ).fetchone())["n"])

    assert after == before + 1, "success + 实体齐 → 必新增恰好一行 outcome_evaluations"
    assert row["entity_type"] == "event"
    assert row["entity_id"] == entity_id
    assert str(row["outcome"]).lower() == "success"
    # BOOLEAN 读回跨适配器可能是 int 1/0 → 用 _truthy 容错。
    from app.domains.memory.agent_memory_writer import _truthy

    assert _truthy(row["success"]) is True
    assert _truthy(row["recommend_again"]) is True  # 非 fail → recommend_again True
    # 红线:agent_action_id 必须留空(inbox 号不入 FK)。
    assert row["agent_action_id"] is None
