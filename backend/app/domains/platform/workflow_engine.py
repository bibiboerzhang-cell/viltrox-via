"""Lease-fenced durable workflow engine.

The public ``start_run``/``run``/``get_run`` API remains compatible with the
pre-265 engine.  Internally, every invocation must first acquire one durable
run claim.  Every step begin, checkpoint, failure and completion is then a
compare-and-swap carrying that claim's owner, token hash and monotonically
increasing fence token.

Step callbacks still receive a plain state dict.  A reserved
``__vkpi_workflow_execution__`` entry and the context helpers below expose the
logical side-effect key and current fence to downstream writers.  The raw
lease token is never put in state, checkpoints, events or logs.
"""
from __future__ import annotations

import contextvars
import os
import socket
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from app.core.logging import get_logger
from app.domains.platform import workflow_repository


logger = get_logger(__name__)

Step = tuple[str, Callable[[dict[str, Any]], dict[str, Any] | None]]
_EXECUTION_STATE_KEY = "__vkpi_workflow_execution__"
# The engine can fence its own durable state, but existing external providers
# and several legacy sinks do not all accept a durable idempotency key.  Keep
# this explicit in every callback/result instead of implying exactly-once.
EXTERNAL_EXACTLY_ONCE = False


class WorkflowFenceLost(RuntimeError):
    """The step owner no longer holds a live workflow execution fence."""


@dataclass(frozen=True, slots=True)
class WorkflowExecutionContext:
    run_id: int
    step_index: int
    step_name: str
    owner_id: str
    fence_token: int
    side_effect_key: str
    _claim: workflow_repository.WorkflowClaim = field(repr=False, compare=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "step_index": self.step_index,
            "step_name": self.step_name,
            "owner_id": self.owner_id,
            "fence_token": self.fence_token,
            "side_effect_key": self.side_effect_key,
            "external_exactly_once": EXTERNAL_EXACTLY_ONCE,
        }


_execution_context: contextvars.ContextVar[WorkflowExecutionContext | None] = (
    contextvars.ContextVar("vkpi_workflow_execution", default=None)
)


def current_execution_context() -> WorkflowExecutionContext | None:
    """Return the current callback's non-persisted execution context."""

    return _execution_context.get()


def require_workflow_fence() -> dict[str, Any]:
    """Fail closed unless the caller is inside the exact live step fence.

    Side-effecting sinks can call this immediately before their own fenced CAS
    and persist ``run_id``, ``step_index`` and ``fence_token`` alongside their
    idempotency key.  Merely possessing stale callback state is insufficient.
    """

    context = current_execution_context()
    if context is None:
        raise WorkflowFenceLost("workflow_execution_context_missing")
    if not workflow_repository.claim_is_live(context._claim):
        raise WorkflowFenceLost("workflow_execution_fence_lost")
    return context.public_dict()


def _emit(event_type: str, run: dict[str, Any], **extra: Any) -> None:
    try:
        from app.domains.platform import event_ledger

        event_ledger.emit(
            event_type,
            entity_type="workflow",
            entity_id=run.get("id"),
            actor_type="system",
            source="workflow_engine",
            payload={"workflow": run.get("workflow_name"), **extra},
            trace_id=str(run.get("trace_id") or ""),
        )
    except Exception:
        logger.warning("workflow.event_emit_failed", exc_info=True)


def _default_owner_id() -> str:
    return (
        f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}:"
        f"{uuid.uuid4().hex[:12]}"
    )


def start_run(
    workflow_name: str,
    *,
    input: dict[str, Any] | None = None,
    entity_type: str = "",
    entity_id: Any = "",
    organization_id: int = 1,
) -> dict[str, Any]:
    """Create a resumable run.  Execution still begins through ``run`` claim."""

    if not workflow_repository.schema_ready():
        return {
            "status": "unavailable",
            "reason": "workflow_fencing_schema_missing",
        }
    from app.domains.platform import event_ledger

    trace = event_ledger.new_trace_id("wf", workflow_name, entity_id)
    row = workflow_repository.create_run(
        str(workflow_name),
        input_value=input,
        entity_type=str(entity_type or ""),
        entity_id=str(entity_id or ""),
        organization_id=int(organization_id),
        trace_id=trace,
    )
    run = {"id": row["id"], "workflow_name": workflow_name, "trace_id": trace}
    _emit("workflow_started", run)
    return {"status": "ok", "run_id": row["id"], "trace_id": trace}


