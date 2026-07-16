"""Crash recovery dispatcher for migration-265 durable workflows.

The recovery scanner never invents callbacks from database data.  It accepts
only the three workflow names compiled into this process, rebuilds their
existing step definitions, and calls ``workflow_engine.run`` with the original
``run_id``.  Unknown names fail closed and remain untouched for operator
inspection.

Workflow fencing makes run/step/checkpoint state single-writer.  It does not
make arbitrary external providers exactly-once; callbacks surface that limit
as ``external_exactly_once=false`` and pass their logical side-effect key where
the existing sink contract can carry it.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.platform import workflow_engine, workflow_repository


logger = get_logger(__name__)
SUPPORTED_WORKFLOWS = frozenset(
    {"kol_onboarding", "fulfillment_sweep", "agent_cycle"}
)


def _build_steps(
    workflow_name: str,
    input_value: dict[str, Any],
    staff: dict[str, Any] | None,
) -> list[workflow_engine.Step] | None:
    """Resolve only code-owned builders; database names cannot import code."""

    if workflow_name == "kol_onboarding":
        from app.domains.kol import onboarding_workflow

        query = str(input_value.get("query") or "").strip()
        if not query:
            return None
        return onboarding_workflow.build_kol_onboarding_steps(query, staff)
    if workflow_name == "fulfillment_sweep":
        from app.domains.projects import fulfillment_workflow

        return fulfillment_workflow.build_fulfillment_steps(staff)
    if workflow_name == "agent_cycle":
        from app.domains.actions import agent_cycle_workflow

        return agent_cycle_workflow.build_agent_cycle_steps(staff)
    return None


def _start_new(
    workflow_name: str,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    if workflow_name == "fulfillment_sweep":
        from app.domains.projects import fulfillment_workflow

        return fulfillment_workflow.start_fulfillment_sweep(staff)
    if workflow_name == "agent_cycle":
        from app.domains.actions import agent_cycle_workflow

        return agent_cycle_workflow.start_agent_cycle(staff)
    return {
        "status": "unsupported_workflow",
        "workflow_name": workflow_name,
        "reason": "scheduled_workflow_not_allowlisted",
    }


def recover_run(
    run_id: int,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover one known run under a fresh claim and the same durable id."""

    if not workflow_repository.schema_ready():
        return {
            "status": "unavailable",
            "run_id": int(run_id),
            "reason": "workflow_fencing_schema_missing",
        }
    row = workflow_repository.get_run(int(run_id))
    if row is None:
        return {"status": "not_found", "run_id": int(run_id)}
    workflow_name = str(row.get("workflow_name") or "").strip()
    if workflow_name not in SUPPORTED_WORKFLOWS:
        logger.error(
            "workflow.recovery_unsupported",
            extra={"run_id": int(run_id), "workflow_name": workflow_name},
        )
        return {
            "status": "unsupported_workflow",
            "run_id": int(run_id),
            "workflow_name": workflow_name,
            "reason": "workflow_recovery_dispatch_missing",
            "claimed": False,
        }
    input_value = workflow_repository.latest_state(
        int(run_id), row.get("input_json")
    )
    # Builders need only immutable input fields.  latest_state preserves the
    # original query while also allowing a future builder to use checkpoints.
    steps = _build_steps(workflow_name, input_value, staff)
    if steps is None:
        return {
            "status": "invalid_workflow_input",
            "run_id": int(run_id),
            "workflow_name": workflow_name,
            "reason": "workflow_recovery_input_missing",
            "claimed": False,
        }
    result = workflow_engine.run(int(run_id), steps)
    return {
        **result,
        "run_id": int(run_id),
        "workflow_name": workflow_name,
        "recovery_dispatch": True,
        "external_exactly_once": False,
    }


def sweep_recoverable_runs(
    staff: dict[str, Any] | None = None,
    *,
    limit: int = 20,
    minimum_age_seconds: int = 60,
) -> dict[str, Any]:
    """Boundedly resume expired/failed/paused runs without creating new ones."""

    if not workflow_repository.schema_ready():
        return {
            "status": "unavailable",
            "reason": "workflow_fencing_schema_missing",
            "scanned": 0,
            "results": [],
        }
    candidates = workflow_repository.list_recoverable_runs(
        limit=limit,
        minimum_age_seconds=minimum_age_seconds,
    )
    results = [recover_run(int(row["id"]), staff) for row in candidates]
    return {
        "status": "ok",
        "scanned": len(candidates),
        "completed": sum(
            1 for result in results if result.get("status") == "completed"
        ),
        "in_progress": sum(
            1 for result in results if result.get("status") == "in_progress"
        ),
        "failed": sum(1 for result in results if result.get("status") == "failed"),
        "unsupported": sum(
            1
            for result in results
            if result.get("status")
            in {"unsupported_workflow", "invalid_workflow_input"}
        ),
        "results": results,
        "external_exactly_once": False,
    }


def run_scheduled_workflow(
    workflow_name: str,
    staff: dict[str, Any] | None = None,
    *,
    organization_id: int = 1,
) -> dict[str, Any]:
    """Resume the oldest unfinished scheduled run, else create one.

    The existence check is an optimization rather than a uniqueness proof.
    Scheduler fleet fire claims prevent duplicate planned fires, while the run
    claim fences an existing run.  We therefore report this boundary instead
    of claiming cross-system exactly-once execution.
    """

    clean_name = str(workflow_name or "").strip()
    if clean_name not in {"fulfillment_sweep", "agent_cycle"}:
        return {
            "status": "unsupported_workflow",
            "workflow_name": clean_name,
            "reason": "scheduled_workflow_not_allowlisted",
        }
    if not workflow_repository.schema_ready():
        return {
            "status": "unavailable",
            "workflow_name": clean_name,
            "reason": "workflow_fencing_schema_missing",
        }
    unfinished = workflow_repository.find_unfinished_run(
        clean_name,
        organization_id=organization_id,
    )
    if unfinished is not None:
        result = recover_run(int(unfinished["id"]), staff)
        return {**result, "scheduled_action": "resume_existing"}
    result = _start_new(clean_name, staff)
    return {**result, "scheduled_action": "start_new"}
