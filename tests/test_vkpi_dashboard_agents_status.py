import json
from pathlib import Path

from app.api.routers import vkpi_dashboard_staff


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_dashboard_agents_status_reads_latest_runtime_artifacts(tmp_path: Path) -> None:
    _write(
        tmp_path / "local-p7-82-recommendation-agent-v0.json",
        {
            "mode": "p7_82_recommendation_agent_v0",
            "passed": True,
            "summary": {"candidate_count": 3, "agent_status": "ready"},
        },
    )
    _write(
        tmp_path / "local-p5-69-market-intelligence-v0.json",
        {
            "mode": "p5_69_market_intelligence_v0",
            "passed": True,
            "summary": {"signals_loaded": 25, "high_priority": 7},
        },
    )

    payload = vkpi_dashboard_staff._build_dashboard_agents_status(str(tmp_path), kol_pool_total=1023)

    agents = {agent["id"]: agent for agent in payload["agents"]}
    assert payload["total"] == 7
    assert payload["active_count"] == 3
    assert agents["recommendation"]["status"] == "active"
    assert agents["recommendation"]["summary"] == "3 个候选 · ready"
    assert agents["market_intel"]["summary"] == "25 条市场信号 · 7 条高优先级"
    assert agents["kol_intel"]["status"] == "active"
    assert agents["kol_intel"]["summary"] == "1023 个 KOL 已入池"


def test_dashboard_agents_status_marks_missing_artifacts_idle(tmp_path: Path) -> None:
    payload = vkpi_dashboard_staff._build_dashboard_agents_status(str(tmp_path), kol_pool_total=0)

    assert payload["active_count"] == 0
    assert all(agent["status"] == "idle" for agent in payload["agents"])
    assert all(agent["summary"] for agent in payload["agents"])


def test_dashboard_copilot_brief_reads_latest_brief_artifact(tmp_path: Path) -> None:
    _write(
        tmp_path / "local-p7-83-brief-agent-v0.json",
        {
            "mode": "p7_83_brief_agent_v0",
            "summary": {"headline": "今天优先处理 3 个 KOL 机会"},
            "brief_items": [{"title": "机会 A"}, {"title": "机会 B"}],
            "next_actions": [{"title": "联系 KOL"}],
        },
    )

    payload = vkpi_dashboard_staff._build_dashboard_copilot_brief(str(tmp_path))

    assert payload["is_real"] is True
    assert payload["headline"] == "今天优先处理 3 个 KOL 机会"
    assert payload["mode"] == "p7_83_brief_agent_v0"
    assert len(payload["items"]) == 2
    assert len(payload["actions"]) == 1
