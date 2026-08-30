"""Characterization tests for agents/loop_runner.run_demo_loop.

锁行为用:六步链路的每步落点表、summary 文案、返回结构逐字锁死,
降复杂度刀(CC 54 → ≤10)改完必须原样绿。全部外部依赖 monkeypatch,零真库零 LLM。
"""
from __future__ import annotations

import pytest

from app.db import connection as db_connection
from app.domains.actions import executors as actions_executors
from app.domains.actions import inbox as actions_inbox
from app.domains.agents import autonomy_license, loop_runner
from app.domains.memory import agent_memory_writer

FIXED_NOW = "2026-08-30T00:00:00+00:00"
STAFF = {"id": 17, "user_id": 9}


def _action(**overrides) -> dict:
    base = {
        "id": 42,
        "dedupe_key": "dk-42",
        "category": "kol_profile",
        "title": "补全KOL档案",
        "priority": "high",
        "entity_type": "kol",
        "entity_id": "abc",
        "suggested_endpoint": "",
        "requires_approval": 1,
        "owner_staff_id": 7,
        "status": "suggested",
        "touches_v6_fit": 0,
        "estimated_cost_cents": 0,
        "uses_llm": 0,
    }
    base.update(overrides)
    return base


class _SignalRecorder:
    """Capture record_signal calls and return preset row ids in order."""

    def __init__(self, ids):
        self.ids = list(ids)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.ids.pop(0) if self.ids else None


@pytest.fixture()
def wired(monkeypatch):
    """Wire every collaborator run_demo_loop touches; return the mutable knobs."""
    knobs = {
        "table_exists": True,
        "action": _action(),
        "pick_calls": [],
        "license_row_id": 7,
        "license": {"status": "ready", "level": 2, "level_label": "半自动"},
        "license_exc": None,
        "signals": _SignalRecorder([501, 502]),
        "outcome_id": 601,
        "outcome_calls": [],
        "ledger_id": 88,
        "ledger_calls": [],
        "approve_result": {"ok": True},
        "approve_exc": None,
        "approve_calls": [],
        "execute_result": {"ok": True, "outcome": "success", "ledger_id": 321},
        "execute_exc": None,
        "execute_calls": [],
    }

    monkeypatch.setattr(db_connection, "table_exists", lambda name: knobs["table_exists"])
    monkeypatch.setattr(loop_runner, "_now_iso", lambda: FIXED_NOW)

    def fake_pick(*, whitelist_only):
        knobs["pick_calls"].append(whitelist_only)
        return knobs["action"]

    monkeypatch.setattr(loop_runner, "_pick_suggested_action", fake_pick)
    monkeypatch.setattr(loop_runner, "_license_row_id", lambda action_type: knobs["license_row_id"])

    def fake_current_level(action_type):
        if knobs["license_exc"] is not None:
            raise knobs["license_exc"]
        return knobs["license"]

    monkeypatch.setattr(autonomy_license, "current_level", fake_current_level)
    monkeypatch.setattr(agent_memory_writer, "record_signal", knobs["signals"])

    def fake_record_outcome(**kwargs):
        knobs["outcome_calls"].append(kwargs)
        return knobs["outcome_id"]

    monkeypatch.setattr(agent_memory_writer, "record_outcome", fake_record_outcome)

    def fake_write_demo_ledger(action, staff, *, dry_run):
        knobs["ledger_calls"].append((action, staff, dry_run))
        return knobs["ledger_id"]

    monkeypatch.setattr(loop_runner, "_write_demo_ledger", fake_write_demo_ledger)

    def fake_approve(action_id, staff, reason=""):
        knobs["approve_calls"].append({"action_id": action_id, "staff": staff, "reason": reason})
        if knobs["approve_exc"] is not None:
            raise knobs["approve_exc"]
        return knobs["approve_result"]

    monkeypatch.setattr(actions_inbox, "approve_action", fake_approve)

    def fake_execute(action_id, staff):
        knobs["execute_calls"].append({"action_id": action_id, "staff": staff})
        if knobs["execute_exc"] is not None:
            raise knobs["execute_exc"]
        return knobs["execute_result"]

    monkeypatch.setattr(actions_executors, "execute_action", fake_execute)
    return knobs


