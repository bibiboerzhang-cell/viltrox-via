from __future__ import annotations

import sqlite3

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


def test_analysis_cache_surfaces_quality_incomplete_without_calling_it_ready(monkeypatch):
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_project", lambda *_: None)
    monkeypatch.setattr(
        vkpi_projects,
        "get_analysis_cache_entry",
        lambda *_a, **_k: {
            "target_type": "video",
            "target_id": "3951",
            "derive_method": "video_analysis_final_v1",
            "status": "quality_incomplete",
            "result": {
                "quality_status": "quality_incomplete",
                "quality_issues": ["missing_brand_product_evidence"],
            },
        },
    )
    monkeypatch.setattr(
        vkpi_projects,
        "get_latest_analysis_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache entry should be authoritative")),
    )
    monkeypatch.setattr(vkpi_projects, "_resolve_video_cached_url", lambda *_: None)

    result = vkpi_projects.analysis_cache(
        target_type="video",
        target_id="3951",
        derive_method="video_analysis_final_v1",
        staff={"id": 1},
    )

    assert result["state"] == "quality_incomplete"
    assert result["entry"]["status"] == "quality_incomplete"


def test_analysis_cache_surfaces_legacy_as_terminal_without_polling_job(monkeypatch):
    entry = {
        "cache_id": 91,
        "target_type": "video",
        "target_id": "3951",
        "derive_method": "video_analysis_final_v1",
        "status": "ready",
        "result": {"status": "completed"},
    }
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_project", lambda *_: None)
    monkeypatch.setattr(vkpi_projects, "get_analysis_cache_entry", lambda *_a, **_k: entry)
    monkeypatch.setattr(
        vkpi_projects,
        "get_latest_analysis_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("legacy terminal must not poll jobs")),
    )
    monkeypatch.setattr(vkpi_projects, "_resolve_video_cached_url", lambda *_: None)

    result = vkpi_projects.analysis_cache(
        target_type="video",
        target_id="3951",
        derive_method="video_analysis_final_v1",
        staff={"id": 1},
    )

    assert result["state"] == "legacy_unverified"
    assert result["terminal"] is True
    assert result["revalidation_required"] is True
    assert result["claim_status"] == "descriptive_only"
    assert result["cache_id"] == 91
    assert result["entry"] is entry
    assert result["analysis_job"] is None
    assert 0 < len(result["reasons"]) <= 12
    assert all(len(reason) <= 120 for reason in result["reasons"])


def test_analysis_cache_projection_preserves_classifier_verified_ready(monkeypatch):
    monkeypatch.setattr(
        cache_repo,
        "canonical_final_v1_cache_reuse",
        lambda *_a, **_k: {"reusable": True, "reasons": []},
    )
    projection = cache_repo.analysis_cache_read_projection(
        {"status": "ready", "target_type": "video", "target_id": "7", "derive_method": "video_analysis_final_v1"},
    )
    assert projection == {"state": "ready", "terminal": True}


def test_final_v1_alias_target_cannot_bypass_canonical_classifier():
    projection = cache_repo.analysis_cache_read_projection(
        {
            "status": "ready",
            "target_type": "cn_platform_video",
            "target_id": "7",
            "derive_method": "video_analysis_final_v1",
        },
    )
    assert projection["state"] == "legacy_unverified"
    assert projection["terminal"] is True
    assert "unsupported_target_type" in projection["reasons"]


def test_analysis_cache_surfaces_stale_without_promoting_it_to_ready(monkeypatch):
    assert vkpi_projects._cache_entry_state({"status": "stale"}) == "stale"
    assert vkpi_projects._cache_entry_state({"status": "foreign"}) == "unknown"


@pytest.mark.parametrize(
    ("job_state", "job_updated_at", "expected_state"),
    [
        ("running", "2026-08-25T00:00:00Z", "running"),
        ("failed", "2026-08-25T02:00:00Z", "failed"),
        ("failed", "2026-08-24T23:59:59Z", "stale"),
    ],
)
def test_analysis_cache_reconciles_stale_entry_with_latest_job(
    monkeypatch,
    job_state,
    job_updated_at,
    expected_state,
):
    entry = {
        "target_type": "video",
        "target_id": "3951",
        "derive_method": "video_analysis_final_v1",
        "status": "stale",
        "updated_at": "2026-08-25T01:00:00Z",
    }
    job = {
        "id": 18428,
        "status": job_state,
        "state": job_state,
        "updated_at": job_updated_at,
    }
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_project", lambda *_: None)
    monkeypatch.setattr(vkpi_projects, "get_analysis_cache_entry", lambda *_a, **_k: entry)
    monkeypatch.setattr(vkpi_projects, "get_latest_analysis_job", lambda *_a, **_k: job)
    monkeypatch.setattr(vkpi_projects, "_resolve_video_cached_url", lambda *_: None)

    result = vkpi_projects.analysis_cache(
        target_type="video",
        target_id="3951",
        derive_method="video_analysis_final_v1",
        staff={"id": 1},
    )

    assert result["state"] == expected_state
    assert result["entry"] is entry
    assert result["analysis_job"] is job


