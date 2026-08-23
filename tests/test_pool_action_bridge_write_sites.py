"""波 C·C4:人工写口 → 训练信号 插桩(recommendations.pool_action_bridge)。

五个真实人工写口(项目自动收藏 / 加入项目触达 / 分组共享成员 / 派单 stage=contacted /
外联消息即时桥)各验两件事:
  1. 主写提交后恰好调用一次 actions.record_pool_action_feedback(动作 + payload 口径正确);
  2. 桥抛异常时主写结果不变、只记 warning(不静默、不阻断)。
全部用查询路由型假连接,零真库、零 LLM。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ── 假连接:按 SQL 关键字路由返回行;记录 commit/rollback 次数 ──────────────
class _Result:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _RoutedConn:
    def __init__(self, routes: list[tuple[str, Any]]):
        self.routes = routes
        self.sql: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        compact = " ".join(str(sql).split())
        self.sql.append(compact)
        for needle, rows in self.routes:
            if needle in compact:
                return _Result(rows(params) if callable(rows) else rows)
        return _Result([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Recorder:
    """替身 record_pool_action_feedback:记录每次调用及调用时的 commit 计数;可按需抛错。"""

    def __init__(self, conn: _RoutedConn, *, fail: bool = False):
        self.conn = conn
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def __call__(self, kol_pool_id, action, *, staff=None, note="", payload=None):
        self.calls.append({
            "kol_pool_id": kol_pool_id,
            "action": action,
            "staff": staff,
            "payload": dict(payload or {}),
            "commits_at_call": self.conn.commits,
        })
        if self.fail:
            raise RuntimeError("bridge boom")
        return {"linked": True, "feedback_inserted": True}


@pytest.fixture()
def bridge_env(monkeypatch):
    """把 actions.record_pool_action_feedback 换成记录器;返回 (make_recorder)。"""
    from app.domains.recommendations import actions as rec_actions
    from app.domains.recommendations import pool_action_bridge

    def install(conn: _RoutedConn, *, fail: bool = False) -> _Recorder:
        rec = _Recorder(conn, fail=fail)
        monkeypatch.setattr(rec_actions, "record_pool_action_feedback", rec)
        monkeypatch.setattr(pool_action_bridge, "get_conn", lambda: conn)
        return rec

    return install


def _warnings(caplog, needle: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING and needle in r.getMessage()]


# ── 0) 桥本身:失败不抛 + warning + rollback;幂等交给 actions ──────────────
def test_bridge_pool_action_never_raises_and_recovers_connection(bridge_env, caplog):
    from app.domains.recommendations import pool_action_bridge

    conn = _RoutedConn([])
    rec = bridge_env(conn, fail=True)
    with caplog.at_level(logging.WARNING):
        out = pool_action_bridge.bridge_pool_action(7, "favorite", staff={"id": 3}, payload={"x": 1}, source="unit")
    assert out["linked"] is False and out["reason"] == "bridge_failed"
    assert rec.calls[0]["payload"] == {"x": 1, "source": "unit"}
    assert conn.rollbacks == 1  # PG aborted 态要拉回来
    assert _warnings(caplog, "pool_action_bridge.failed")

    # 系统任务无 staff → payload 标 actor=system;pool_id 无效不打扰 actions
    rec2 = bridge_env(conn)
    assert pool_action_bridge.bridge_pool_action(0, "touch")["reason"] == "no_pool_id"
    pool_action_bridge.bridge_pool_action(9, "touch", staff=None, source="cron")
    assert rec2.calls[-1]["payload"] == {"source": "cron", "actor": "system"}


def test_bridge_message_outreach_direction_and_pool_resolution(bridge_env):
    from app.domains.recommendations import pool_action_bridge

    conn = _RoutedConn([
        ("FROM vkpi_kol_pool WHERE linked_main_kol_id=?", [{"id": 41}, {"id": 42}]),
    ])
    rec = bridge_env(conn)
    # inbound:闭集无对应动作 → 诚实跳过(留给每日 sync_message_outcomes)
    assert pool_action_bridge.bridge_message_outreach(message_id=1, project_id=5, kol_id=77, direction="inbound") == []
    assert rec.calls == []
    # outbound:kol_id → linked_main_kol_id 桥到池,每个池项一次 "outreach"
    out = pool_action_bridge.bridge_message_outreach(message_id=1, project_id=5, kol_id=77, direction="outbound", staff={"id": 2}, source="t")
    assert len(out) == 2 and [c["kol_pool_id"] for c in rec.calls] == [41, 42]
    assert all(c["action"] == "outreach" for c in rec.calls)
    assert rec.calls[0]["payload"] == {"message_id": 1, "direction": "outbound", "project_id": 5, "kol_id": 77, "source": "t"}


# ── 1) 项目自动收藏 + 触达:workflow_projects_kols.add_project_kols ─────────
def _project_kols_env(monkeypatch, conn: _RoutedConn):
    from app.domains.projects import workflow_projects_kols as wf

    monkeypatch.setattr(wf, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(wf.scope, "assert_project_access", lambda *a, **k: None)
    monkeypatch.setattr(wf.scope, "can_view_all", lambda *_a, **_k: False)
    monkeypatch.setattr(wf, "get_conn", lambda: conn)
    monkeypatch.setattr(wf, "_locked_pool_claim_occupancy", lambda *_a, **_k: {})
    monkeypatch.setattr(wf, "_log_project_audit", lambda **_k: None)
    from app.domains.memory import agent_memory_writer

    monkeypatch.setattr(agent_memory_writer, "record_kol_signal", lambda *_a, **_k: None)
    return wf


def _project_kols_conn() -> _RoutedConn:
    inserted = iter([101, 102])
    return _RoutedConn([
        ("SELECT id, stage_status FROM vkpi_projects", [{"id": 1, "stage_status": "active"}]),
        ("SELECT id FROM vkpi_kol_pool WHERE id IN", [{"id": 11}, {"id": 12}]),
        ("INSERT INTO vkpi_project_kol_assignments", lambda _p: [{"id": next(inserted)}]),
    ])


@pytest.mark.parametrize("fail", [False, True])
def test_add_project_kols_bridges_touch_and_favorite_after_commit(monkeypatch, bridge_env, caplog, fail):
    conn = _project_kols_conn()
    wf = _project_kols_env(monkeypatch, conn)
    rec = bridge_env(conn, fail=fail)
    with caplog.at_level(logging.WARNING):
        out = wf.add_project_kols(1, {"kol_pool_ids": [11, 12]}, staff={"id": 5, "role": "staff"})
    assert out["inserted"] == 2  # 主写结果与桥成败无关
    assert conn.commits == 1
    assert all(c["commits_at_call"] == 1 for c in rec.calls)  # 全部在业务事务提交之后
    touches = [(c["kol_pool_id"], c["payload"]) for c in rec.calls if c["action"] == "touch"]
    favorites = [(c["kol_pool_id"], c["payload"]) for c in rec.calls if c["action"] == "favorite"]
    assert touches == [(11, {"project_id": 1, "channel": "project_assignment", "source": "project_assignment"}),
                       (12, {"project_id": 1, "channel": "project_assignment", "source": "project_assignment"})]
    assert favorites == [(11, {"project_id": 1, "source": "project_auto_favorite"}),
                         (12, {"project_id": 1, "source": "project_auto_favorite"})]
    assert all(c["staff"] == {"id": 5, "role": "staff"} for c in rec.calls)
    assert bool(_warnings(caplog, "pool_action_bridge.failed")) is fail


# ── 2) 分组共享成员:staff_groups.service ───────────────────────────────────
def _groups_conn() -> _RoutedConn:
    group_row = {
        "id": "grp_1718500000000", "name": "g", "description": "",
        "member_ids": "[5, 6]", "permissions_json": '{"shared_kol_pool_ids": [21, 22]}',
    }
    return _RoutedConn([
        ("SELECT * FROM vkpi_staff_groups WHERE id = ?", [group_row]),
        ("SELECT id FROM vkpi_kol_pool WHERE id IN", [{"id": 21}, {"id": 22}]),
        ("SELECT id FROM staff WHERE id IN", [{"id": 5}, {"id": 6}]),
        ("SELECT id FROM vkpi_projects WHERE id IN", []),
    ])


@pytest.mark.parametrize("fail", [False, True])
def test_staff_group_member_expansion_bridges_favorite_member(monkeypatch, bridge_env, caplog, fail):
    from app.domains.staff_groups import service

    conn = _groups_conn()
    monkeypatch.setattr(service, "get_conn", lambda: conn)
    rec = bridge_env(conn, fail=fail)
    staff = {"id": 9, "staff_id": 9, "role": "admin"}
    with caplog.at_level(logging.WARNING):
        out = service.add_member("grp_1718500000000", 6, staff)
    assert out["item"]["member_ids"] == [5, 6]
    assert conn.commits == 1
    member_inserts = [s for s in conn.sql if "INSERT INTO vkpi_kol_pool_members" in s]
    assert len(member_inserts) == 4  # 2 KOL × 2 成员,主写不受桥影响
    assert [(c["kol_pool_id"], c["action"]) for c in rec.calls] == [(21, "favorite"), (22, "favorite")]
    assert all(c["payload"] == {"pool_action": "member", "group_id": "grp_1718500000000", "source": "staff_group_shared_kol"} for c in rec.calls)
    assert all(c["commits_at_call"] == 1 and c["staff"] is staff for c in rec.calls)
    assert bool(_warnings(caplog, "pool_action_bridge.failed")) is fail

    # 删组 / 无共享 KOL → 不桥
    rec2 = bridge_env(conn)
    service.delete_group("grp_1718500000000", staff)
    assert rec2.calls == []


# ── 3) 派单 stage=contacted:workflow_evidence.advance_project_kol_assignment ──
def _assignment_conn() -> _RoutedConn:
    base = {"id": 55, "project_id": 3, "kol_pool_id": 31, "stage": "discovered", "stage_status": "active", "metadata_json": "{}"}
    return _RoutedConn([
        ("SELECT * FROM vkpi_project_kol_assignments WHERE id=?", [{**base, "stage": "contacted"}]),
        ("SELECT * FROM vkpi_project_kol_assignments WHERE project_id=?", [base]),
    ])


def _evidence_env(monkeypatch, conn: _RoutedConn):
    from app.domains.projects import workflow_evidence as wf

    monkeypatch.setattr(wf, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(wf.scope, "assert_project_access", lambda *a, **k: None)
    monkeypatch.setattr(wf, "get_conn", lambda: conn)
    monkeypatch.setattr(wf.audit, "log_business_event", lambda **_k: None)
    return wf


@pytest.mark.parametrize("fail", [False, True])
def test_advance_assignment_to_contacted_bridges_contact(monkeypatch, bridge_env, caplog, fail):
    conn = _assignment_conn()
    wf = _evidence_env(monkeypatch, conn)
    rec = bridge_env(conn, fail=fail)
    with caplog.at_level(logging.WARNING):
        out = wf.advance_project_kol_assignment(3, 55, {"to_stage": "contacted"}, staff={"id": 4})
    assert out["assignment"]["stage"] == "contacted"
    assert conn.commits == 1
    assert [(c["kol_pool_id"], c["action"], c["commits_at_call"]) for c in rec.calls] == [(31, "contact", 1)]
    assert rec.calls[0]["payload"] == {"stage": "contacted", "project_id": 3, "assignment_id": 55, "source": "assignment_stage"}
    assert bool(_warnings(caplog, "pool_action_bridge.failed")) is fail


def test_advance_assignment_other_stage_does_not_bridge(monkeypatch, bridge_env):
    conn = _assignment_conn()
    wf = _evidence_env(monkeypatch, conn)
    rec = bridge_env(conn)
    wf.advance_project_kol_assignment(3, 55, {"to_stage": "replied"}, staff={"id": 4})
    assert rec.calls == []  # 只插 contacted;其余阶段留给每日 sync_assignment_outcomes


# ── 4) 外联消息即时桥:evidence/messages.create_message ────────────────────
def _message_conn(kol_id: int = 77) -> _RoutedConn:
    return _RoutedConn([
        ("INSERT INTO vkpi_messages", lambda p: [{"id": 900, "project_id": p[0], "kol_id": p[1], "direction": p[4]}]),
        ("FROM vkpi_kol_pool WHERE linked_main_kol_id=?", lambda p: [{"id": 61}] if int(p[0]) == kol_id else []),
    ])


@pytest.mark.parametrize("fail", [False, True])
def test_create_message_bridges_outbound_outreach(monkeypatch, bridge_env, caplog, fail):
    from app.domains.evidence import messages

    conn = _message_conn()
    monkeypatch.setattr(messages, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(messages, "get_conn", lambda: conn)
    monkeypatch.setattr(messages, "_project_context", lambda *_a, **_k: {"kol_id": 77})
    monkeypatch.setattr(messages.audit, "log_business_event", lambda **_k: None)
    rec = bridge_env(conn, fail=fail)
    with caplog.at_level(logging.WARNING):
        item = messages.create_message({"project_id": 5, "body": "hi"}, staff={"id": 2})
    assert item["id"] == 900 and conn.commits == 1
    assert [(c["kol_pool_id"], c["action"], c["commits_at_call"]) for c in rec.calls] == [(61, "outreach", 1)]
    assert rec.calls[0]["payload"] == {"message_id": 900, "direction": "outbound", "project_id": 5, "kol_id": 77, "source": "evidence_message"}
    assert bool(_warnings(caplog, "pool_action_bridge.failed")) is fail

    rec2 = bridge_env(conn)
    messages.create_message({"project_id": 5, "body": "re", "direction": "inbound"}, staff={"id": 2})
    assert rec2.calls == []


# ── 5) 项目级外联消息:workflow_evidence_project_writes.add_project_message ─
@pytest.mark.parametrize("fail", [False, True])
def test_add_project_message_bridges_outbound_outreach(monkeypatch, bridge_env, caplog, fail):
    from app.domains.projects import workflow_evidence_project_writes as pw

    conn = _message_conn()
    conn.routes.insert(0, ("SELECT kol_id, assigned_staff_id FROM vkpi_projects", [{"kol_id": 77, "assigned_staff_id": 8}]))
    monkeypatch.setattr(pw, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(pw.scope, "assert_project_access", lambda *a, **k: None)
    monkeypatch.setattr(pw, "get_conn", lambda: conn)
    monkeypatch.setattr(pw.audit, "log_business_event", lambda **_k: None)
    rec = bridge_env(conn, fail=fail)
    with caplog.at_level(logging.WARNING):
        item = pw.add_project_message(5, {"body": "hello"}, staff={"id": 2})
    assert item["id"] == 900 and conn.commits == 1
    assert [(c["kol_pool_id"], c["action"], c["commits_at_call"]) for c in rec.calls] == [(61, "outreach", 1)]
    assert rec.calls[0]["payload"]["source"] == "project_message"
    assert bool(_warnings(caplog, "pool_action_bridge.failed")) is fail
