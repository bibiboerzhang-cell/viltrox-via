from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.domains.projects import contract_assist, outreach, retrospective_aggregate


ROOT = Path(__file__).resolve().parents[1]
PROJECT_MODULES = (
    ROOT / "backend/app/domains/projects/outreach.py",
    ROOT / "backend/app/domains/projects/retrospective_aggregate.py",
    ROOT / "backend/app/domains/projects/contract_assist.py",
)


def _call_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_project_text_generation_has_no_legacy_gateway_call() -> None:
    calls: list[str] = []
    for path in PROJECT_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls.extend(_call_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert "llm_gateway.invoke" not in calls
    assert "llm_gateway.invoke_json" not in calls
    assert calls.count("llm_production.generate_json") == 3


def test_outreach_uses_exact_single_model_and_progress_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        outreach,
        "_load_creators",
        lambda _ids: ([{"id": 1, "display_name": "Creator", "handle": "creator", "platform": "youtube"}], []),
    )
    monkeypatch.setattr(outreach, "_outreach_binding", lambda _preferred: ("openai", "gpt-exact"))
    captured: dict[str, Any] = {}

    def fake_generate_json(_prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-exact",
            "json": {
                "messages": [{"kol_pool_id": 1, "subject": "Hello", "body": "A specific draft"}],
                "sow_draft": {
                    "scope": "Review",
                    "deliverables": ["One video"],
                    "timeline": "Two weeks",
                    "usage_rights": "Three months",
                    "compensation": "to be discussed",
                },
            },
        }

    monkeypatch.setattr(outreach.llm_production, "generate_json", fake_generate_json)
    result = outreach.generate_outreach([1], brief={"query_text": "camera reviewer"})

    assert result["llm_used"] is True
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-exact"
    assert captured["required_keys"] == ("messages", "sow_draft")
    assert captured["metadata"]["phase"] == "project_outreach"
    assert captured["metadata"]["attempt_index"] == 1
    assert captured["metadata"]["total"] == 1


def test_contract_polish_ai_unavailable_never_writes_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        contract_assist.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: {
            "status": "unavailable",
            "failure": {"code": "model_readiness_blocked"},
        },
    )
    monkeypatch.setattr(contract_assist, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("must not write")))
    result = contract_assist.run_contract_polish_for_job(
        {"polish_key": "p1", "project_id": 7, "fields": {"deliverables": "One video"}}
    )
    assert result == {"status": "failed", "reason": "model_readiness_blocked"}


def test_retrospective_ai_unavailable_never_writes_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    class Conn:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("must not write")

    monkeypatch.setattr(retrospective_aggregate, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        retrospective_aggregate.cache_repo,
        "list_project_video_analysis_cache",
        lambda *_args, **_kwargs: {
            "items": [{"state": "ready", "entry": {"result": {}}, "evidence_id": 1, "view_count": 10}]
        },
    )
    from app.domains.projects import observation_windows

    monkeypatch.setattr(observation_windows, "matched_content_posts_for_retrospective", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        retrospective_aggregate.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: {"status": "unavailable", "failure": {"code": "budget_guard_blocked"}},
    )
    result = retrospective_aggregate.run_project_retrospective(7)
    assert result["status"] == "failed"
    assert result["reason"] == "budget_guard_blocked"
