"""Marketing Brain① Skill Registry 单测:record 落库 + list/stats 读回。

策略:
  - register_skill 是纯只读合并视图,hermetic 直测(不碰 DB)。
  - record_skill_run / list_skill_runs / skill_acceptance_stats 走活 dev DB(vkpi_skill_runs)。
    若该表不存在(迁移未 apply)则 skip,不让缺表把套件染红。
  - 用唯一 skill_name(uuid)隔离本次 run,断言写进去的字段原样读回:
    JSONB(input_schema/retrieved_context/output)结构保真、accepted 经 _truthy 归一为 Python bool。
红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.connection import table_exists  # noqa: E402
from app.domains.marketing_brain import skill_registry  # noqa: E402


def test_register_skill_merges_tool_registry_metadata():
    """已知工具(search_kol)合并出 tool_registry 元数据 + version;未知工具退化为最小声明。"""
    res = skill_registry.register_skill("search_kol", skill_version="v2")
    assert res["status"] == "ok"
    skill = res["skill"]
    assert skill["skill_name"] == "search_kol"
    assert skill["skill_version"] == "v2"
    # tool_registry 里 search_kol 烧 LLM、低成本档 —— 合并视图应带出这些声明。
    assert skill.get("uses_llm") is True

    unknown = skill_registry.register_skill("totally_made_up_skill_xyz")
    assert unknown["status"] == "ok"
    assert unknown["skill"]["skill_name"] == "totally_made_up_skill_xyz"


def test_register_skill_rejects_blank():
    assert skill_registry.register_skill("")["status"] == "error"


def test_truthy_compat_boolean_readback():
    """compat BOOLEAN 读回 int 1/0 —— _truthy 把 1/0 归一为 Python True/False,None 透传。"""
    assert skill_registry._truthy(1) is True
    assert skill_registry._truthy(0) is False
    assert skill_registry._truthy(None) is None
    assert skill_registry._truthy(True) is True


@pytest.mark.skipif(
    not table_exists("vkpi_skill_runs"),
    reason="vkpi_skill_runs missing (migration 199 not applied)",
)
def test_record_then_readback_roundtrip():
    skill_name = "test_skill_" + uuid.uuid4().hex[:12]
    payload = {
        "skill_name": skill_name,
        "skill_version": "v1",
        "input_schema": {"query": "string", "limit": 10},
        "model_used": "claude-test",
        "prompt_version": "p7",
        "retrieved_context": {"docs": ["a", "b"], "n": 2},
        "output": {"result": "ok", "items": [1, 2, 3]},
        "cost_cents": 42,
        "latency_ms": 1234,
        "human_score": 0.85,
        "business_result": "lead_created",
        "accepted": True,
    }
    rec = skill_registry.record_skill_run(**payload)
    assert rec["status"] == "ok"
    assert isinstance(rec["run_id"], int)

    listed = skill_registry.list_skill_runs(skill_name=skill_name)
    assert listed["status"] == "ok"
    assert listed["count"] == 1
    run = listed["runs"][0]

    assert run["id"] == rec["run_id"]
    assert run["skill_name"] == skill_name
    assert run["skill_version"] == "v1"
    assert run["model_used"] == "claude-test"
    assert run["prompt_version"] == "p7"
    assert run["cost_cents"] == 42
    assert run["latency_ms"] == 1234
    # JSONB 结构保真。
    assert run["input_schema"] == {"query": "string", "limit": 10}
    assert run["retrieved_context"] == {"docs": ["a", "b"], "n": 2}
    assert run["output"] == {"result": "ok", "items": [1, 2, 3]}
    # BOOLEAN 读回经 _truthy 归一为 Python bool(非 int 1)。
    assert run["accepted"] is True
    # human_score 浮点保真。
    assert abs(float(run["human_score"]) - 0.85) < 1e-6


@pytest.mark.skipif(
    not table_exists("vkpi_skill_runs"),
    reason="vkpi_skill_runs missing (migration 199 not applied)",
)
def test_acceptance_stats_three_state():
    skill_name = "test_skill_" + uuid.uuid4().hex[:12]
    base = dict(skill_name=skill_name, model_used="m", cost_cents=10, latency_ms=100)
    # 2 accepted, 1 rejected, 1 unjudged(accepted=None)。
    skill_registry.record_skill_run(accepted=True, human_score=1.0, **base)
    skill_registry.record_skill_run(accepted=True, human_score=0.5, **base)
    skill_registry.record_skill_run(accepted=False, human_score=0.0, **base)
    skill_registry.record_skill_run(accepted=None, **base)

    stats = skill_registry.skill_acceptance_stats(skill_name=skill_name)
    assert stats["status"] == "ok"
    assert stats["total"] == 4
    assert stats["judged"] == 3        # accepted=None 不计入分母
    assert stats["accepted"] == 2
    assert stats["acceptance_rate"] == round(2 / 3, 3)
    assert stats["avg_cost_cents"] == 10.0
    assert stats["avg_latency_ms"] == 100.0
