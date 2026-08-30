"""Contracts for the project-to-creator lifecycle dependency inversion."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_search, vkpi_projects
from app.domains.projects import workflow_projects
from app.services.projects import creator_lifecycle_adapters as adapters


ROOT = Path(__file__).resolve().parents[1]
PROJECT_SOURCES = (
    "backend/app/domains/projects/workflow_evidence.py",
    "backend/app/domains/projects/workflow_evidence_project_writes.py",
    "backend/app/domains/projects/workflow_projects.py",
    "backend/app/domains/projects/workflow_projects_kols.py",
)


def test_project_sources_have_no_kol_or_recommendation_lifecycle_import() -> None:
    forbidden = ("app.domains.kol", "app.domains.recommendations")
    for relative in PROJECT_SOURCES:
        path = ROOT / relative
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert not any(name.startswith(forbidden) for name in imported), relative
        assert "importlib" not in imported
        assert "__import__(" not in source


def test_default_adapters_preserve_exact_legacy_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    pool_result = {"linked": True}
    outreach_result = [{"linked": True}]
    session_result = {"id": 7}
    summary_result = {"status": "ready"}
    release_result = {"released": [{"claim_id": 9}]}

    monkeypatch.setattr(
        adapters.pool_action_bridge,
        "bridge_pool_action",
        lambda *args, **kwargs: calls.append(("pool", args, kwargs)) or pool_result,
    )
    monkeypatch.setattr(
        adapters.pool_action_bridge,
        "bridge_message_outreach",
        lambda **kwargs: calls.append(("message", kwargs)) or outreach_result,
    )
    monkeypatch.setattr(
        adapters.search_sessions,
        "get_session",
        lambda *args, **kwargs: calls.append(("get", args, kwargs)) or session_result,
    )
    monkeypatch.setattr(
        adapters.search_sessions,
        "update_session_result_summary",
        lambda *args, **kwargs: calls.append(("summary", args, kwargs)) or summary_result,
    )
    monkeypatch.setattr(
        adapters.claims,
        "auto_release_claims_for_project",
        lambda *args, **kwargs: calls.append(("claim", args, kwargs)) or release_result,
    )

    feedback = adapters.ServiceRecommendationFeedbackSink()
    sessions = adapters.KolSearchSessionDraftAdapter()
    claims = adapters.KolClaimLifecycleAdapter()
    staff = {"id": 3}

    assert feedback.record_pool_action(
        11,
        "touch",
        staff=staff,
        note="n",
        payload={"project_id": 5},
        source="project_assignment",
    ) is pool_result
    assert feedback.record_message_outreach(
        message_id=4,
        project_id=5,
        kol_id=6,
        direction="outbound",
        staff=staff,
        source="project_message",
    ) is outreach_result
    assert sessions.get_session(7, staff=staff, scope_to_staff=True) is session_result
    assert sessions.update_result_summary(
        7,
        status="ready",
        summary_patch={"draft_project": {"project_id": 5}},
    ) is summary_result
    assert claims.auto_release_for_project(
        5,
        to_stage="closed",
        actor_staff_id=3,
        reason="done",
    ) is release_result

    assert calls == [
        (
            "pool",
            (11, "touch"),
            {
                "staff": staff,
                "note": "n",
                "payload": {"project_id": 5},
                "source": "project_assignment",
            },
        ),
        (
            "message",
            {
                "message_id": 4,
                "project_id": 5,
                "kol_id": 6,
                "direction": "outbound",
                "staff": staff,
                "source": "project_message",
            },
        ),
        ("get", (7,), {"staff": staff, "scope_to_staff": True}),
        (
            "summary",
            (7,),
            {
                "status": "ready",
                "summary_patch": {"draft_project": {"project_id": 5}},
            },
        ),
        (
            "claim",
            (5,),
            {"to_stage": "closed", "actor_staff_id": 3, "reason": "done"},
        ),
    ]


def test_http_composition_injects_all_reviewed_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def capture(name: str):
        def inner(*_args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append((name, kwargs))
            return {}

        return inner

    monkeypatch.setattr(vkpi_projects.workflow, "add_project_kols", capture("add"))
    monkeypatch.setattr(
        vkpi_projects.workflow,
        "advance_project_kol_assignment",
        capture("advance"),
    )
    monkeypatch.setattr(vkpi_projects.workflow, "transition_project", capture("transition"))
    monkeypatch.setattr(vkpi_projects.workflow, "delete_project", capture("delete"))
    monkeypatch.setattr(vkpi_projects.workflow, "add_project_message", capture("message"))
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_search_sessions,
        "require_session_owner",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.project_workflow,
        "create_project_draft_from_session",
        capture("draft"),
    )

    staff = {"id": 3}
    vkpi_projects.add_project_kols(1, {}, staff=staff)
    vkpi_projects.advance_project_kol(1, "2", {}, staff=staff)
    vkpi_projects.transition_project(1, {"to_stage": "closed"}, staff=staff)
    vkpi_projects.delete_project(1, {}, staff=staff)
    vkpi_projects.add_project_message(1, {}, staff=staff)
    vkpi_kol_pool_search.create_project_draft_from_kol_search_session(
        7,
        {},
        staff=staff,
    )

    by_name = {name: kwargs for name, kwargs in calls}
    assert by_name["add"]["feedback_sink"] is adapters.DEFAULT_RECOMMENDATION_FEEDBACK_SINK
    assert by_name["advance"]["feedback_sink"] is adapters.DEFAULT_RECOMMENDATION_FEEDBACK_SINK
    assert by_name["message"]["feedback_sink"] is adapters.DEFAULT_RECOMMENDATION_FEEDBACK_SINK
    assert by_name["transition"]["claim_lifecycle"] is adapters.DEFAULT_CLAIM_LIFECYCLE_PORT
    assert by_name["delete"]["claim_lifecycle"] is adapters.DEFAULT_CLAIM_LIFECYCLE_PORT
    assert by_name["draft"]["search_session_port"] is adapters.DEFAULT_SEARCH_SESSION_DRAFT_PORT
    assert by_name["draft"]["feedback_sink"] is adapters.DEFAULT_RECOMMENDATION_FEEDBACK_SINK


class _OneRow:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _TransitionConn:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.commits = 0

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _OneRow:
        if "SELECT * FROM vkpi_projects" in " ".join(sql.split()):
            return _OneRow(
                {
                    "id": 5,
                    "stage": "measured",
                    "stage_status": "active",
                    "sample_status": "received",
                    "tracking_number": "T",
                    "closed_at": None,
                }
            )
        return _OneRow()

    def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")


@pytest.mark.parametrize("fail", [False, True])
def test_terminal_claim_port_runs_after_commit_and_remains_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
    fail: bool,
) -> None:
    events: list[str] = []
    conn = _TransitionConn(events)

    class ClaimPort:
        def auto_release_for_project(self, project_id: int, **kwargs: Any) -> dict[str, Any]:
            assert conn.commits == 1
            events.append("claim")
            assert (project_id, kwargs) == (
                5,
                {"to_stage": "closed", "actor_staff_id": 3},
            )
            if fail:
                raise RuntimeError("claim boom")
            return {"released": [{"claim_id": 9}]}

    monkeypatch.setattr(workflow_projects, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow_projects.scope, "assert_project_access", lambda *_a, **_k: None)
    monkeypatch.setattr(workflow_projects, "get_conn", lambda: conn)
    monkeypatch.setattr(workflow_projects, "_validate_transition", lambda *_a, **_k: None)
    monkeypatch.setattr(
        workflow_projects,
        "_log_project_audit",
        lambda **_kwargs: events.append("audit"),
    )

    result = workflow_projects.transition_project(
        5,
        {"to_stage": "closed"},
        staff={"id": 3},
        claim_lifecycle=ClaimPort(),
    )

    assert events == ["commit", "claim", "audit"]
    assert result == {
        "id": 5,
        "from_stage": "measured",
        "to_stage": "closed",
        "auto_product_cost": None,
        "claim_auto_release": (
            {"status": "error", "reason": "claim boom"}
            if fail
            else {"released": [{"claim_id": 9}]}
        ),
    }
