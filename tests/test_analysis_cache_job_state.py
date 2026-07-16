from __future__ import annotations

import pytest

from app.api.routers import vkpi_projects_analysis as vkpi_projects
from app.domains.analysis import cache_repo


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.params = ()

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return _Result(self.row)


def test_latest_analysis_job_maps_blocked_json_to_safe_display_fields():
    conn = _Conn(
        {
            "id": 18428,
            "status": "blocked",
            "last_error_category": "blocked",
            "last_error": (
                '{"reason":"budget_guard_blocked","provider":"google",'
                '"stage":"video_analysis_final_v1","reason_detail":"model_binding_blocked",'
                '"secret":"must-not-leak"}'
            ),
            "created_at": "2026-07-15T00:33:53Z",
            "started_at": None,
            "updated_at": "2026-07-15T00:33:53Z",
        }
    )

    job = cache_repo.get_latest_analysis_job(
        "video", "3951", derive_method="video_analysis_final_v1", conn=conn
    )

    assert job["id"] == 18428
    assert job["status"] == "blocked"
    assert job["state"] == "blocked"
    assert job["error_category"] == "blocked"
    assert job["reason"] == "model_binding_blocked"
    assert job["reason_detail"] == "model_binding_blocked"
    assert job["failure"]["code"] == "model_binding_blocked"
    assert job["failure"]["category"] == "model_binding"
    assert job["provider"] == "google"
    assert job["stage"] == "video_analysis_final_v1"
    assert job["created_at"] == "2026-07-15T00:33:53Z"
    assert job["started_at"] is None
    assert job["updated_at"] == "2026-07-15T00:33:53Z"
    assert conn.params == ("video", "3951", "video_analysis_final_v1")
    assert "secret" not in str(job)


@pytest.mark.parametrize(
    ("reason_detail", "expected_reason", "expected_category"),
    [
        ("readiness_not_production_ready", "readiness_not_production_ready", "readiness"),
        ("model_binding_blocked", "model_binding_blocked", "model_binding"),
        ("budget_blocked", "budget_blocked", "budget"),
    ],
)
def test_safe_worker_error_keeps_distinct_ui_gate_reason(
    reason_detail,
    expected_reason,
    expected_category,
):
    result = cache_repo._safe_job_error(
        '{"reason":"budget_guard_blocked","reason_detail":"'
        + reason_detail
        + '"}'
    )

    assert result["reason"] == expected_reason
    assert result["failure"]["code"] == expected_reason
    assert result["failure"]["category"] == expected_category


def test_analysis_cache_surfaces_terminal_job_instead_of_pending(monkeypatch):
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_project", lambda *_: None)
    monkeypatch.setattr(vkpi_projects, "get_analysis_cache_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(
        vkpi_projects,
        "get_latest_analysis_job",
        lambda *_a, **_k: {
            "id": 18428,
            "status": "blocked",
            "state": "blocked",
            "reason": "budget_guard_blocked",
            "reason_detail": "model_binding_blocked",
        },
    )
    monkeypatch.setattr(vkpi_projects, "_resolve_video_cached_url", lambda *_: None)

    result = vkpi_projects.analysis_cache(
        target_type="video",
        target_id="3951",
        derive_method="video_analysis_final_v1",
        staff={"id": 1},
    )

    assert result["state"] == "blocked"
    assert result["analysis_job"]["reason_detail"] == "model_binding_blocked"


def test_analysis_cache_without_cache_or_job_is_not_requested(monkeypatch):
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_project", lambda *_: None)
    monkeypatch.setattr(vkpi_projects, "get_analysis_cache_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(vkpi_projects, "get_latest_analysis_job", lambda *_a, **_k: None)
    monkeypatch.setattr(vkpi_projects, "_resolve_video_cached_url", lambda *_: None)

    result = vkpi_projects.analysis_cache(
        target_type="video",
        target_id="3951",
        derive_method="video_analysis_final_v1",
        staff={"id": 1},
    )

    assert result["state"] == "not_requested"
    assert result["entry"] is None
    assert result["analysis_job"] is None


@pytest.mark.parametrize("job_state", ["queued", "running", "blocked", "failed"])
def test_analysis_cache_preserves_real_job_state(monkeypatch, job_state):
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_project", lambda *_: None)
    monkeypatch.setattr(vkpi_projects, "get_analysis_cache_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(
        vkpi_projects,
        "get_latest_analysis_job",
        lambda *_a, **_k: {"id": 18428, "status": job_state, "state": job_state},
    )
    monkeypatch.setattr(vkpi_projects, "_resolve_video_cached_url", lambda *_: None)

    result = vkpi_projects.analysis_cache(
        target_type="video",
        target_id="3951",
        derive_method="video_analysis_final_v1",
        staff={"id": 1},
    )

    assert result["state"] == job_state
    assert result["analysis_job"]["status"] == job_state


def test_analysis_cache_strict_production_poll_is_not_masked_by_local_evaluation(monkeypatch):
    observed_fallback_flags = []

    def cache_entry(*_args, **kwargs):
        allow_fallback = kwargs.get("allow_local_evaluation_fallback")
        observed_fallback_flags.append(allow_fallback)
        if allow_fallback:
            return {
                "derive_method": "video_analysis_final_v1_local_evaluation",
                "evaluation_only": True,
            }
        return None

    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_project", lambda *_: None)
    monkeypatch.setattr(vkpi_projects, "get_analysis_cache_entry", cache_entry)
    monkeypatch.setattr(
        vkpi_projects,
        "get_latest_analysis_job",
        lambda *_a, **_k: {"id": 18429, "status": "running", "state": "running"},
    )
    monkeypatch.setattr(vkpi_projects, "_resolve_video_cached_url", lambda *_: None)

    result = vkpi_projects.analysis_cache(
        target_type="video",
        target_id="3951",
        derive_method="video_analysis_final_v1",
        allow_local_evaluation_fallback=False,
        staff={"id": 1},
    )

    assert observed_fallback_flags == [False]
    assert result["entry"] is None
    assert result["state"] == "running"
    assert result["analysis_job"]["id"] == 18429


def test_analysis_cache_batch_preserves_order_and_uses_bounded_reads(monkeypatch):
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_projects", lambda *_: set())
    observed_project_sets: list[set[int]] = []
    monkeypatch.setattr(
        vkpi_projects.policy,
        "require_projects_read",
        lambda project_ids, _staff: observed_project_sets.append(set(project_ids)),
    )
    monkeypatch.setattr(
        vkpi_projects,
        "get_analysis_cache_entries_for_targets",
        lambda *_a, **_k: {
            ("10", "video_analysis_final_v1"): {
                "target_id": "10",
                "derive_method": "video_analysis_final_v1",
                "status": "ready",
            }
        },
    )
    observed: list[list[str]] = []

    def jobs(_target_type, target_ids, *, derive_method):
        observed.append(list(target_ids))
        assert derive_method == "video_analysis_final_v1"
        return {"11": {"id": 7, "state": "running", "status": "running"}}

    monkeypatch.setattr(vkpi_projects, "get_latest_analysis_jobs_for_targets", jobs)

    result = vkpi_projects.analysis_cache_batch(
        body={
            "target_type": "video",
            "target_ids": ["10", "11", "12", "10"],
            "derive_method": "video_analysis_final_v1",
        },
        staff={"id": 1},
    )

    assert observed == [["11", "12"]]
    assert observed_project_sets == [set()]
    assert [item["target_id"] for item in result["items"]] == ["10", "11", "12"]
    assert [item["state"] for item in result["items"]] == ["ready", "running", "not_requested"]