def test_video_quality_triage_namespace_is_fallback_only_and_preserves_ready() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_analysis_cache (
          id INTEGER PRIMARY KEY,
          target_type TEXT NOT NULL,
          target_id TEXT NOT NULL,
          derive_method TEXT NOT NULL,
          model TEXT,
          cost NUMERIC,
          status TEXT NOT NULL,
          triggered_by_user_id INTEGER,
          result TEXT,
          created_at TEXT,
          updated_at TEXT,
          prompt_version TEXT,
          model_family TEXT
        );
        INSERT INTO vkpi_analysis_cache VALUES
          (1, 'video_quality_triage', '3951', 'video_analysis_final_v1',
           'gemini-3.6-flash', 0.01, 'quality_incomplete', NULL,
           '{"quality_status":"quality_incomplete"}', '2026-08-25',
           '2026-08-25T01:00:00Z', 'v2', 'gemini-3.6');
        """
    )

    fallback = cache_repo.get_analysis_cache_entry(
        "video",
        "3951",
        derive_method="video_analysis_final_v1",
        conn=conn,
    )
    assert fallback is not None
    assert fallback["target_type"] == "video_quality_triage"
    assert fallback["status"] == "quality_incomplete"
    # a05 and older readers query only target_type='video'; a code-only
    # rollback therefore cannot reinterpret the triage row as ready.
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_analysis_cache "
        "WHERE target_type='video' AND target_id='3951'"
    ).fetchone()[0] == 0

    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache VALUES
          (2, 'video', '3951', 'video_analysis_final_v1',
           'gemini-3.5-flash-lite', 0.02, 'ready', NULL,
           '{"status":"completed"}', '2026-08-24',
           '2026-08-24T01:00:00Z', 'v1', 'gemini-3.5')
        """
    )
    authoritative = cache_repo.get_analysis_cache_entry(
        "video",
        "3951",
        derive_method="video_analysis_final_v1",
        conn=conn,
    )
    assert authoritative is not None
    assert authoritative["target_type"] == "video"
    assert authoritative["status"] == "ready"

    batch = cache_repo.get_analysis_cache_entries_for_targets(
        "video",
        ["3951"],
        derive_methods=("video_analysis_final_v1",),
        conn=conn,
    )
    assert batch[("3951", "video_analysis_final_v1")]["status"] == "ready"
    conn.close()


def test_project_item_state_keeps_incomplete_cache_out_of_ready_count() -> None:
    state, reason = cache_repo._project_video_item_state(
        derive_method="video_analysis_final_v1",
        platform="youtube",
        entry={"status": "quality_incomplete"},
        job={"status": "triage"},
    )

    assert state == "quality_incomplete"
    assert reason == "final_v1_quality_incomplete"


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
    assert [item["state"] for item in result["items"]] == ["legacy_unverified", "running", "not_requested"]
    assert result["items"][0]["terminal"] is True
    assert result["items"][0]["revalidation_required"] is True
    assert result["items"][0]["claim_status"] == "descriptive_only"


@pytest.mark.parametrize(
    ("job_state", "job_updated_at", "expected_state"),
    [
        ("running", "2026-08-25T00:00:00Z", "running"),
        ("failed", "2026-08-25T02:00:00Z", "failed"),
        ("failed", "2026-08-24T23:59:59Z", "stale"),
    ],
)
def test_analysis_cache_batch_reconciles_stale_entry_with_latest_job(
    monkeypatch,
    job_state,
    job_updated_at,
    expected_state,
):
    entry = {
        "target_id": "10",
        "derive_method": "video_analysis_final_v1",
        "status": "stale",
        "updated_at": "2026-08-25T01:00:00Z",
    }
    job = {
        "id": 7,
        "state": job_state,
        "status": job_state,
        "updated_at": job_updated_at,
    }
    monkeypatch.setattr(vkpi_projects.scope, "resolve_analysis_target_projects", lambda *_: set())
    monkeypatch.setattr(vkpi_projects.policy, "require_projects_read", lambda *_: None)
    monkeypatch.setattr(
        vkpi_projects,
        "get_analysis_cache_entries_for_targets",
        lambda *_a, **_k: {("10", "video_analysis_final_v1"): entry},
    )
    observed: list[list[str]] = []

    def jobs(_target_type, target_ids, *, derive_method):
        observed.append(list(target_ids))
        assert derive_method == "video_analysis_final_v1"
        return {"10": job}

    monkeypatch.setattr(vkpi_projects, "get_latest_analysis_jobs_for_targets", jobs)

    result = vkpi_projects.analysis_cache_batch(
        body={
            "target_type": "video",
            "target_ids": ["10"],
            "derive_method": "video_analysis_final_v1",
        },
        staff={"id": 1},
    )

    assert observed == [["10"]]
    assert result["items"][0]["state"] == expected_state
    assert result["items"][0]["entry"] is entry
    assert result["items"][0]["analysis_job"] is job
