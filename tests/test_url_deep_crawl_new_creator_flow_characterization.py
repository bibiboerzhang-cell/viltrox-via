"""Characterization tests locking _execute_new_creator_video_flow behavior.

Written BEFORE the CC-57 function was decomposed. They pin, with fully mocked
collaborators:
- the exact response dict for every top-level path (creator_unresolved /
  official_channel / happy path / crawl failure / generic exception /
  already_analyzed followup);
- the byte-level kwargs handed to _enqueue_final_v1_video_analysis;
- the summary payload written via _record_deep_crawl_run;
- checkpoint call counts (authorization gate sites) and rollback behavior.

The refactor must keep every assertion green without edits.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.kol import url_deep_crawl_execute as execute
from app.domains.kol import url_deep_crawl_execute_video_flows as flows
from app.domains.kol import video_url_resolver
from app.domains.kol.provider_job_access import ProviderJobAccessError

VIDEO_URL = "https://www.youtube.com/watch?v=abcdefghijk"
PROFILE_URL = "https://www.youtube.com/@creator"


class _Conn:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


def _classified() -> SimpleNamespace:
    # platform youtube => _cache_video_flow_url returns (None, False) without provider calls.
    return SimpleNamespace(normalized_url=VIDEO_URL, platform="youtube", video_id="abcdefghijk")


def _video_flow() -> dict[str, Any]:
    return {
        "creator_identity": {"handle": "creator", "platform": "youtube"},
        "video_metadata": {"title": "demo", "content_url": VIDEO_URL},
    }


def _body(**overrides: Any) -> dict[str, Any]:
    body = {
        "max_posts": 2,
        "search_session_id": "sess-1",
        "search_session_item_id": "item-9",
        "parent_job_id": 7,
        "local_evaluation": True,
        "provider_parent_payload": {"parent": 1},
        "paid_action_staff": {"id": 3},
        "enforce_target_write": True,
        "history_video_limit": 5,
    }
    body.update(overrides)
    return body


class _Recorder:
    def __init__(self) -> None:
        self.checkpoints = 0
        self.run_kwargs: list[dict[str, Any]] = []
        self.enqueue_kwargs: list[dict[str, Any]] = []
        self.dossier_kwargs: list[dict[str, Any]] = []
        self.rep_kwargs: list[dict[str, Any]] = []
        self.hist_kwargs: list[dict[str, Any]] = []

    def checkpoint(self) -> None:
        self.checkpoints += 1


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    conn = _Conn()
    rec = _Recorder()
    profile_classified = SimpleNamespace(normalized_url=PROFILE_URL, platform="youtube")

    def record_run(_conn: Any, **kwargs: Any) -> int:
        rec.run_kwargs.append(kwargs)
        return 77

    def enqueue_final_v1(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.enqueue_kwargs.append(kwargs)
        return {
            "status": "queued",
            "ai_analysis": {"state": "queued", "reason": "", "provider_calls_allowed": True},
            "viltrox_fit_score_changed_ids": [],
        }

    def dossier(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.dossier_kwargs.append(kwargs)
        return {"status": "queued", "job_id": 5}

    def rep_analysis(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.rep_kwargs.append(kwargs)
        return {"worker_touched": False, "queued": 0}

    def hist_evidence(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.hist_kwargs.append(kwargs)
        return {"materialized": 2, "reused": 0}

    monkeypatch.setattr(flows, "get_conn", lambda: conn)
    monkeypatch.setattr(execute, "_profile_classified_from_video_flow", lambda *_a, **_k: profile_classified)
    monkeypatch.setattr(execute, "_profile_target", lambda *_a, **_k: {"platform": "youtube", "handle": "creator"})
    monkeypatch.setattr(execute, "_crawl_profile_basics", lambda *_a, **_k: {"status": "ok", "provider_source": "mock"})
    monkeypatch.setattr(
        execute,
        "_profile_data_for_new_video_creator",
        lambda *_a, **_k: {"platform": "youtube", "handle": "creator", "followers": 10},
    )
    monkeypatch.setattr(execute, "_profile_incremental_state", lambda *_a, **_k: {"marker": "fresh"})
    monkeypatch.setattr(execute, "_record_deep_crawl_run", record_run)
    monkeypatch.setattr(execute, "_enqueue_account_dossier_extract_followup", dossier)
    monkeypatch.setattr(video_url_resolver, "find_official_channel_match", lambda *_a: None)
    monkeypatch.setattr(flows, "write_kol_profile_basics", lambda *_a, **_k: {"ok": True, "kol_pool_id": 42, "operation": "insert"})
    monkeypatch.setattr(
        flows,
        "ensure_video_evidence_from_url",
        lambda *_a, **_k: {"ok": True, "evidence_id": 9, "status": "created", "kol_pool_id": 42},
    )
    monkeypatch.setattr(flows, "_enqueue_final_v1_video_analysis", enqueue_final_v1)
    monkeypatch.setattr(flows, "_execute_profile_representative_video_analysis", rep_analysis)
    monkeypatch.setattr(flows, "_execute_profile_history_video_evidence", hist_evidence)
    return {"conn": conn, "rec": rec, "profile_classified": profile_classified}


def _run(rec: _Recorder, body: dict[str, Any] | None = None, video_flow: dict[str, Any] | None = None) -> dict[str, Any]:
    return flows._execute_new_creator_video_flow(
        _classified(),
        video_flow if video_flow is not None else _video_flow(),
        body if body is not None else _body(),
        authorization_checkpoint=rec.checkpoint,
    )


def _strip_elapsed(data: dict[str, Any]) -> int:
    elapsed = data.pop("elapsed_ms")
    assert isinstance(elapsed, int) and elapsed >= 0
    return elapsed


def test_happy_path_full_response_and_enqueue_payload(harness: dict[str, Any]) -> None:
    rec: _Recorder = harness["rec"]
    result = _run(rec)
    _strip_elapsed(result)

    assert result == {
        "creator_identity": {"handle": "creator", "platform": "youtube"},
        "video_metadata": {"title": "demo", "content_url": VIDEO_URL},
        "status": "queued",
        "operation": "new_creator_video_analysis",
        "kol_pool_id": 42,
        "evidence_id": 9,
        "profile_flow": {
            "status": "ready",
            "operation": "insert",
            "kol_pool_id": 42,
            "target": {"platform": "youtube", "handle": "creator"},
            "profile_data": {"platform": "youtube", "handle": "creator", "followers": 10},
            "write_result": {
                "ok": True,
                "operation": "insert",
                "kol_pool_id": 42,
                "fields_written": None,
                "ignored_fields": None,
                "missing_columns": None,
                "viltrox_fit_score_changed_ids": [],
                "viltrox_fit_score_untouched": None,
                "method": None,
            },
            "crawl_status": "ok",
            "provider_source": "mock",
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        },
        "evidence_result": {
            "ok": True,
            "status": "created",
            "operation": None,
            "kol_pool_id": 42,
            "evidence_id": 9,
            "source_url": None,
            "fields_written": None,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": None,
            "method": None,
        },
        "enqueue_result": {
            "status": "queued",
            "kol_pool_id": None,
            "evidence_id": None,
            "derive_method": None,
            "provider_calls": None,
            "write_db": None,
            "viltrox_fit_score_changed_ids": [],
            "ai_analysis": {"state": "queued", "reason": "", "provider_calls_allowed": True},
        },
        "ai_analysis": {"state": "queued", "reason": "", "provider_calls_allowed": True},
        "representative_video_analysis": {"worker_touched": False, "queued": 0},
        "history_video_evidence": {"materialized": 2, "reused": 0},
        "account_dossier_extract_job": None,
        "run_id": 77,
        "run_status": "ready",
        "error": None,
        "crawl_performed": True,
        "business_tables_written": True,
        "worker_touched": True,
        "write_db": True,
        "writes": ["vkpi_kol_pool", "vkpi_kol_video_evidence", "apify_jobs", "vkpi_kol_url_deep_crawl_runs"],
        "cached_video_url": None,
        "provider_calls_performed": False,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }

    # enqueue payload must stay byte-equivalent
    assert rec.enqueue_kwargs == [
        {
            "kol_pool_id": 42,
            "evidence_id": 9,
            "source": "kol_url_deep_crawl",
            "batch": "url_new_creator",
            "commit": True,
            "search_session_id": "sess-1",
            "search_session_item_id": "item-9",
            "parent_job_id": 7,
            "local_evaluation": True,
            "provider_parent_payload": {"parent": 1},
            "staff": {"id": 3},
            "enforce_target_write": True,
        }
    ]

    # followup onboarding calls received the augmented body and shared incremental state
    assert len(rec.rep_kwargs) == 1 and len(rec.hist_kwargs) == 1
    onboarding_body = rec.rep_kwargs[0]["body"]
    assert onboarding_body["mode"] == "account_deep"
    assert onboarding_body["representative_video_limit"] == 3
    assert onboarding_body["history_video_limit"] == 5
    assert onboarding_body["materialize_history_videos"] is True
    assert onboarding_body["exclude_video_urls"] == [VIDEO_URL]
    assert rec.rep_kwargs[0]["incremental_state"] == {"marker": "fresh"}
    assert rec.hist_kwargs[0]["body"] is onboarding_body or rec.hist_kwargs[0]["body"] == onboarding_body

    # run summary written verbatim
    assert len(rec.run_kwargs) == 1
    run = rec.run_kwargs[0]
    summary = dict(run["summary"])
    _strip_elapsed(summary)
    assert run["kol_pool_id"] == 42
    assert run["source_url"] == VIDEO_URL
    assert run["url_type"] == "video"
    assert run["mode"] == "video_deep"
    assert run["status"] == "ready"
    assert run["dry_run"] is False
    assert summary == {
        "operation": "new_creator_video_analysis",
        "status": "queued",
        "error": None,
        "creator_identity": {"handle": "creator", "platform": "youtube"},
        "profile_url": PROFILE_URL,
        "profile_crawl_status": "ok",
        "profile_provider_source": "mock",
        "profile_write_result": {
            "ok": True,
            "operation": "insert",
            "kol_pool_id": 42,
            "fields_written": None,
            "ignored_fields": None,
            "missing_columns": None,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": None,
            "method": None,
        },
        "profile_data": {"platform": "youtube", "handle": "creator", "followers": 10},
        "video_metadata": {"title": "demo", "content_url": VIDEO_URL},
        "evidence_result": {
            "ok": True,
            "status": "created",
            "operation": None,
            "kol_pool_id": 42,
            "evidence_id": 9,
            "source_url": None,
            "fields_written": None,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": None,
            "method": None,
        },
        "enqueue_result": {
            "status": "queued",
            "kol_pool_id": None,
            "evidence_id": None,
            "derive_method": None,
            "provider_calls": None,
            "write_db": None,
            "viltrox_fit_score_changed_ids": [],
            "ai_analysis": {"state": "queued", "reason": "", "provider_calls_allowed": True},
        },
        "representative_video_analysis": {"worker_touched": False, "queued": 0},
        "history_video_evidence": {"materialized": 2, "reused": 0},
        "account_dossier_extract_job": None,
        "viltrox_fit_score_changed_ids": [],
    }

    # checkpoint sites: try-top, pre-pool-write, pre-evidence, pre-enqueue,
    # pre-followups, pre-cache, pre-record => 7
    assert rec.checkpoints == 7
    assert harness["conn"].rollbacks == 0
    assert rec.dossier_kwargs == []


def test_creator_unresolved_records_failed_run(harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    rec: _Recorder = harness["rec"]
    monkeypatch.setattr(execute, "_profile_classified_from_video_flow", lambda *_a, **_k: None)
    result = _run(rec)
    _strip_elapsed(result)
    assert result == {
        "creator_identity": {"handle": "creator", "platform": "youtube"},
        "video_metadata": {"title": "demo", "content_url": VIDEO_URL},
        "status": "creator_unresolved",
        "operation": "new_creator_video_analysis",
        "message": "video creator could not be converted into a profile identity; refused to create an anonymous KOL.",
        "run_id": 77,
        "business_tables_written": True,
        "worker_touched": False,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }
    run = rec.run_kwargs[0]
    assert run["status"] == "failed"
    assert run["kol_pool_id"] is None
    assert run["summary"] == {
        "operation": "new_creator_video_analysis",
        "status": "creator_unresolved",
        "reason": "resolved video creator lacks a usable profile identity",
        "creator_identity": {"handle": "creator", "platform": "youtube"},
        "video_metadata": {"title": "demo", "content_url": VIDEO_URL},
        "viltrox_fit_score_changed_ids": [],
    }
    assert rec.checkpoints == 1
    assert rec.enqueue_kwargs == []


def test_official_channel_gate_skips_enrollment(harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    rec: _Recorder = harness["rec"]
    seen: list[Any] = []

    def official(identity: Any) -> dict[str, Any]:
        seen.append(identity)
        return {"channel_id": "official-1"}

    monkeypatch.setattr(video_url_resolver, "find_official_channel_match", official)
    result = _run(rec)
    _strip_elapsed(result)
    assert result == {
        "creator_identity": {"handle": "creator", "platform": "youtube"},
        "video_metadata": {"title": "demo", "content_url": VIDEO_URL},
        "status": "official_channel_video",
        "operation": "new_creator_video_analysis",
        "message": "官方自有账号的视频：不建人选档案，也不做深度分析，仅保留视频基础数据。",
        "official_channel": {"channel_id": "official-1"},
        "ai_analysis": {
            "state": "skipped",
            "reason": "official_channel_video",
            "provider_calls_allowed": False,
        },
        "run_id": 77,
        "business_tables_written": True,
        "worker_touched": False,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }
    # the identity dict (not e.g. a stringified form) reaches the matcher
    assert seen == [{"handle": "creator", "platform": "youtube"}]
    run = rec.run_kwargs[0]
    assert run["status"] == "ready"
    assert run["summary"]["status"] == "official_channel_video"
    assert run["summary"]["official_channel"] == {"channel_id": "official-1"}
    assert rec.checkpoints == 1


def test_official_channel_matcher_gets_empty_dict_for_non_dict_identity(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec: _Recorder = harness["rec"]
    seen: list[Any] = []
    monkeypatch.setattr(video_url_resolver, "find_official_channel_match", lambda ident: seen.append(ident))
    _run(rec, video_flow={"creator_identity": "not-a-dict", "video_metadata": None})
    assert seen == [{}]


def test_crawl_failure_short_circuits_pipeline(harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    rec: _Recorder = harness["rec"]
    monkeypatch.setattr(execute, "_crawl_profile_basics", lambda *_a, **_k: {"status": "error", "provider_source": "mock"})
    result = _run(rec)
    assert result["status"] == "profile_crawl_failed"
    assert result["error"] == "profile_crawl_not_ready"
    assert result["run_status"] == "failed"
    assert result["kol_pool_id"] is None
    assert result["evidence_id"] is None
    assert result["crawl_performed"] is True
    assert result["business_tables_written"] is True  # run row written
    assert result["worker_touched"] is False
    assert result["profile_flow"]["status"] == "profile_crawl_failed"
    assert result["profile_flow"]["write_result"] == {}
    assert result["evidence_result"] == {}
    assert result["enqueue_result"] == {}
    assert result["ai_analysis"] == {
        "state": "not_requested",
        "reason": "analysis_not_requested",
        "gate_reason": "",
        "model_readiness_status": "not_ready",
        "provider_calls_allowed": False,
    }
    assert rec.enqueue_kwargs == []
    assert rec.rep_kwargs == []
    # checkpoints: try-top, pre-cache, pre-record => 3
    assert rec.checkpoints == 3


def test_generic_exception_rolls_back_and_reports_failed(harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    rec: _Recorder = harness["rec"]

    def bomb(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("pool write exploded")

    monkeypatch.setattr(flows, "write_kol_profile_basics", bomb)
    result = _run(rec)
    assert result["status"] == "failed"
    assert result["error"] == "video_creator_flow_failed"
    assert result["run_status"] == "failed"
    assert harness["conn"].rollbacks == 1
    run = rec.run_kwargs[0]
    assert run["status"] == "failed"
    assert run["summary"]["error"] == "video_creator_flow_failed"
    # crawl happened before the bomb, so partial state must survive into the summary
    assert run["summary"]["profile_crawl_status"] == "ok"


def test_provider_access_error_rolls_back_and_reraises(harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    rec: _Recorder = harness["rec"]

    def bomb(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise ProviderJobAccessError("provider_job_actor_inactive", 403)

    monkeypatch.setattr(flows, "write_kol_profile_basics", bomb)
    with pytest.raises(ProviderJobAccessError):
        _run(rec)
    assert harness["conn"].rollbacks == 1
    assert rec.run_kwargs == []  # re-raise happens before the run record


def test_already_analyzed_enqueues_dossier_followup(harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    rec: _Recorder = harness["rec"]

    def enqueue_already(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.enqueue_kwargs.append(kwargs)
        return {"status": "already_analyzed"}

    monkeypatch.setattr(flows, "_enqueue_final_v1_video_analysis", enqueue_already)
    result = _run(rec)
    assert result["status"] == "already_analyzed"
    assert result["run_status"] == "ready"
    assert result["account_dossier_extract_job"] == {"status": "queued", "job_id": 5}
    assert result["worker_touched"] is True  # via queued dossier job
    assert rec.dossier_kwargs == [
        {
            "kol_pool_id": 42,
            "source": "kol_url_video_new_creator_flow",
            "trigger": "video_already_analyzed",
            "source_url": VIDEO_URL,
            "query_text": "new creator account dossier - kol_pool #42",
        }
    ]
    # 7 happy-path checkpoints + 1 pre-dossier
    assert rec.checkpoints == 8


def test_skip_profile_video_followups_suppresses_onboarding_and_dossier(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec: _Recorder = harness["rec"]

    def enqueue_already(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.enqueue_kwargs.append(kwargs)
        return {"status": "already_analyzed"}

    monkeypatch.setattr(flows, "_enqueue_final_v1_video_analysis", enqueue_already)
    result = _run(rec, body=_body(skip_profile_video_followups=True))
    assert result["status"] == "already_analyzed"
    assert result["account_dossier_extract_job"] is None
    assert result["representative_video_analysis"] == {}
    assert result["history_video_evidence"] == {}
    assert rec.rep_kwargs == []
    assert rec.hist_kwargs == []
    assert rec.dossier_kwargs == []
    # try-top, pre-pool-write, pre-evidence, pre-enqueue, pre-cache, pre-record => 6
    assert rec.checkpoints == 6


def test_no_checkpoint_argument_runs_clean(harness: dict[str, Any]) -> None:
    result = flows._execute_new_creator_video_flow(_classified(), _video_flow(), _body())
    assert result["status"] == "queued"
    assert result["run_id"] == 77


def test_representative_worker_touched_rescues_run_status(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec: _Recorder = harness["rec"]

    def enqueue_failed(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.enqueue_kwargs.append(kwargs)
        return {"status": "enqueue_failed"}

    def rep_touched(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        rec.rep_kwargs.append(kwargs)
        return {"worker_touched": True}

    monkeypatch.setattr(flows, "_enqueue_final_v1_video_analysis", enqueue_failed)
    monkeypatch.setattr(flows, "_execute_profile_representative_video_analysis", rep_touched)
    result = _run(rec)
    assert result["status"] == "enqueue_failed"
    assert result["run_status"] == "ready"  # rescued by representative worker touch
    assert result["worker_touched"] is True
