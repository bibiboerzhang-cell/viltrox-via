"""Pure binding check for a server-recorded, current search interruption."""
from collections.abc import Mapping
from typing import Any


def confirmed_execution_failure(summary: Mapping[str, Any]) -> str | None:
    job = summary.get("smart_search_profile_advance_job")
    if not isinstance(job, Mapping):
        return None
    execution_id, job_id = summary.get("search_execution_id"), summary.get("search_execution_job_id")
    if (isinstance(execution_id, str) and execution_id.strip()
            and type(job_id) is int and job_id > 0
            and job.get("execution_id") == execution_id
            and type(job.get("job_id")) is int and job.get("job_id") == job_id
            and job.get("status") == "failed"
            and isinstance(job.get("reason"), str)
            and job.get("reason") in {"search_pipeline_cancelled", "search_pipeline_failed"}):
        return str(job["reason"])
    return None


def project_execution_observation(session: Mapping[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Stop waiting for this orchestration, not for every provider/child unit.

    Existing successful/active/terminal unit observations remain unchanged.
    No provider outcome or charge is inferred from cancellation.
    """
    summary = session.get("result_summary")
    reason = confirmed_execution_failure(summary) if isinstance(summary, Mapping) else None
    if reason is None:
        return contract
    return {**contract, "state": "failed", "phase": "failed", "orchestration_interrupted": True,
            "observation_terminal": True, "interruption_reason": reason,
            "completion_kind": "orchestration_interrupted"}
