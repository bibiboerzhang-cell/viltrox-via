"""驾照系统点火三件测试(2026-08-30):

① prediction_ledger 家族聚合回读:_LICENSE_FAMILY 成员 category(kol_profile 等)的
   executed/success 样本必须计入家族驾照(pool_enrich)命中率;映射缺席按字面回退。
② 每日晋升评估 job:恒 dry_run,promote/demote 建议写 action_inbox(category=license_promotion,
   人审后走既有 override 端点);env 闸默认 OFF(不注册),置 1 才注册。
③ license_status_snapshot 纯读快照:level/sample_count/hit_rate/离晋升门槛差距,永不 raise。

红线断言:任何路径不落库改级、不自动晋升;零真库(全部 monkeypatch)。
"""
from __future__ import annotations

import sys
import types

import pytest

from app.db import connection as db_connection
from app.domains.agents import autonomy_license, prediction_ledger
from app.domains.agents.loop_runner import _LICENSE_FAMILY


# ── 假连接:按 IN(...) 参数过滤执行台账行 ─────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FamilyLedgerConn:
    """只认 vkpi_action_execution_ledger 查询;按参数里的 category 集合回行。"""

    def __init__(self, rows_by_category):
        self.rows_by_category = dict(rows_by_category)
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        if "vkpi_action_execution_ledger" in sql and "GROUP BY" not in sql:
            cats = list(params[:-1])  # 末位是 LIMIT
            rows = []
            for cat in cats:
                rows.extend(self.rows_by_category.get(cat, []))
            return _FakeCursor(rows)
        return _FakeCursor([])


def _exec_row(outcome="success", mode="executed", at="2026-08-29T00:00:00+00:00"):
    return {"mode": mode, "outcome": outcome, "created_at": at}


# ── ① 家族聚合回读 ───────────────────────────────────────────────────


def test_family_members_expand_from_loop_runner_single_source():
    members = prediction_ledger._license_family_members("pool_enrich")
    assert members[0] == "pool_enrich"
    for member, family in _LICENSE_FAMILY.items():
        if family == "pool_enrich":
            assert member in members  # 单一真源:loop_runner 的映射逐条进列表
    assert prediction_ledger._license_family_members("event_followup") == ["event_followup"]


def test_family_category_samples_count_into_family_license(monkeypatch):
    """核心 bug 修复:kol_profile 的 11 条 executed/success 计入 pool_enrich 驾照命中率。"""
    conn = _FamilyLedgerConn({"kol_profile": [_exec_row() for _ in range(11)]})
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    out = prediction_ledger.hit_rate_for("pool_enrich")
    assert out["status"] == "ok"
    assert out["action_type"] == "pool_enrich"
    assert out["sample_count"] == 11
    assert out["hit_rate"] == 1.0
    fam = out["basis"]["family_members"]
    assert "kol_profile" in fam and "discovery_enroll" in fam and "deep_missing" in fam


def test_family_aggregation_mixes_members_and_judges_misses(monkeypatch):
    conn = _FamilyLedgerConn(
        {
            "kol_profile": [_exec_row(), _exec_row(outcome="failed")],
            "discovery_enroll": [_exec_row()],
            "deep_missing": [_exec_row(mode="dry_run")],  # 演练剔除
        }
    )
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    out = prediction_ledger.hit_rate_for("pool_enrich")
    assert out["sample_count"] == 3
    assert out["hit_rate"] == round(2 / 3, 4)


def test_non_family_category_stays_literal(monkeypatch):
    """非家族名逐字匹配,行为不变(ledger_summary 动态组口径)。"""
    conn = _FamilyLedgerConn({"event_followup": [_exec_row()]})
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    out = prediction_ledger.hit_rate_for("event_followup")
    assert out["sample_count"] == 1
    ledger_sql, ledger_params = next(
        (s, p) for s, p in conn.queries if "vkpi_action_execution_ledger" in s
    )
    assert ledger_params[:-1] == ("event_followup",)  # 只查了自己


