"""Server execution observation fence, not provider authorization or budget."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import json
from typing import Any

from app.domains.kol.search_execution_observation import confirmed_execution_failure


@dataclass(frozen=True)
class ExecutionFence:
    session_id: int
    execution_id: str
    job_id: int


_CURRENT: ContextVar[ExecutionFence | None] = ContextVar("kol_search_execution_fence", default=None)


class SearchExecutionSuperseded(ValueError):
    """Stop this orchestration after an observation-owner mismatch, without retry."""


def execution_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    execution_id = payload.get("_search_execution_id")
    if execution_id is None:
        return {}
    job_id = payload.get("job_id")
    if not isinstance(execution_id, str) or not execution_id.strip() or type(job_id) is not int or job_id <= 0:
        raise ValueError("invalid_server_search_execution")
    return {"expected_execution_id": execution_id, "expected_job_id": job_id}


def validate_execution_start(patch: dict[str, Any]) -> None:
    observation = execution_kwargs({"_search_execution_id": patch.get("search_execution_id"),
                                    "job_id": patch.get("search_execution_job_id")})
    job = patch.get("smart_search_profile_advance_job")
    if not observation or not isinstance(job, dict) or job.get("status") != "running" or job.get("job_id") != observation["expected_job_id"]:
        raise ValueError("invalid_server_search_execution_start")


def resolve_fence(session_id: int, expected_execution_id: str | None = None,
                  expected_job_id: int | None = None) -> ExecutionFence | None:
    if expected_execution_id is None and expected_job_id is None:
        return _CURRENT.get()
    if (not isinstance(expected_execution_id, str) or not expected_execution_id.strip()
            or type(expected_job_id) is not int or expected_job_id <= 0):
        raise ValueError("invalid_server_search_execution")
    return ExecutionFence(int(session_id), expected_execution_id, expected_job_id)


def permits_write(summary: dict[str, Any], session_id: int, fence: ExecutionFence | None) -> bool:
    if fence is None:
        return True
    return bool(fence.session_id == int(session_id)
                and summary.get("search_execution_id") == fence.execution_id
                and summary.get("search_execution_job_id") == fence.job_id
                and not confirmed_execution_failure(summary))


def bind_patch_observation(patch: dict[str, Any], fence: ExecutionFence) -> None:
    job = patch.get("smart_search_profile_advance_job")
    if isinstance(job, dict):
        patch["smart_search_profile_advance_job"] = {**job, "job_id": fence.job_id, "execution_id": fence.execution_id}
    lanes = patch.get("search_lanes")
    if isinstance(lanes, dict):
        patch["search_lanes"] = {key: {**value, "execution_id": fence.execution_id}
                                 if key in {"local", "online"} and isinstance(value, dict) else value
                                 for key, value in lanes.items()}


def locked_write_allowed(conn: Any, session_id: int) -> bool:
    fence = resolve_fence(session_id)
    if fence is None:
        return True
    row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=? FOR NO KEY UPDATE",
        (int(session_id),),
    ).fetchone()
    if not row:
        return False
    raw = dict(row).get("result_summary_json")
    summary = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    return permits_write(summary, session_id, fence)


def superseded_result(session_id: int) -> dict[str, Any]:
    return {"status": "superseded", "session_id": int(session_id), "write_db": False,
            "write_applied": False, "write_reason": "search_execution_not_current"}


def require_applied(result: Any) -> Any:
    if isinstance(result, dict) and result.get("write_applied") is False:
        raise SearchExecutionSuperseded("search_execution_not_current")
    return result


def bind_pipeline_execution(function):
    @wraps(function)
    async def bound(*, session_id: int, payload: dict[str, Any], provider_actor=None):
        kwargs = execution_kwargs(payload)
        fence = resolve_fence(session_id, **kwargs) if kwargs else None
        token = _CURRENT.set(fence)
        try:
            return await function(session_id=session_id, payload=payload, provider_actor=provider_actor)
        except SearchExecutionSuperseded:
            return superseded_result(session_id)
        finally:
            _CURRENT.reset(token)
    return bound