def test_missing_inbox_table_returns_honest_empty(wired):
    wired["table_exists"] = False

    assert loop_runner.run_demo_loop(dry_run=True, staff=STAFF) == {
        "status": "empty",
        "dry_run": True,
        "reason": "vkpi_action_inbox 未建(迁移141未 apply),无建议可串",
        "steps": [],
        "chain_ok": False,
        "generated_at": FIXED_NOW,
    }


def test_no_suggested_action_dry_run_empty(wired):
    wired["action"] = None

    assert loop_runner.run_demo_loop(dry_run=True, staff=STAFF) == {
        "status": "empty",
        "dry_run": True,
        "reason": "inbox 当前无 suggested 建议(先跑每日生成或等 producer 产出)",
        "whitelist": ["event_followup", "inventory_low", "project_shared_to_you"],
        "steps": [],
        "chain_ok": False,
        "generated_at": FIXED_NOW,
    }
    assert wired["pick_calls"] == [False]


def test_no_suggested_action_real_run_reason(wired):
    wired["action"] = None

    result = loop_runner.run_demo_loop(dry_run=False, staff=STAFF)

    assert result["reason"] == (
        "白名单类(零外部副作用)当前无 suggested 建议,或候选均未过执行前置校验"
        "(如实体已不存在的 stale 建议),dry_run=False 诚实拒跑"
    )
    assert result["dry_run"] is False
    assert wired["pick_calls"] == [True]


def test_dry_run_full_chain_six_steps_exact(wired):
    result = loop_runner.run_demo_loop(dry_run=True, staff=STAFF)

    expected_steps = [
        {
            "step": 1,
            "key": "inbox_pick",
            "title": "取建议",
            "table": "vkpi_action_inbox",
            "op": "read",
            "row_id": 42,
            "ok": True,
            "summary": "取到真实 suggested 建议 #42(kol_profile):补全KOL档案",
            "detail": {"priority": "high", "dedupe_key": "dk-42"},
        },
        {
            "step": 2,
            "key": "license_check",
            "title": "验驾照",
            "table": "vkpi_autonomy_licenses",
            "op": "read",
            "row_id": 7,
            "ok": True,
            "summary": "驾照 pool_enrich = L2 半自动(级别足以内部执行)",
            "detail": {
                "license_action_type": "pool_enrich",
                "mapped_from_category": "kol_profile",
                "level": 2,
                "needs_human_review": False,
                "license_status": "ready",
            },
        },
        {
            "step": 3,
            "key": "approval",
            "title": "批准",
            "table": "vkpi_agent_actions",
            "op": "write",
            "row_id": 501,
            "ok": True,
            "summary": "模拟批准留痕(dry_run:inbox 状态原样不动)",
            "detail": {"real_approved": False, "note": ""},
        },
        {
            "step": 4,
            "key": "execute_ledger",
            "title": "执行留痕",
            "table": "vkpi_action_execution_ledger",
            "op": "write",
            "row_id": 88,
            "ok": True,
            "summary": "demo 标记行落 ledger(mode=dry_run, endpoint=internal:demo_loop),未执行任何外部动作",
            "detail": {"mode": "dry_run"},
        },
        {
            "step": 5,
            "key": "outcome_register",
            "title": "结果登记",
            "table": "vkpi_agent_outcome_evaluations",
            "op": "write",
            "row_id": 601,
            "ok": True,
            "summary": "结果 success 登记入学习闭环(evidence 带 demo_loop 标记)",
            "detail": {"outcome": "success"},
        },
        {
            "step": 6,
            "key": "memory",
            "title": "入记忆",
            "table": "vkpi_agent_actions",
            "op": "write",
            "row_id": 502,
            "ok": True,
            "summary": "整链复盘信号入记忆(trace_id=502,detail 存六步落点表)",
            "detail": {"trace_entity_type": "demo_loop"},
        },
    ]
    assert result == {
        "status": "ready",
        "dry_run": True,
        "mode": "simulated",
        "whitelist": ["event_followup", "inventory_low", "project_shared_to_you"],
        "chain_ok": True,
        "trace_id": 502,
        "action": {
            "id": 42,
            "category": "kol_profile",
            "title": "补全KOL档案",
            "priority": "high",
            "entity_type": "kol",
            "entity_id": "abc",
        },
        "steps": expected_steps,
        "generated_at": FIXED_NOW,
        "note": (
            "六步链路:inbox 建议 → 驾照判权 → 批准 → 执行 ledger → 结果登记 → 记忆;"
            "dry_run 零执行零业务写;dry_run=False 仅白名单受理型动作;零 LLM;不触 viltrox_fit_score / rule_v0。"
        ),
    }
    # dry_run 红线:不触 approve/execute;ledger 只写 demo 行。
    assert wired["approve_calls"] == []
    assert wired["execute_calls"] == []
    assert wired["ledger_calls"] == [(wired["action"], STAFF, True)]

    approve_signal, trace_signal = wired["signals"].calls
    assert approve_signal["action_kind"] == "approve"
    assert approve_signal["entity_type"] == "action"
    assert approve_signal["entity_id"] == 42
    assert approve_signal["reason"] == "demo_loop 模拟批准(dry_run,不改 inbox 状态);驾照 L2 "
    assert approve_signal["detail"] == {
        "demo_loop": True,
        "dry_run": True,
        "real_approved": False,
        "needs_human_review": False,
        "category": "kol_profile",
    }
    assert trace_signal["action_kind"] == "retrospective"
    assert trace_signal["entity_type"] == "demo_loop"
    assert trace_signal["reason"] == "demo_loop 整链串跑(dry_run 模拟):kol_profile #42"
    trace_detail = trace_signal["detail"]
    assert trace_detail["mode"] == "simulated"
    assert trace_detail["chain_ok"] is True
    assert trace_detail["action"] == {
        "id": 42,
        "category": "kol_profile",
        "title": "补全KOL档案",
        "priority": "high",
    }
    # trace 里的步⑥占位行:row_id=None,自引用标记。
    assert len(trace_detail["steps"]) == 6
    placeholder = trace_detail["steps"][5]
    assert placeholder["row_id"] is None
    assert placeholder["summary"] == "整链复盘信号入记忆(本 trace 行自身即步6落点,行 id=trace_id)"
    assert placeholder["detail"] == {"self_row": True}

    assert wired["outcome_calls"] == [
        {
            "entity_type": "kol",
            "entity_id": "abc",
            "outcome": "success",
            "evidence": {
                "demo_loop": True,
                "dry_run": True,
                "action_id": 42,
                "ledger_id": 88,
                "category": "kol_profile",
            },
        }
    ]