def test_family_mapping_missing_falls_back_literal(monkeypatch):
    """loop_runner 映射不可得 → 按字面回退,绝不炸台账。"""
    monkeypatch.setitem(
        sys.modules, "app.domains.agents.loop_runner", types.SimpleNamespace()
    )
    assert prediction_ledger._license_family_members("pool_enrich") == ["pool_enrich"]


# ── ② 每日晋升评估 job(恒 dry_run + inbox 建议 + env 闸)────────────


def _promotion_report():
    return {
        "status": "ready",
        "dry_run": True,
        "evaluated_at": "2026-08-30T00:00:00+00:00",
        "items": [
            {
                "action_type": "pool_enrich",
                "decision": "promote",
                "current_level": 1,
                "proposed_level": 2,
                "hit_rate": 0.95,
                "sample_count": 22,
                "miss_streak": 0,
                "reason": "近 20 次命中率 0.95 >= 0.85 且样本 22 >= 20,升 1 级",
            },
            {"action_type": "event_followup", "decision": "hold", "current_level": 1, "proposed_level": 1},
            {
                "action_type": "inventory_low",
                "decision": "demote",
                "current_level": 2,
                "proposed_level": 1,
                "hit_rate": 0.0,
                "sample_count": 6,
                "miss_streak": 6,
                "reason": "连续 6 次未命中,降 1 级",
            },
        ],
    }


def test_promotion_scan_writes_inbox_suggestions_dry_run_only(monkeypatch):
    eval_calls: list[bool] = []

    def fake_evaluate(dry_run=True):
        eval_calls.append(dry_run)
        return _promotion_report()

    monkeypatch.setattr(autonomy_license, "evaluate_promotions", fake_evaluate)
    from app.domains.actions import inbox as actions_inbox

    persisted_batches: list[list[dict]] = []
    monkeypatch.setattr(
        actions_inbox, "persist_suggestions", lambda s: persisted_batches.append(s) or len(s)
    )

    out = autonomy_license.run_license_promotion_scan()

    assert eval_calls == [True]  # 红线:恒 dry_run,评估绝不落库改级
    assert out["dry_run"] is True
    assert out["evaluated"] == 3 and out["proposals"] == 2 and out["persisted"] == 2
    assert out["inbox_category"] == "license_promotion"

    (batch,) = persisted_batches
    by_key = {s["dedupe_key"]: s for s in batch}
    promo = by_key["license_promotion:pool_enrich:L1toL2"]
    assert promo["category"] == "license_promotion"
    assert promo["requires_approval"] is True  # 红线:晋升永远走人审
    assert promo["suggested_endpoint"] == "/api/admin/vkpi/autonomy/licenses/pool_enrich/override"
    assert promo["payload"]["decision"] == "promote" and promo["payload"]["proposed_level"] == 2
    demo = by_key["license_promotion:inventory_low:L2toL1"]
    assert demo["payload"]["decision"] == "demote"
    # hold 不产建议
    assert not any("event_followup" in k for k in by_key)


def test_promotion_scan_honest_when_persist_fails(monkeypatch):
    monkeypatch.setattr(autonomy_license, "evaluate_promotions", lambda dry_run=True: _promotion_report())
    from app.domains.actions import inbox as actions_inbox

    def boom(_s):
        raise RuntimeError("inbox down")

    monkeypatch.setattr(actions_inbox, "persist_suggestions", boom)
    out = autonomy_license.run_license_promotion_scan()
    assert out["persisted"] == 0 and "inbox down" in out["persist_error"]
    assert out["proposals"] == 2  # 评估结论仍诚实回报


def test_promotion_scan_no_proposals_no_inbox_write(monkeypatch):
    report = {"status": "ready", "items": [{"action_type": "a", "decision": "hold"}]}
    monkeypatch.setattr(autonomy_license, "evaluate_promotions", lambda dry_run=True: report)
    from app.domains.actions import inbox as actions_inbox

    def must_not_call(_s):  # pragma: no cover - 断言用
        raise AssertionError("hold-only 不该写 inbox")

    monkeypatch.setattr(actions_inbox, "persist_suggestions", must_not_call)
    out = autonomy_license.run_license_promotion_scan()
    assert out["proposals"] == 0 and out["persisted"] == 0


