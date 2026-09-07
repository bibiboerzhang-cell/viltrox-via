"""Lane-owned session summaries; only the orchestrator owns global completion."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.domains.kol.search_execution_observation import confirmed_execution_failure
from app.domains.kol.search_execution_fence import permits_write, resolve_fence
from app.domains.kol.search_sessions_serde import _dict, _json_dumps, _loads, _sanitize_session_payload

_PROJECTION_KEYS = {
    "local": {"method", "recall_snapshot_attached", "recall_snapshot_complete", "replay_contract",
              "local_qualification", "llm_query_plan", "filters", "ratio", "bucket_policy", "ranking"},
    "online": {"online_snapshot_attached", "online_snapshot_complete", "online_qualification", "new_discovery"},
}


def merge_lane_summary(existing: dict[str, Any], *, lane: str, status: str,
                       returned_count: int | None, summary: dict[str, Any] | None = None,
                       reason: str | None = None, execution_id: str | None = None,
                       expected_execution_id: str | None = None, expected_job_id: int | None = None) -> dict[str, Any]:
    """Pure merge: no lane can replace another lane or global status/progress."""
    if lane not in _PROJECTION_KEYS:
        raise ValueError("invalid_search_lane")
    output = deepcopy(existing)
    if expected_execution_id is not None or expected_job_id is not None:
        if not permits_write(existing, 0, resolve_fence(0, expected_execution_id, expected_job_id)):
            return output
        execution_id = expected_execution_id
    if execution_id and existing.get("search_execution_id") not in {None, execution_id}:
        return output
    if confirmed_execution_failure(existing):
        return output
    patch = _dict(summary)
    output.update({key: deepcopy(value) for key, value in patch.items() if key in _PROJECTION_KEYS[lane]})
    lanes = deepcopy(_dict(output.get("search_lanes")))
    execution_id = execution_id or _dict(lanes.get(lane)).get("execution_id")
    lanes[lane] = {"status": status, "returned_count": returned_count,
                   **({"reason": reason} if reason else {}),
                   **({"execution_id": execution_id} if execution_id else {})}
    output["search_lanes"] = lanes
    return output


def write_lane_summary(conn: Any, session_id: int, *, lane: str, status: str,
                       returned_count: int | None, summary: dict[str, Any] | None = None,
                       reason: str | None = None, execution_id: str | None = None,
                       expected_execution_id: str | None = None, expected_job_id: int | None = None) -> bool:
    # Serialize lane merges in the existing transaction. Neither lane writes
    # global status/phase/progress; no stale full-summary overwrite is possible.
    row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=? FOR NO KEY UPDATE",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    existing = _loads(dict(row).get("result_summary_json"), {})
    fence = resolve_fence(session_id, expected_execution_id, expected_job_id)
    if not permits_write(_dict(existing), session_id, fence):
        return False
    merged = merge_lane_summary(_dict(existing), lane=lane, status=status,
                                returned_count=returned_count, summary=summary, reason=reason,
                                execution_id=execution_id,
                                **({"expected_execution_id": fence.execution_id, "expected_job_id": fence.job_id} if fence else {}))
    if merged == existing:
        return not confirmed_execution_failure(_dict(existing)) and (not execution_id or _dict(existing).get("search_execution_id") in {None, execution_id})
    safe = _sanitize_session_payload(merged)
    conn.execute(
        "UPDATE vkpi_kol_search_sessions SET result_summary_json=?::jsonb, updated_at=NOW() WHERE id=?",
        (_json_dumps(safe), int(session_id)),
    )
    return True


def interrupted_lane_summary(existing: dict[str, Any], *, execution_id: str,
                             reason: str) -> dict[str, Any]:
    """Settle only this execution's unfinished lanes, never a newer attempt."""
    output = deepcopy(existing)
    if not execution_id:
        return output
    for lane in _PROJECTION_KEYS:
        current = _dict(_dict(output.get("search_lanes")).get(lane))
        if (current.get("execution_id") == execution_id
                and current.get("status") in {"queued", "already_queued", "running"}):
            output["search_lanes"][lane] = {
                **current, "status": "failed", "returned_count": None, "reason": reason,
            }
    return output


def preserve_execution_failure(summary: dict[str, Any], proposed_status: str) -> str:
    """A late child completion cannot undo this execution's confirmed failure."""
    return "failed" if confirmed_execution_failure(summary) else proposed_status


def retain_execution_observation(existing: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """Legacy batch replacement must not discard its active execution owner."""
    keys = ("search_execution_id", "search_execution_job_id", "smart_search_profile_advance_job", "search_lanes")
    retained = {key: deepcopy(existing[key]) for key in keys if key in existing}
    return {**deepcopy(proposed), **retained}


def write_interrupted_lanes(conn: Any, session_id: int, *, execution_id: str, reason: str) -> bool:
    row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=? FOR NO KEY UPDATE",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    existing = _dict(_loads(dict(row).get("result_summary_json"), {}))
    merged = interrupted_lane_summary(existing, execution_id=execution_id, reason=reason)
    if merged == existing:
        return False
    conn.execute(
        "UPDATE vkpi_kol_search_sessions SET result_summary_json=?::jsonb, updated_at=NOW() WHERE id=?",
        (_json_dumps(_sanitize_session_payload(merged)), int(session_id)),
    )
    return True


def write_execution_failure(conn: Any, session_id: int, *, execution_id: str,
                            job_id: int, reason: str, error: str) -> bool:
    """Session-only failure receipt; queue/provider lease state belongs to the executor."""
    row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=? FOR NO KEY UPDATE",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    existing = _dict(_loads(dict(row).get("result_summary_json"), {}))
    current_job = _dict(existing.get("smart_search_profile_advance_job"))
    if (not execution_id or existing.get("search_execution_id") != execution_id
            or existing.get("search_execution_job_id") != job_id
            or current_job.get("job_id", job_id) != job_id
            or current_job.get("status") not in {"queued", "already_queued", "running"}):
        return False
    merged = interrupted_lane_summary(existing, execution_id=execution_id, reason=reason)
    merged.update({
        "phase": "failed",
        "smart_search_profile_advance_job": {
            **current_job, "status": "failed", "job_id": job_id, "execution_id": execution_id,
            "reason": reason, "error": error[:1000],
            "viltrox_fit_score_untouched": True,
        },
    })
    conn.execute(
        "UPDATE vkpi_kol_search_sessions SET status='failed', result_summary_json=?::jsonb, updated_at=NOW() WHERE id=?",
        (_json_dumps(_sanitize_session_payload(merged)), int(session_id)),
    )
    return True


def write_diagnostics_patch(conn: Any, session_id: int, patch: dict[str, Any]) -> None:
    """Merge server diagnostics under the same row lock as both result lanes."""
    row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=? FOR NO KEY UPDATE",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    existing = _dict(_loads(dict(row).get("result_summary_json"), {}))
    if not permits_write(existing, session_id, resolve_fence(session_id)):
        return
    protected = {"status", "phase", "progress", "search_lanes", "returned_count", "items_count",
                 "result_projection", "result_state", "origin_breakdown", "completion"} | _PROJECTION_KEYS["local"]
    safe_patch = _sanitize_session_payload({key: value for key, value in patch.items() if key not in protected})
    conn.execute(
        "UPDATE vkpi_kol_search_sessions SET result_summary_json=?::jsonb, updated_at=NOW() WHERE id=?",
        (_json_dumps({**existing, **safe_patch}), int(session_id)),
    )
