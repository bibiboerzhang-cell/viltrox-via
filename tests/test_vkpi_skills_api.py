"""Skills HTTP 端点单测(run / list / runs)。

策略:
  - 用 importlib 直接加载 backend/app/api/routers/vkpi_skills.py,直接调端点函数(绕过 FastAPI
    Depends —— 显式传 _staff=None),hermetic 测分发/校验/投影,不起 ASGI、不要真 auth。
  - skills 的 run 默认 model_fn=None → 不真烧 LLM;但底层 content_score/creator_match 等会读 DB,
    缺数据时返回 unavailable/error,这都是合法 status,不让测试依赖真数据。
  - list / runs 端口在缺表(vkpi_skill_runs 未 apply)时应退化为安全空/零,不抛。

红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

vkpi_skills = importlib.import_module("app.api.routers.vkpi_skills")


def test_router_prefix_and_dispatch_table():
    """router 前缀对齐契约;分发表覆盖 5 个已知 skill(以 SKILL_NAME 为键)。"""
    assert vkpi_skills.router.prefix == "/api/admin/vkpi"
    keys = set(vkpi_skills._DISPATCH.keys())
    # SKILL_NAME 口径:brief_generate(非 brief_generate_v1)。
    assert {"creator_match", "brief_generate", "content_score", "roi_review", "campaign_plan"} <= keys
    for fn in vkpi_skills._DISPATCH.values():
        assert callable(fn)


def test_run_unknown_skill_404():
    """未知 skill_name → 404,且不触任何 run。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        vkpi_skills.run_skill("no_such_skill_xyz", {"input": {}}, _staff=None)
    assert exc.value.status_code == 404


def test_run_dispatches_and_shapes_response(monkeypatch):
    """run 端点把 input 透传给对应 skill 的 run(record=True),并形状化成 {status, output}。"""
    captured = {}

    def fake_run(skill_input, *, record=True, model_fn=None):
        captured["input"] = skill_input
        captured["record"] = record
        captured["model_fn"] = model_fn
        return {"status": "ok", "scores": {"quality": 80}}

    monkeypatch.setitem(vkpi_skills._DISPATCH, "creator_match", fake_run)

    resp = vkpi_skills.run_skill("creator_match", {"input": {"product": "X"}}, _staff=None)
    assert resp["status"] == "ok"
    assert resp["output"]["scores"]["quality"] == 80
    assert captured["input"] == {"product": "X"}
    assert captured["record"] is True
    # 默认不真烧 LLM:不显式注入 model_fn(走 skill 默认 None)。
    assert captured["model_fn"] is None


def test_run_tolerates_flat_body(monkeypatch):
    """body 无 input 包裹时,平铺字段也能透传(input 兜底)。"""
    captured = {}

    def fake_run(skill_input, *, record=True, model_fn=None):
        captured["input"] = skill_input
        return {"ok": True}

    monkeypatch.setitem(vkpi_skills._DISPATCH, "roi_review", fake_run)
    resp = vkpi_skills.run_skill("roi_review", {"campaign_id": 7}, _staff=None)
    # ok:True 无显式 status → 归一为 ok。
    assert resp["status"] == "ok"
    assert captured["input"] == {"campaign_id": 7}


def test_run_skill_failure_500(monkeypatch):
    """skill 内部抛异常 → 包成 500,不裸抛。"""
    from fastapi import HTTPException

    def boom(skill_input, *, record=True, model_fn=None):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(vkpi_skills._DISPATCH, "content_score", boom)
    with pytest.raises(HTTPException) as exc:
        vkpi_skills.run_skill("content_score", {"input": {}}, _staff=None)
    assert exc.value.status_code == 500


def test_status_of_helper():
    """_status_of 归一:显式 status 优先,ok 布尔次之,无字段且有内容默认 ok。"""
    assert vkpi_skills._status_of({"status": "unavailable"}) == "unavailable"
    assert vkpi_skills._status_of({"ok": True}) == "ok"
    assert vkpi_skills._status_of({"ok": False}) == "error"
    assert vkpi_skills._status_of({"recommendations": []}) == "ok"
    assert vkpi_skills._status_of("weird") == "ok"


def test_list_skills_shape():
    """list 端口:每行带契约字段;缺表时 runs 退化为 0,acceptance_rate 可为 None。"""
    rows = vkpi_skills.list_skills(_staff=None)
    assert isinstance(rows, list)
    names = {r["skill_name"] for r in rows}
    assert {"creator_match", "brief_generate", "content_score", "roi_review", "campaign_plan"} <= names
    for r in rows:
        assert set(r.keys()) == {
            "skill_name",
            "version",
            "runs",
            "acceptance_rate",
            "avg_cost_cents",
            "avg_latency_ms",
        }
        assert isinstance(r["runs"], int)
        assert isinstance(r["version"], str)


def test_list_skills_handles_stats_error(monkeypatch):
    """skill_acceptance_stats 抛错时,该行仍产出且 runs=0(best-effort 不染红)。"""
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(vkpi_skills.skill_registry, "skill_acceptance_stats", boom)
    rows = vkpi_skills.list_skills(_staff=None)
    assert all(r["runs"] == 0 for r in rows)


def test_runs_endpoint_projection(monkeypatch):
    """runs 端口:把 list_skill_runs 的明细投影成契约字段子集。"""
    def fake_list(skill_name="", limit=50):
        return {
            "status": "ok",
            "runs": [
                {
                    "id": 42,
                    "skill_name": "creator_match",
                    "model_used": None,
                    "cost_cents": 0,
                    "latency_ms": 12,
                    "accepted": True,
                    "created_at": "2026-06-28T00:00:00Z",
                    "output": {"extra": "should_be_dropped"},
                }
            ],
        }

    monkeypatch.setattr(vkpi_skills.skill_registry, "list_skill_runs", fake_list)
    rows = vkpi_skills.list_skill_runs(skill_name="creator_match", limit=10, _staff=None)
    assert len(rows) == 1
    assert set(rows[0].keys()) == {
        "id",
        "skill_name",
        "model_used",
        "cost_cents",
        "latency_ms",
        "accepted",
        "created_at",
    }
    assert rows[0]["id"] == 42
    assert rows[0]["accepted"] is True


def test_runs_endpoint_unavailable(monkeypatch):
    """list_skill_runs 返回 unavailable(缺表)→ runs 端口退化为空列表。"""
    monkeypatch.setattr(
        vkpi_skills.skill_registry,
        "list_skill_runs",
        lambda skill_name="", limit=50: {"status": "unavailable", "runs": []},
    )
    assert vkpi_skills.list_skill_runs(_staff=None) == []