def test_dry_run_ledger_write_failure_marks_chain_not_ok(wired):
    wired["ledger_id"] = None

    result = loop_runner.run_demo_loop(dry_run=True, staff=STAFF)

    step4 = result["steps"][3]
    assert step4["ok"] is False
    assert step4["row_id"] is None
    assert step4["summary"] == "demo ledger 行写入失败(表缺或写库异常)"
    assert result["chain_ok"] is False
    # 结果登记仍按 dry_run 口径记 success,ledger_id 如实为 None。
    assert wired["outcome_calls"][0]["outcome"] == "success"
    assert wired["outcome_calls"][0]["evidence"]["ledger_id"] is None


def test_license_error_degrades_to_l0_needs_review(wired):
    wired["action"] = _action(category="event_followup")
    wired["license_exc"] = ValueError("boom")
    wired["license_row_id"] = None

    result = loop_runner.run_demo_loop(dry_run=True, staff=STAFF)

    step2 = result["steps"][1]
    assert step2["ok"] is True
    assert step2["row_id"] is None
    assert step2["summary"] == (
        "驾照 event_followup = L0 观察(需人审:该级只许建议,执行须人批);驾照读数 error:boom"
    )
    assert step2["detail"] == {
        "license_action_type": "event_followup",
        "mapped_from_category": "event_followup",
        "level": 0,
        "needs_human_review": True,
        "license_status": "error",
    }
    # 需人审如实写进批准信号 reason。
    assert wired["signals"].calls[0]["reason"] == "demo_loop 模拟批准(dry_run,不改 inbox 状态);驾照 L0 需人审"


