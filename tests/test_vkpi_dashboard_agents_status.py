import json
import os
from pathlib import Path

from app.domains import dashboard as dashboard_domain
from app.domains.dashboard import agents as dashboard_agents


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class _Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row):
        self.row = row

    def execute(self, query):
        assert "COUNT(*) AS n FROM vkpi_kol_pool" in query
        return _Rows(self.row)


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

    payload = dashboard_domain._build_dashboard_agents_status(str(tmp_path), kol_pool_total=1023)

    agents = {agent["id"]: agent for agent in payload["agents"]}
    assert payload["total"] == 7
    assert payload["active_count"] == 3
    assert agents["recommendation"]["status"] == "active"
    assert agents["recommendation"]["summary"] == "3 个候选 · ready"
    assert agents["market_intel"]["summary"] == "25 条市场信号 · 7 条高优先级"
    assert agents["kol_intel"]["status"] == "active"
    assert agents["kol_intel"]["summary"] == "1023 个 KOL 已入池"


def test_build_dashboard_agents_status_reads_kol_pool_total(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dashboard_agents, "get_conn", lambda: _Conn({"n": 7}))

    payload = dashboard_domain.build_dashboard_agents_status(str(tmp_path))

    agents = {agent["id"]: agent for agent in payload["agents"]}
    assert agents["kol_intel"]["status"] == "active"
    assert agents["kol_intel"]["summary"] == "7 个 KOL 已入池"


def test_dashboard_agents_status_marks_missing_artifacts_idle(tmp_path: Path) -> None:
    payload = dashboard_domain._build_dashboard_agents_status(str(tmp_path), kol_pool_total=0)

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

    payload = dashboard_domain._build_dashboard_copilot_brief(str(tmp_path))

    assert payload["is_real"] is True
    assert payload["headline"] == "今天优先处理 3 个 KOL 机会"
    assert payload["mode"] == "p7_83_brief_agent_v0"
    assert len(payload["items"]) == 2
    assert len(payload["actions"]) == 1


def test_dashboard_tasks_reads_recommendation_candidates(tmp_path: Path) -> None:
    _write(
        tmp_path / "local-p7-82-recommendation-agent-v0.json",
        {
            "mode": "p7_82_recommendation_agent_v0",
            "candidates": [
                {"candidate_id": "c1", "kol_handle": "@one", "confidence": 0.82, "reason": "证据链完整"},
                {"candidate_id": "c2", "title": "观察 @two", "score": 0.3, "summary": "证据不足"},
            ],
            "next_steps": ["人工复核后再联系"],
        },
    )

    payload = dashboard_domain._build_dashboard_tasks(str(tmp_path), limit=6)

    assert payload["is_real"] is True
    assert payload["candidate_count"] == 2
    assert payload["tasks"][0]["priority"] == "high"
    assert payload["tasks"][0]["title"] == "@one"
    assert payload["tasks"][1]["priority"] == "low"
    assert payload["next_steps"] == ["人工复核后再联系"]


def test_dashboard_agents_inbox_reads_runtime_artifacts_in_mtime_order(tmp_path: Path) -> None:
    brief_path = tmp_path / "local-p7-83-brief-agent-v0.json"
    recommendation_path = tmp_path / "local-p7-82-recommendation-agent-v0.json"
    _write(
        brief_path,
        {
            "mode": "p7_83_brief_agent_v0",
            "passed": True,
            "generated_at": "2026-05-23T11:38:48Z",
            "summary": {"headline": "今天优先处理候选 KOL", "brief_item_count": 2},
            "next_steps": ["打开证据链"],
        },
    )
    _write(
        recommendation_path,
        {
            "mode": "p7_82_recommendation_agent_v0",
            "passed": True,
            "generated_at": "2026-05-23T11:34:20Z",
            "summary": {"candidate_count": 3, "agent_status": "ready"},
            "next_steps": ["人工复核后再联系"],
        },
    )
    os.utime(brief_path, (100, 100))
    os.utime(recommendation_path, (200, 200))

    payload = dashboard_domain._build_dashboard_agents_inbox(str(tmp_path), limit=10)

    assert payload["is_real"] is True
    assert payload["total"] == 2
    assert payload["items"][0]["agent_id"] == "recommendation"
    assert payload["items"][0]["title"] == "推荐 Agent · 3 个候选"
    assert payload["items"][0]["status"] == "active"
    assert payload["items"][0]["details"]["next_steps"] == ["人工复核后再联系"]
    assert payload["items"][1]["agent_id"] == "brief"
    assert payload["items"][1]["title"] == "今天优先处理候选 KOL"


def test_dashboard_agents_inbox_filters_agent_and_limits(tmp_path: Path) -> None:
    _write(
        tmp_path / "local-p7-83-brief-agent-v0.json",
        {
            "mode": "p7_83_brief_agent_v0",
            "passed": True,
            "summary": {"headline": "简报输出"},
        },
    )
    _write(
        tmp_path / "local-p7-82-recommendation-agent-v0.json",
        {
            "mode": "p7_82_recommendation_agent_v0",
            "passed": False,
            "summary": {"candidate_count": 1, "agent_status": "blocked"},
        },
    )

    payload = dashboard_domain._build_dashboard_agents_inbox(str(tmp_path), limit=1, agent_id="recommendation")

    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["agent_id"] == "recommendation"
    assert payload["items"][0]["agent_id"] == "recommendation"
    assert payload["items"][0]["status"] == "warning"