def _heartbeat_claim(
    claim: workflow_repository.WorkflowClaim,
    stop: threading.Event,
    lost: threading.Event,
    *,
    lease_seconds: int,
) -> None:
    interval = max(5.0, min(60.0, float(lease_seconds) / 3.0))
    while not stop.wait(interval):
        try:
            if not workflow_repository.renew_claim(
                claim,
                lease_seconds=lease_seconds,
            ):
                lost.set()
                return
        except Exception:
            logger.warning(
                "workflow.lease_heartbeat_failed",
                extra={"run_id": claim.run_id, "fence_token": claim.fence_token},
                exc_info=True,
            )
            lost.set()
            return


@contextmanager
def _step_execution(
    claim: workflow_repository.WorkflowClaim,
    *,
    step_index: int,
    step_name: str,
    lease_seconds: int,
) -> Iterator[WorkflowExecutionContext]:
    context = WorkflowExecutionContext(
        run_id=claim.run_id,
        step_index=int(step_index),
        step_name=str(step_name),
        owner_id=claim.owner_id,
        fence_token=claim.fence_token,
        side_effect_key=f"workflow:{claim.run_id}:step:{int(step_index)}",
        _claim=claim,
    )
    token = _execution_context.set(context)
    stop = threading.Event()
    lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_claim,
        args=(claim, stop, lost),
        kwargs={"lease_seconds": lease_seconds},
        name=f"workflow-heartbeat-{claim.run_id}-{step_index}",
        daemon=True,
    )
    heartbeat.start()
    try:
        if not workflow_repository.claim_is_live(claim):
            raise WorkflowFenceLost("workflow_execution_fence_lost_before_step")
        yield context
        if lost.is_set() or not workflow_repository.claim_is_live(claim):
            raise WorkflowFenceLost("workflow_execution_fence_lost_during_step")
    finally:
        stop.set()
        heartbeat.join(timeout=2)
        _execution_context.reset(token)


