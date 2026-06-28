"""回归:Action Inbox 执行结果回写不再用 inbox 行号当 FK,失败也不污染共享连接。

背景(bug):executors._record_outcome_eval 曾把 action["id"](vkpi_action_inbox 行号)
当成 agent_memory_writer.record_outcome 的 agent_action_id 传入。该列 FK 指向
vkpi_agent_actions(mig182),inbox 动作并不在那张表里 → 每次执行都触
ForeignKeyViolation(vkpi_agent_outcome_evaluations_agent_action_id_fkey),主路径成功但
日志被刷屏。

两道断言锁修复:
  1) _record_outcome_eval 永远以 agent_action_id=None 调 record_outcome(inbox 号只进
     evidence.action_id),根因消除。
  2) record_outcome 内 INSERT 失败时必须 rollback —— get_conn() 是请求/线程作用域共享连接,
     失败事务不回滚会让同作用域后续查询全报 "current transaction is aborted"。

hermetic:全程 monkeypatch,不依赖真 DB / FK 是否启用。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def test_record_outcome_eval_never_passes_inbox_id_as_agent_action_id(monkeypatch):
    """根因锁:inbox 行号只落 evidence.action_id,agent_action_id 恒为 None(不碰 FK)。"""
    from app.domains.actions import executors
    from app.domains.memory import agent_memory_writer
    from app.domains.platform import event_ledger

    # 事件总线 best-effort,屏蔽真 DB 写。
    monkeypatch.setattr(event_ledger, "emit", lambda *a, **k: None)
    monkeypatch.setattr(event_ledger, "new_trace_id", lambda *a, **k: "trace-test")

    captured: dict = {}

    def _fake_record_outcome(**kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(agent_memory_writer, "record_outcome", _fake_record_outcome)

    inbox_action_id = 778899  # vkpi_action_inbox 行号,绝不允许当 agent_action_id
    action = {
        "id": inbox_action_id,
        "category": "failed_retry",
        "entity_type": "kol",
        "entity_id": "4242",
    }

    executors._record_outcome_eval(action, outcome="success")

    assert captured, "record_outcome 应被调用"
    # 核心:agent_action_id 必须显式为 None(不再误传 inbox 行号触 FK)。
    assert captured["agent_action_id"] is None
    # inbox 行号仍完整保留在 evidence,学习闭环不丢信息。
    assert captured["evidence"]["action_id"] == inbox_action_id
    assert captured["entity_type"] == "kol"
    assert captured["entity_id"] == "4242"


def test_record_outcome_rolls_back_on_insert_failure(monkeypatch):
    """连接卫生锁:INSERT 失败 → rollback 清 aborted 事务、不抛、返回 None。"""
    from app.domains.memory import agent_memory_writer

    class _FakeConn:
        def __init__(self) -> None:
            self.rolled_back = False
            self.committed = False

        def execute(self, *a, **k):
            raise RuntimeError("simulated ForeignKeyViolation")

        def commit(self) -> None:  # 不应到达
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

    fake = _FakeConn()
    monkeypatch.setattr(agent_memory_writer, "table_exists", lambda *a, **k: True)
    monkeypatch.setattr(agent_memory_writer, "get_conn", lambda: fake)

    # best-effort:即便底层炸了也绝不抛。
    result = agent_memory_writer.record_outcome(
        entity_type="kol", entity_id="4242", outcome="success",
    )

    assert result is None
    assert fake.rolled_back is True, "失败 INSERT 必须 rollback,否则污染共享连接事务"
    assert fake.committed is False
