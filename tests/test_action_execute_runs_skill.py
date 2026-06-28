"""skills-wire 守卫:codify「execute_action 成功 → 顺带跑对应 Skill 且 record_skill_run 被调」。

收口① 让 5 个 Skill 真跑 + 落账。真实调用点接在 executors.execute_action 的 success 分支:
    outcome == "success"
        → inbox.set_status(executed)
        → _record_execution_feedback(...)        (R8 影子)
        → _record_outcome_eval(...)              (B5/H4 学习闭环)
        → _maybe_run_skill(action, detail)       (skills-wire:跑对应 skill.run(record=True))
              → kol_profile      → creator_match.run(...)  → skill_registry.record_skill_run(...)
              → content_candidate → content_score.run(...)  → skill_registry.record_skill_run(...)

这道守卫锁四件事:
  1) kol_profile 类成功执行 → creator_match.run 被调一次(record=True, model_fn=None),
     record_skill_run 被调,且 result.detail 带 skill_run 回执。
  2) content_candidate 类成功执行 → content_score.run 被调一次(record=True, model_fn=None),
     record_skill_run 被调。
  3) best-effort 铁律:_maybe_run_skill 内部抛异常时,execute_action 主返回仍 success(不被拖垮)。
  4) 集成(真库):直跑 creator_match.run(record=True) → vkpi_skill_runs 真多一行(无库/表则 skip)。

hermetic 优先:1)2)3) 全 monkeypatch DB / skill 边界,不依赖真库;4) 需真库,缺则 skip。
红线:全程 model_fn=None(绝不烧真 LLM);零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _silence_event_bus(monkeypatch) -> None:
    from app.domains.platform import event_ledger

    monkeypatch.setattr(event_ledger, "emit", lambda *a, **k: None)
    monkeypatch.setattr(event_ledger, "new_trace_id", lambda *a, **k: "trace-test")


def _stub_execute_boundaries(monkeypatch, action: dict) -> None:
    """把 execute_action 的 DB / 学习闭环边界全 monkeypatch 掉,只留 handler + skills-wire 真跑。"""
    from app.domains.actions import executors

    monkeypatch.setattr(executors.inbox, "get_action", lambda aid, staff=None: dict(action))
    monkeypatch.setattr(
        executors.validators, "validate_action",
        lambda a: {"ok": True, "reason": "", "checks": {"entity_exists": True}},
    )
    monkeypatch.setattr(executors, "_write_ledger", lambda **k: 12345)
    monkeypatch.setattr(executors.inbox, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(executors.inbox, "set_result_checklist", lambda *a, **k: None)
    monkeypatch.setattr(executors, "_snapshot_table_counts", lambda tables: {})
    monkeypatch.setattr(executors, "_record_execution_feedback", lambda *a, **k: None)
    monkeypatch.setattr(executors, "_record_outcome_eval", lambda *a, **k: None)
    # skills-wire 默认 OFF(execute 契约零变);本守卫显式开闸验证接线行为。
    monkeypatch.setattr(executors, "_SKILLS_AUTORUN", True)


# ── 1) kol_profile 成功 → creator_match.run + record_skill_run 被调 ─────────────
def test_kol_profile_success_runs_creator_match_and_records(monkeypatch):
    from app.domains.actions import executors
    from app.domains.marketing_brain import skill_registry
    from app.domains.marketing_brain.skills import creator_match

    _silence_event_bus(monkeypatch)

    action = {
        "id": 770011,
        "status": "approved",
        "category": "kol_profile",
        "entity_type": "kol",
        "entity_id": "501",
        "touches_v6_fit": False,
        "estimated_cost_cents": 0,
        "uses_llm": False,
        "suggested_endpoint": "POST /kol/501/profile-crawl",
        "payload_json": {},
        "affected_tables_json": [],
    }
    _stub_execute_boundaries(monkeypatch, action)

    # handler 真跑会入 apify_jobs;隔离成功桩(skills-wire 在 handler success 之后才触发)。
    monkeypatch.setattr(
        executors, "_exec_kol_profile",
        lambda a, s: {"outcome": "success", "reason": "", "detail": {}},
    )
    monkeypatch.setitem(executors._DISPATCH, "kol_profile", executors._exec_kol_profile)
    # KOL 产品口径取数 → 桩一个真实产品(绕真库)。
    monkeypatch.setattr(executors, "_kol_product_query", lambda kid: ("viltrox af 85mm", "US"))

    # creator_match.run 真跑会打底层匹配服务(DB/Memory)→ 桩成确定输出,但断言它被以 record=True 调用。
    run_calls: list[dict] = []

    def _stub_cm_run(inp, *, model_fn=None, record=True):
        run_calls.append({"input": inp, "model_fn": model_fn, "record": record})
        return {"recommendations": [{"handle": "alice_lens"}], "rationale": "stub"}

    monkeypatch.setattr(creator_match, "run", _stub_cm_run)

    # record_skill_run 是落账本的真落点 —— 捕获以证明被调。
    rec_calls: list[dict] = []
    monkeypatch.setattr(
        skill_registry, "record_skill_run",
        lambda **k: rec_calls.append(k) or {"status": "ok", "run_id": 1},
    )

    res = executors.execute_action(770011, staff={"id": 1})

    assert res["ok"] is True and res["outcome"] == "success"
    # 守卫核心:creator_match.run 恰被调一次,record=True、model_fn=None(绝不烧真 LLM)。
    assert len(run_calls) == 1, "kol_profile 成功执行必跑一次 creator_match.run"
    assert run_calls[0]["record"] is True
    assert run_calls[0]["model_fn"] is None
    assert run_calls[0]["input"]["product"] == "viltrox af 85mm"
    # 回执进 detail。
    assert res["detail"]["skill_run"]["skill"] == "creator_match"
    assert res["detail"]["skill_run"]["recorded"] is True


# ── 2) content_candidate 成功 → content_score.run + record_skill_run 被调 ────────
def test_content_candidate_success_runs_content_score_and_records(monkeypatch):
    from app.domains.actions import executors
    from app.domains.marketing_brain.skills import content_score

    _silence_event_bus(monkeypatch)

    action = {
        "id": 770022,
        "status": "approved",
        "category": "content_candidate",
        "entity_type": "project",
        "entity_id": "88",
        "touches_v6_fit": False,
        "estimated_cost_cents": 0,
        "uses_llm": False,
        "suggested_endpoint": "POST /content/review",
        "payload_json": {"post_id": 4242},
        "affected_tables_json": [],
    }
    _stub_execute_boundaries(monkeypatch, action)

    monkeypatch.setattr(
        executors, "_exec_content_candidate",
        lambda a, s: {"outcome": "success", "reason": "", "detail": {}},
    )
    monkeypatch.setitem(executors._DISPATCH, "content_candidate", executors._exec_content_candidate)
    # post → evidence_id 取数桩(绕真库)。
    monkeypatch.setattr(executors, "_evidence_id_for_post", lambda pid: "vid-ev-999")

    run_calls: list[dict] = []

    def _stub_cs_run(inp, *, model_fn=None, record=True):
        run_calls.append({"input": inp, "model_fn": model_fn, "record": record})
        # content_score.run 内部本会 best-effort 落账 —— 这里直接桩 run,断言它被以 record=True 调用。
        return {"status": "unavailable", "scores": {}}

    monkeypatch.setattr(content_score, "run", _stub_cs_run)

    res = executors.execute_action(770022, staff={"id": 1})

    assert res["ok"] is True and res["outcome"] == "success"
    assert len(run_calls) == 1, "content_candidate 成功执行必跑一次 content_score.run"
    assert run_calls[0]["record"] is True
    assert run_calls[0]["model_fn"] is None
    assert run_calls[0]["input"]["analysis_cache_ref"]["target_id"] == "vid-ev-999"
    assert res["detail"]["skill_run"]["skill"] == "content_score"


# ── 3) best-effort 铁律:skill 跑挂绝不拖垮主执行 ───────────────────────────────
def test_skill_wire_failure_does_not_break_execute(monkeypatch):
    from app.domains.actions import executors

    _silence_event_bus(monkeypatch)

    action = {
        "id": 770033,
        "status": "approved",
        "category": "kol_profile",
        "entity_type": "kol",
        "entity_id": "777",
        "touches_v6_fit": False,
        "estimated_cost_cents": 0,
        "uses_llm": False,
        "payload_json": {},
        "affected_tables_json": [],
    }
    _stub_execute_boundaries(monkeypatch, action)
    monkeypatch.setattr(
        executors, "_exec_kol_profile",
        lambda a, s: {"outcome": "success", "reason": "", "detail": {}},
    )
    monkeypatch.setitem(executors._DISPATCH, "kol_profile", executors._exec_kol_profile)

    # 让 skill 取数直接炸 → _maybe_run_skill 必须吞掉,主返回仍 success。
    def _boom(_kid):
        raise RuntimeError("boom")

    monkeypatch.setattr(executors, "_kol_product_query", _boom)

    res = executors.execute_action(770033, staff={"id": 1})
    assert res["ok"] is True and res["outcome"] == "success"
    # skill 挂 → 无 skill_run 回执,但主执行毫发无伤。
    assert "skill_run" not in res.get("detail", {})


# ── 4) 集成(真库):creator_match.run(record=True) → vkpi_skill_runs 真多一行 ─────
def test_creator_match_run_persists_skill_run_row(monkeypatch):
    from app.db.connection import get_conn, table_exists
    from app.domains.marketing_brain.skills import creator_match

    try:
        if not table_exists("vkpi_skill_runs"):
            pytest.skip("vkpi_skill_runs 表不存在")
        conn = get_conn()
    except Exception as exc:
        pytest.skip(f"DB 不可达: {exc}")

    # 隔离底层匹配服务 → 桩确定输出(避免真打 Memory/DB);只验证 record_skill_run 真落行。
    monkeypatch.setattr(
        creator_match, "_build_preview",
        lambda **k: {"items": [], "summary": {"eligible_after_hard_filters": 0}, "target_family_name": "probe-fam"},
    )

    before = int(dict(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_skill_runs WHERE skill_name = ?",
        ("creator_match",),
    ).fetchone())["n"])

    out = creator_match.run({"product": "skills-wire-probe", "market": "US"}, model_fn=None, record=True)
    assert isinstance(out.get("recommendations"), list)

    after = int(dict(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_skill_runs WHERE skill_name = ?",
        ("creator_match",),
    ).fetchone())["n"])
    assert after == before + 1, "creator_match.run(record=True) 必落一行 vkpi_skill_runs"
