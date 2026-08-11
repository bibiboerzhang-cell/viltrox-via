"""Workflow callbacks must check the live fence immediately before sinks."""
from __future__ import annotations

import asyncio

import pytest

from app.domains.actions import agent_cycle_workflow, inbox
from app.domains.discovery import apify_enrich, enroll
from app.domains.kol import onboarding_workflow
from app.domains.logistics import seventeen_track
from app.domains.platform import event_ledger, workflow_engine, workflow_recovery
from app.domains.projects import fulfillment_workflow, observation_windows
from app.services.scheduler import jobs_tasks
from app.api.routers import vkpi_agents


def _fence(sequence: list[str], key: str = "workflow:7:step:0"):
    def require() -> dict[str, object]:
        sequence.append("fence")
        return {
            "run_id": 7,
            "step_index": 0,
            "step_name": "test",
            "owner_id": "worker",
            "fence_token": 3,
            "side_effect_key": key,
            "external_exactly_once": False,
        }

    return require


def test_onboarding_writers_are_fenced_and_carry_side_effect_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence: list[str] = []
    details: list[dict[str, object]] = []
    monkeypatch.setattr(workflow_engine, "require_workflow_fence", _fence(sequence))
    monkeypatch.setattr(
        enroll,
        "federated_discover_and_enroll",
        lambda *_args, **_kwargs: sequence.append("discover_sink")
        or {"found": 1, "enrolled": 1, "enrolled_ids": [11, 12]},
    )
    monkeypatch.setattr(
        apify_enrich,
        "enrich_kol",
        lambda kid: sequence.append(f"enrich_sink:{kid}") or {"status": "ok"},
    )
    from app.domains.memory import agent_memory_writer

    monkeypatch.setattr(
        agent_memory_writer,
        "record_signal",
        lambda **kwargs: sequence.append("memory_sink")
        or details.append(dict(kwargs["detail"]))
        or 1,
    )

    steps = onboarding_workflow.build_kol_onboarding_steps("lens")
    discovered = steps[0][1]({})
    enriched = steps[1][1](discovered)
    memory = steps[2][1]({**discovered, **enriched})

    assert sequence == [
        "fence",
        "discover_sink",
        "fence",
        "enrich_sink:11",
        "fence",
        "enrich_sink:12",
        "fence",
        "memory_sink",
    ]
    assert details[0]["workflow_side_effect_key"] == "workflow:7:step:0"
    assert details[0]["external_exactly_once"] is False
    assert memory["external_exactly_once"] is False


def test_fulfillment_writers_check_fence_before_each_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence: list[str] = []
    monkeypatch.setattr(workflow_engine, "require_workflow_fence", _fence(sequence))
    monkeypatch.setattr(
        seventeen_track,
        "enqueue_logistics_sync_job",
        lambda **_kwargs: sequence.append("queue_sink") or {"status": "queued"},
    )
    monkeypatch.setattr(
        observation_windows,
        "scan_delivered_into_windows",
        lambda _staff: sequence.append("delivered_sink") or {"created": [1]},
    )
    monkeypatch.setattr(
        observation_windows,
        "scan_windows_for_content",
        lambda _staff: sequence.append("content_sink") or {"matched": 1},
    )

    outputs = [callback({}) for _name, callback in fulfillment_workflow.build_fulfillment_steps()]

    assert sequence == [
        "fence",
        "queue_sink",
        "fence",
        "delivered_sink",
        "fence",
        "content_sink",
    ]
    assert outputs[0]["logistics_sync"]["external_exactly_once"] is False
    assert outputs[0]["logistics_sync"]["side_effect_key"] == "workflow:7:step:0"


def test_agent_cycle_writers_are_fenced_and_event_carries_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence: list[str] = []
    event_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(workflow_engine, "require_workflow_fence", _fence(sequence))
    monkeypatch.setattr(
        inbox,
        "generate_daily_action_inbox",
        lambda *_args, **_kwargs: sequence.append("inbox_sink")
        or {"persisted": 2, "by_category": {"kol": 2}},
    )
    monkeypatch.setattr(
        inbox,
        "list_inbox",
        lambda *_args, **_kwargs: {"items": []},
    )
    monkeypatch.setattr(
        event_ledger,
        "emit",
        lambda *_args, **kwargs: sequence.append("event_sink")
        or event_payloads.append(dict(kwargs["payload"]))
        or 1,
    )

    state: dict[str, object] = {}
    for _name, callback in agent_cycle_workflow.build_agent_cycle_steps():
        state.update(callback(state))

    assert sequence == ["fence", "inbox_sink", "fence", "event_sink"]
    assert event_payloads[0]["workflow_side_effect_key"] == "workflow:7:step:0"
    assert event_payloads[0]["external_exactly_once"] is False
    assert state["external_exactly_once"] is False


def test_agent_cycle_has_explicit_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflow_engine,
        "run",
        lambda run_id, steps: {"status": "completed", "run_id": run_id, "steps": len(steps)},
    )

    result = agent_cycle_workflow.resume_agent_cycle(44)

    assert result == {"status": "completed", "run_id": 44, "steps": 3}


def test_agent_cycle_resume_route_reuses_same_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = {
        "id": 9,
        "staff_id": 9,
        "role": "manager",
        "permissions": {"vkpi": "write"},
        "organization_id": 1,
        "organization_scope_status": "resolved",
    }
    monkeypatch.setattr(
        agent_cycle_workflow,
        "resume_agent_cycle",
        lambda run_id, staff: {"status": "completed", "run_id": run_id, "staff": staff},
    )

    result = vkpi_agents.agent_cycle_resume(52, staff=manager)

    assert result == {"status": "completed", "run_id": 52, "staff": manager}


def test_scheduler_uses_recovery_path_instead_of_direct_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        workflow_recovery,
        "run_scheduled_workflow",
        lambda name, _staff: calls.append(name)
        or {"status": "completed", "run_id": len(calls), "scheduled_action": "resume_existing"},
    )

    asyncio.run(jobs_tasks.job_vkpi_fulfillment_sweep())
    asyncio.run(jobs_tasks.job_vkpi_agent_cycle())

    assert calls == ["fulfillment_sweep", "agent_cycle"]