def run(
    run_id: int,
    steps: list[Step],
    *,
    owner_id: str = "",
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Resume and execute a run under one exclusive, renewable claim."""

    if not workflow_repository.schema_ready():
        return {
            "status": "unavailable",
            "run_id": int(run_id),
            "reason": "workflow_fencing_schema_missing",
        }
    claim_result = workflow_repository.claim_run(
        int(run_id),
        owner_id or _default_owner_id(),
        lease_seconds=lease_seconds,
    )
    if claim_result.get("status") == "not_found":
        return {"status": "not_found", "run_id": int(run_id)}
    if claim_result.get("status") == "completed":
        try:
            completion = workflow_repository.ensure_completed_event(int(run_id))
        except workflow_repository.WorkflowCompletionEvidenceError as exc:
            return {
                "status": "blocked",
                "run_id": int(run_id),
                "reason": exc.reason,
                "run_status": "completed",
                "retryable": True,
                "external_exactly_once": EXTERNAL_EXACTLY_ONCE,
            }
        row = workflow_repository.get_run(int(run_id)) or {}
        state = workflow_repository.latest_state(int(run_id), row.get("input_json"))
        return {
            "status": "completed",
            "run_id": int(run_id),
            "steps": len(steps),
            "state": state,
            "already_completed": True,
            "completion_event_id": int(completion["event_id"]),
            "completion_evidence_repaired": bool(completion["repaired"]),
            "external_exactly_once": EXTERNAL_EXACTLY_ONCE,
        }
    if claim_result.get("status") != "acquired":
        return {
            "status": "in_progress",
            "run_id": int(run_id),
            "reason": claim_result.get("reason") or "workflow_live_lease",
            "lease_owner": claim_result.get("lease_owner") or "",
            "lease_expires_at": claim_result.get("lease_expires_at") or "",
            "fence_token": int(claim_result.get("fence_token") or 0),
            "retryable": True,
        }

    claim = claim_result["claim"]
    if not isinstance(claim, workflow_repository.WorkflowClaim):
        raise RuntimeError("workflow claim contract invalid")
    row = workflow_repository.get_run(int(run_id))
    if row is None:
        return {"status": "not_found", "run_id": int(run_id)}
    start = int(row.get("current_step") or claim.current_step)
    state = workflow_repository.latest_state(int(run_id), row.get("input_json"))

    for index in range(start, len(steps)):
        name, callback = steps[index]
        if not workflow_repository.begin_step(claim, index, name):
            return {
                "status": "fenced",
                "run_id": int(run_id),
                "failed_step": index,
                "reason": "workflow_execution_fence_lost_before_step",
                "retryable": True,
            }
        try:
            with _step_execution(
                claim,
                step_index=index,
                step_name=name,
                lease_seconds=lease_seconds,
            ) as execution:
                callback_state = dict(state)
                callback_state[_EXECUTION_STATE_KEY] = execution.public_dict()
                output = callback(callback_state) or {}
        except Exception as exc:
            error = str(exc)[:480]
            failed = workflow_repository.fail_step(claim, index, error)
            if not failed:
                return {
                    "status": "fenced",
                    "run_id": int(run_id),
                    "failed_step": index,
                    "step_name": name,
                    "reason": "workflow_execution_fence_lost_during_failure",
                    "retryable": True,
                }
            logger.warning(
                "workflow.step_failed",
                extra={"run_id": int(run_id), "step": index},
                exc_info=True,
            )
            _emit("workflow_failed", row, step=index, error=error)
            return {
                "status": "failed",
                "run_id": int(run_id),
                "failed_step": index,
                "step_name": name,
                "error": error,
                "note": "已停在该步；修因后再次 run 将重新取得更高 fence 并从该步续跑。",
            }
        if not isinstance(output, dict):
            output = {}
        clean_output = dict(output)
        clean_output.pop(_EXECUTION_STATE_KEY, None)
        state = {**state, **clean_output}
        state.pop(_EXECUTION_STATE_KEY, None)
        if not workflow_repository.commit_step(
            claim,
            index,
            output=clean_output,
            state=state,
        ):
            return {
                "status": "fenced",
                "run_id": int(run_id),
                "failed_step": index,
                "step_name": name,
                "reason": "workflow_execution_fence_lost_before_commit",
                "retryable": True,
            }
        _emit(
            "workflow_step_done",
            row,
            step=index,
            step_name=name,
            fence_token=claim.fence_token,
        )

    try:
        completed = workflow_repository.complete_run(claim, expected_steps=len(steps))
    except workflow_repository.WorkflowCompletionEvidenceError as exc:
        return {
            "status": "blocked",
            "run_id": int(run_id),
            "reason": exc.reason,
            "run_status": "completion_unverified",
            "retryable": True,
            "external_exactly_once": EXTERNAL_EXACTLY_ONCE,
        }
    if not completed:
        return {
            "status": "fenced",
            "run_id": int(run_id),
            "reason": "workflow_execution_fence_lost_before_completion",
            "retryable": True,
        }
    return {
        "status": "completed",
        "run_id": int(run_id),
        "steps": len(steps),
        "state": state,
        "fence_token": claim.fence_token,
        "recovered": claim.recovered,
        "completion_evidence": "atomic",
        "external_exactly_once": EXTERNAL_EXACTLY_ONCE,
    }


def get_run(run_id: int) -> dict[str, Any]:
    """Read one run without ever returning its lease token hash."""

    if not workflow_repository.schema_ready():
        return {
            "status": "unavailable",
            "run_id": int(run_id),
            "reason": "workflow_fencing_schema_missing",
        }
    row = workflow_repository.get_run(int(run_id))
    if row is None:
        return {"status": "not_found", "run_id": int(run_id)}
    step_rows = workflow_repository.list_steps(int(run_id))
    checkpoint_total = workflow_repository.checkpoint_count(int(run_id))
    status = str(row.get("status") or "")
    lease_live = bool(
        status == "running"
        and row.get("lease_token_hash")
        and not workflow_repository.lease_is_expired(row.get("lease_expires_at"))
    )
    return {
        "status": "ok",
        "run": {
            key: row.get(key)
            for key in (
                "id",
                "workflow_name",
                "status",
                "current_step",
                "trace_id",
                "last_error",
                "lease_owner",
                "lease_expires_at",
                "heartbeat_at",
                "fence_token",
                "attempt_no",
                "row_version",
            )
        },
        "lease_live": lease_live,
        "steps": step_rows,
        "checkpoints": checkpoint_total,
        "resumable": bool(status in {"failed", "paused"} or (status == "running" and not lease_live)),
        "external_exactly_once": EXTERNAL_EXACTLY_ONCE,
    }
