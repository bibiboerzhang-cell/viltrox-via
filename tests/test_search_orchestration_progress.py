"""Offline read-time interruption semantics; no DB, queue or provider calls."""
from copy import deepcopy

import pytest

from app.domains.kol.search_progress_contract import project_search_progress
from app.domains.kol.search_execution_observation import confirmed_execution_failure


def interrupted_session():
    return {"id": 81, "status": "failed", "result_summary": {
        "phase": "failed", "search_execution_id": "attempt-one", "search_execution_job_id": 17,
        "smart_search_profile_advance_job": {"job_id": 17, "execution_id": "attempt-one",
                                              "status": "failed", "reason": "search_pipeline_cancelled"},
        "search_lanes": {"local": {"status": "ready", "returned_count": 1},
                         "online": {"status": "failed", "returned_count": None}},
        "provider_outcome": "unknown",
    }}


def candidate(profile_status=None):
    payload = {} if profile_status is None else {"profile_advance_job": {"job_id": 23, "status": profile_status}}
    return {"id": 91, "item_type": "recall_candidate", "status": "ready", "kol_pool_id": 91, "payload": payload}


@pytest.mark.parametrize("old_complete", [False, True])
def test_current_interruption_wins_over_old_completion_without_inventing_counts(old_complete):
    session = interrupted_session()
    session["result_summary"]["progress"] = {"phase": "complete" if old_complete else "running",
        "requested_tasks_terminal": old_complete, "full_analysis_complete": old_complete}
    before = deepcopy(session)
    result = project_search_progress(session, [candidate()])
    assert result["state"] == "failed"
    assert result["orchestration_interrupted"] is True
    assert result["observation_terminal"] is True
    assert result["interruption_reason"] == "search_pipeline_cancelled"
    assert result["completion_kind"] == "orchestration_interrupted"
    assert result["requested_units"] == result["successful_units"] == 1
    assert result["requested_tasks_terminal"] is True
    assert result["stages"]["search"]["data_ready"] == 1
    assert "provider_calls" not in result and session == before


def test_observation_stops_without_claiming_registered_children_finished_then_keeps_late_results():
    session = interrupted_session()
    active = project_search_progress(session, [candidate("running")])
    assert active["state"] == "failed" and active["observation_terminal"] is True
    assert active["running_units"] == 1 and active["requested_tasks_terminal"] is False
    finished = project_search_progress(session, [candidate("ready")])
    assert finished["state"] == "failed" and finished["observation_terminal"] is True
    assert finished["running_units"] == 0 and finished["requested_tasks_terminal"] is True
    assert finished["stages"]["profile"]["successful"] == 1


@pytest.mark.parametrize("patch", [
    {"search_execution_id": "replacement"}, {"search_execution_job_id": 18},
    {"search_execution_job_id": None},
    {"smart_search_profile_advance_job": {"job_id": 17, "execution_id": "attempt-one", "status": "ready", "reason": "search_pipeline_cancelled"}},
    {"smart_search_profile_advance_job": {"job_id": 17, "execution_id": "attempt-one", "status": "failed", "reason": "unknown_reason"}},
])
def test_stale_or_incomplete_failure_does_not_override_real_active_child(patch):
    session = interrupted_session()
    session["result_summary"].update(patch)
    result = project_search_progress(session, [candidate("running")])
    assert result["state"] == "running"
    assert result.get("orchestration_interrupted") is not True
    assert result.get("observation_terminal") is not True
    assert result["requested_tasks_terminal"] is False


@pytest.mark.parametrize("value", [None, [], {}, True, 17])
def test_malformed_failure_reason_is_unverified_not_an_exception(value):
    summary = interrupted_session()["result_summary"]
    summary["smart_search_profile_advance_job"]["reason"] = value
    assert confirmed_execution_failure(summary) is None