def test_real_run_approves_and_executes_whitelisted_action(wired):
    wired["action"] = _action(category="event_followup")

    result = loop_runner.run_demo_loop(dry_run=False, staff=STAFF)

    assert wired["pick_calls"] == [True]
    assert wired["approve_calls"] == [
        {
            "action_id": 42,
            "staff": STAFF,
            "reason": "demo_loop:白名单零外部副作用动作,整链验证真批准",
        }
    ]
    assert wired["execute_calls"] == [{"action_id": 42, "staff": STAFF}]
    assert wired["ledger_calls"] == []

    step3 = result["steps"][2]
    assert step3["ok"] is True
    assert step3["summary"] == "真批准成功(inbox 行状态 → approved)+ approve 信号留痕"
    assert step3["detail"] == {"real_approved": True, "note": ""}
    assert wired["signals"].calls[0]["reason"] == "demo_loop 真批准(白名单零副作用);驾照 L2 "

    step4 = result["steps"][3]
    assert step4["ok"] is True
    assert step4["row_id"] == 321
    assert step4["summary"] == "真执行(白名单受理型):outcome=success,ledger 行 #321"
    assert step4["detail"] == {"mode": "executed", "executor_outcome": "success", "executor_reason": ""}

    assert result["mode"] == "real_whitelisted"
    assert result["chain_ok"] is True
    assert wired["outcome_calls"][0]["outcome"] == "success"
    assert wired["signals"].calls[1]["reason"] == "demo_loop 整链串跑(白名单真执行):event_followup #42"


def test_real_run_approve_failure_recorded_but_chain_continues(wired):
    wired["action"] = _action(category="inventory_low")
    wired["approve_result"] = {"ok": False, "reason": "denied"}

    result = loop_runner.run_demo_loop(dry_run=False, staff=STAFF)

    step3 = result["steps"][2]
    assert step3["ok"] is False
    assert step3["summary"] == "approve_action 未成功:denied"
    assert step3["detail"] == {"real_approved": False, "note": "approve_action 未成功:denied"}
    # 批准失败不短路:执行步仍走(原行为如此,逐字锁定)。
    assert wired["execute_calls"] == [{"action_id": 42, "staff": STAFF}]
    assert result["chain_ok"] is False


def test_real_run_approve_exception_is_captured(wired):
    wired["action"] = _action(category="inventory_low")
    wired["approve_exc"] = RuntimeError("闸拒绝")

    result = loop_runner.run_demo_loop(dry_run=False, staff=STAFF)

    step3 = result["steps"][2]
    assert step3["ok"] is False
    assert step3["summary"] == "approve_action 异常:闸拒绝"
    assert step3["detail"] == {"real_approved": False, "note": "approve_action 异常:闸拒绝"}


def test_real_run_executor_exception_degrades(wired):
    wired["action"] = _action(category="event_followup")
    wired["execute_exc"] = RuntimeError("提审失败")

    result = loop_runner.run_demo_loop(dry_run=False, staff=STAFF)

    step4 = result["steps"][3]
    assert step4["ok"] is False
    assert step4["row_id"] is None
    assert step4["summary"] == "执行未成功:outcome=failed 提审失败"
    assert step4["detail"] == {"mode": "executed", "executor_outcome": "failed", "executor_reason": "提审失败"}
    assert wired["outcome_calls"][0]["outcome"] == "fail"
    assert result["chain_ok"] is False


def test_real_run_partial_outcome_word(wired):
    wired["action"] = _action(category="event_followup")
    wired["execute_result"] = {"ok": True, "outcome": "noop", "ledger_id": 5}

    result = loop_runner.run_demo_loop(dry_run=False, staff=STAFF)

    assert wired["outcome_calls"][0]["outcome"] == "partial"
    assert result["steps"][4]["summary"] == "结果 partial 登记入学习闭环(evidence 带 demo_loop 标记)"


def test_signal_write_failures_marked_honest(wired):
    wired["signals"].ids = []  # record_signal 全部回 None(表缺或写库异常)
    wired["outcome_id"] = None

    result = loop_runner.run_demo_loop(dry_run=True, staff=STAFF)

    assert result["steps"][2]["ok"] is False
    assert result["steps"][2]["summary"] == "approve 信号行写入失败(表缺或写库异常)"
    assert result["steps"][4]["ok"] is False
    assert result["steps"][4]["summary"] == "结果登记失败(表缺或写库异常)"
    assert result["steps"][5]["ok"] is False
    assert result["steps"][5]["summary"] == "记忆信号写入失败(表缺或写库异常)"
    assert result["trace_id"] is None
    assert result["chain_ok"] is False