class _FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, dict] = {}

    def add_job(self, func, trigger=None, *args, **kwargs):
        self.jobs[kwargs["id"]] = {"func": func, "trigger": trigger, **kwargs}
        return func


def test_registry_env_gate_default_off(monkeypatch):
    from app.services.scheduler import jobs_registry

    monkeypatch.delenv("VKPI_LICENSE_PROMOTION_SCAN_ENABLED", raising=False)
    sched = _FakeScheduler()
    jobs_registry._register_fulfillment_autoops_jobs(sched)
    assert "vkpi_license_promotion_scan" not in sched.jobs  # 默认关闸:连注册都不发生
    assert "daily_action_inbox_generate" in sched.jobs and "ops_threshold_alerts" in sched.jobs


def test_registry_env_gate_on_registers_daily_job(monkeypatch):
    from app.services.scheduler import jobs_registry

    monkeypatch.setenv("VKPI_LICENSE_PROMOTION_SCAN_ENABLED", "1")
    sched = _FakeScheduler()
    jobs_registry._register_fulfillment_autoops_jobs(sched)
    job = sched.jobs["vkpi_license_promotion_scan"]
    assert job["max_instances"] == 1 and job["coalesce"] is True
    trigger = str(job["trigger"])
    assert "7" in trigger and "40" in trigger  # 每日 07:40 中国


# ── ③ 纯读快照 ───────────────────────────────────────────────────────


def _lic(action, level, ledger):
    return {"action_type": action, "level": level, "ledger": ledger}


def test_license_status_snapshot_gaps_and_readiness(monkeypatch):
    listing = {
        "status": "ready",
        "items": [
            _lic("ready_lane", 1, {"status": "ok", "hit_rate": 0.9, "sample_count": 25}),
            _lic("low_hit", 1, {"status": "ok", "hit_rate": 0.5, "sample_count": 25}),
            _lic("thin_sample", 1, {"status": "ok", "hit_rate": 0.9, "sample_count": 3}),
            _lic("no_ledger", 0, {"status": "unknown_action_type"}),
            _lic("capped", 3, {"status": "ok", "hit_rate": 0.99, "sample_count": 60}),
        ],
    }
    monkeypatch.setattr(autonomy_license, "list_licenses", lambda: listing)
    snap = autonomy_license.license_status_snapshot()
    assert snap["status"] == "ready" and len(snap["items"]) == 5
    by = {i["action_type"]: i for i in snap["items"]}

    assert by["ready_lane"]["promotion_ready"] is True
    assert by["ready_lane"]["promotion_blocked_reason"] == ""
    assert by["low_hit"]["promotion_ready"] is False
    assert by["low_hit"]["promotion_gap"]["hit_rate_gap"] == round(0.85 - 0.5, 4)
    assert by["thin_sample"]["promotion_gap"]["samples_needed"] == 17
    assert "样本还差 17" in by["thin_sample"]["promotion_blocked_reason"]
    assert by["no_ledger"]["hit_rate"] is None
    assert "台账无可用命中率读数" in by["no_ledger"]["promotion_blocked_reason"]
    assert "封顶 L3" in by["capped"]["promotion_blocked_reason"]  # L4 仅人工
    # 快照契约字段齐全
    gap = by["low_hit"]["promotion_gap"]["threshold"]
    assert gap == {"hit_rate": 0.85, "min_sample": 20}


def test_license_status_snapshot_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("db exploded")

    monkeypatch.setattr(autonomy_license, "list_licenses", boom)
    snap = autonomy_license.license_status_snapshot()
    assert snap["status"] == "error" and "db exploded" in snap["reason"]
    assert snap["items"] == []


def test_snapshot_and_scan_touch_no_forbidden_dimension():
    """红线兜底:任何级别推出来的 dimensions,affect_scoring 恒 False。"""
    for level in range(0, 5):
        dims = autonomy_license._dimensions_for_level(level)
        assert dims[autonomy_license.FORBIDDEN_DIMENSION] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
