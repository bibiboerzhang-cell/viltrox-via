"""Keyframe QA must reuse ready final_v1 and never regenerate the video pass."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from typing import Any

import pytest

from app.domains.kol import video_keyframe_qa_enqueue as enqueue
from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError
from app.domains.kol.video_keyframe_qa_cache import qa_cache_matches_source


YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


class _Rows:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.row) if self.row is not None else None


class _Conn:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        compact = " ".join(str(sql).split())
        if "SELECT viltrox_fit_score FROM vkpi_kol_pool" in compact:
            return _Rows({"viltrox_fit_score": 91})
        raise AssertionError(compact)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _WorkerCacheCursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def __enter__(self) -> "_WorkerCacheCursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        compact = " ".join(str(sql).split())
        assert "WITH latest_source AS" in compact
        assert params[0] == params[1] == "701"
        assert params[2] == enqueue.KEYFRAME_QA_DERIVE_METHOD

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.row) if self.row is not None else None


class _WorkerCacheConn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def cursor(self, *, row_factory: Any = None) -> _WorkerCacheCursor:
        assert row_factory is not None
        return _WorkerCacheCursor(self.row)


def _evidence(url: str = YOUTUBE_URL) -> dict[str, Any]:
    return {
        "evidence_id": 701,
        "kol_pool_id": 88,
        "content_url": url,
        "evidence_platform": "youtube",
        "title": "demo",
        "is_active": True,
        "evidence_type": "video",
        "kol_handle": "creator",
    }


def _source() -> dict[str, Any]:
    return {
        "id": 1701,
        "target_id": "701",
        "model": "gemini-3.6-flash",
        "prompt_version": "final_v1_pure_video_evidence_v2",
        "updated_at": "2026-08-24T00:00:00Z",
        "payload_sha256": "a" * 64,
    }


def _prepare_enqueue(monkeypatch: pytest.MonkeyPatch, *, url: str = YOUTUBE_URL, source: dict[str, Any] | None = None) -> _Conn:
    conn = _Conn()
    monkeypatch.setattr(enqueue, "_load_owned_evidence", lambda *_args, **_kwargs: _evidence(url))
    monkeypatch.setattr(enqueue, "_ready_qa_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(enqueue, "_ready_final_v1_source", lambda *_args, **_kwargs: _source() if source is None else source)
    monkeypatch.setattr(enqueue, "_active_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        enqueue,
        "build_target_fence",
        lambda *_args, **_kwargs: {
            "version": 1,
            "action": "video_analysis",
            "kol_pool_id": 88,
            "staff_id": 12,
            "user_id": 34,
            "evidence": [{"evidence_id": 701}],
        },
    )
    monkeypatch.setattr(
        enqueue,
        "_qa_budget_preflight",
        lambda *_args, **_kwargs: {
            "model_readiness_status": "production_ready",
            "providers": [
                {
                    "provider": "google",
                    "binding": f"google/{enqueue.KEYFRAME_QA_MODEL}",
                    "model": enqueue.KEYFRAME_QA_MODEL,
                    "provider_calls_allowed": True,
                    "estimated_cost_usd": 0.01,
                }
            ],
        },
    )
    return conn


def test_enqueue_only_writes_fenced_keyframe_job(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _prepare_enqueue(monkeypatch)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        enqueue,
        "enqueue_active_apify_job",
        lambda _conn, **kwargs: captured.update(kwargs)
        or ({"id": 9701, "status": "queued", "payload": kwargs["payload"]}, True),
    )

    result = enqueue._enqueue_final_v1_keyframe_qa(
        conn,
        kol_pool_id=88,
        evidence_id=701,
        staff={"id": 12, "user_id": 34},
    )

    payload = captured["payload"]
    assert result["status"] == "queued"
    assert result["provider_calls"] is False
    assert result["write_db"] is True
    assert captured["job_type"] == "video"
    assert payload["derive_method"] == enqueue.KEYFRAME_QA_DERIVE_METHOD
    assert payload["source_final_v1_cache_id"] == 1701
    assert payload["source_final_v1_sha256"] == "a" * 64
    assert payload["final_v1_qa_model"] == enqueue.KEYFRAME_QA_MODEL
    assert payload["my_kol_paid_action_fence"]["action"] == "video_analysis"
    assert "keyframe-qa" in captured["idempotency_key"]
    assert conn.commits == 1


def test_enqueue_is_youtube_only_and_requires_ready_source(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _prepare_enqueue(monkeypatch, url="https://www.instagram.com/reel/ABC123/")
    unsupported = enqueue._enqueue_final_v1_keyframe_qa(
        conn,
        kol_pool_id=88,
        evidence_id=701,
        staff={"id": 12, "user_id": 34},
    )
    assert unsupported == {
        "status": "unsupported_platform",
        "kol_pool_id": 88,
        "evidence_id": 701,
        "derive_method": enqueue.KEYFRAME_QA_DERIVE_METHOD,
        "reason": "keyframe_qa_youtube_only",
        "provider_calls": False,
        "write_db": False,
    }

    conn = _prepare_enqueue(monkeypatch)
    monkeypatch.setattr(enqueue, "_ready_final_v1_source", lambda *_args, **_kwargs: None)
    missing = enqueue._enqueue_final_v1_keyframe_qa(
        conn,
        kol_pool_id=88,
        evidence_id=701,
        staff={"id": 12, "user_id": 34},
    )
    assert missing["status"] == "final_v1_not_ready"
    assert missing["provider_calls"] is False


def test_enqueue_authorizes_before_any_evidence_or_cache_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn()
    probes: list[str] = []

    def denied(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        probes.append("authorization")
        raise MyKolPaidActionError("my_kol_paid_action_write_forbidden", 403)

    monkeypatch.setattr(enqueue, "build_target_fence", denied)
    monkeypatch.setattr(enqueue, "_load_owned_evidence", lambda *_a, **_k: pytest.fail("evidence leaked"))
    monkeypatch.setattr(enqueue, "_ready_final_v1_source", lambda *_a, **_k: pytest.fail("cache leaked"))
    monkeypatch.setattr(enqueue, "_ready_qa_cache", lambda *_a, **_k: pytest.fail("QA state leaked"))
    with pytest.raises(MyKolPaidActionError) as caught:
        enqueue._enqueue_final_v1_keyframe_qa(
            conn, kol_pool_id=88, evidence_id=999, staff={"id": 91, "user_id": 191}
        )
    assert (caught.value.code, caught.value.status_code) == ("my_kol_paid_action_write_forbidden", 403)
    assert probes == ["authorization"]


def test_ready_qa_cache_requires_exact_video_cache_and_payload_hash() -> None:
    result = {
        "final_v1_pass": {
            "source_target_id": "701",
            "source_cache_id": 1701,
            "source_payload_sha256": "a" * 64,
        }
    }
    assert qa_cache_matches_source(
        result, evidence_id=701, source_cache_id=1701, source_payload_sha256="a" * 64
    )
    assert not qa_cache_matches_source(
        result, evidence_id=702, source_cache_id=1701, source_payload_sha256="a" * 64
    )
    assert not qa_cache_matches_source(
        result, evidence_id=701, source_cache_id=1701, source_payload_sha256="b" * 64
    )


@pytest.mark.parametrize("qa_variant", ["stale_after_final_v1_update", "legacy_without_fence"])
def test_worker_cache_shortcut_rejects_stale_or_legacy_qa_across_runtime(
    qa_variant: str,
) -> None:
    from app.workers import apify_jobs_worker
    from app.workers.apify_jobs_worker_runtime import process_job_impl

    final_v1 = {
        "layer1_visual_content": {"content_summary": "updated lens review"},
        "layer2_viewer_emotion": {},
        "layer3_three_values": {},
        "layer4_attribution": {},
        "layer5_recommendations": {},
        "layer6_flags_and_scores": {},
    }
    source_sha = enqueue.final_v1_payload_sha256(final_v1)
    qa_result = (
        {
            "final_v1_pass": {
                "source_target_id": "701",
                "source_cache_id": 1701,
                "source_payload_sha256": "a" * 64,
            }
        }
        if qa_variant == "stale_after_final_v1_update"
        else {"qa_pass": True}
    )
    conn = _WorkerCacheConn(
        {
            # The cache upsert keeps the unique row id while replacing result bytes.
            "source_cache_id": 1701,
            "source_result": {"video_analysis_final_v1": final_v1},
            "qa_result": qa_result,
        }
    )
    events: list[str] = []
    namespace = dict(vars(apify_jobs_worker))
    namespace.update(
        {
            "_advisory_lock": lambda *_args: True,
            "_advisory_unlock": lambda *_args: None,
            "_acquire_llm_slot": lambda _conn: "slot-1",
            "_analysis_cache_exists": lambda *_args: pytest.fail("generic cache shortcut used for keyframe QA"),
            "_finish_skipped": lambda *_args, **_kwargs: events.append("skipped"),
            "_llm_budget_preflight": lambda *_args, **_kwargs: {},
            "_google_allowed": lambda *_args, **_kwargs: (True, "allowed", 0.01),
            "_log_budget_preflight_record_only": lambda **_kwargs: None,
            "_google_execution_authorization": lambda *_args, **_kwargs: {
                "binding": f"google/{enqueue.KEYFRAME_QA_MODEL}",
                "execution_class": "production",
            },
            "_respect_gemini_qps": lambda *_args: None,
            "_process_gemini_video": lambda *_args: events.append("processed"),
        }
    )
    process_job_impl(
        conn,  # type: ignore[arg-type]
        {
            "id": 9705,
            "job_type": "video",
            "payload": {
                "target_type": "video",
                "target_id": "701",
                "derive_method": enqueue.KEYFRAME_QA_DERIVE_METHOD,
                "source_final_v1_cache_id": 1701,
                "source_final_v1_sha256": source_sha,
                "final_v1_qa_model": enqueue.KEYFRAME_QA_MODEL,
            },
        },
        namespace,
    )
    assert events == ["processed"]


def test_worker_cache_shortcut_skips_only_exactly_fenced_qa() -> None:
    from app.workers import apify_jobs_worker
    from app.workers.apify_jobs_worker_runtime import process_job_impl

    final_v1 = {
        "layer1_visual_content": {"content_summary": "current lens review"},
        "layer2_viewer_emotion": {},
        "layer3_three_values": {},
        "layer4_attribution": {},
        "layer5_recommendations": {},
        "layer6_flags_and_scores": {},
    }
    source_sha = enqueue.final_v1_payload_sha256(final_v1)
    conn = _WorkerCacheConn(
        {
            "source_cache_id": 1701,
            "source_result": {"video_analysis_final_v1": final_v1},
            "qa_result": {
                "final_v1_pass": {
                    "source_target_id": "701",
                    "source_cache_id": 1701,
                    "source_payload_sha256": source_sha,
                }
            },
        }
    )
    events: list[str] = []
    namespace = dict(vars(apify_jobs_worker))
    namespace.update(
        {
            "_advisory_lock": lambda *_args: True,
            "_advisory_unlock": lambda *_args: None,
            "_acquire_llm_slot": lambda _conn: "slot-1",
            "_analysis_cache_exists": lambda *_args: pytest.fail("generic cache shortcut used for keyframe QA"),
            "_final_v1_scope_checkpoint": lambda *_args, **_kwargs: True,
            "_finish_skipped": lambda *_args, **_kwargs: events.append("skipped"),
            "_process_gemini_video": lambda *_args: events.append("processed"),
        }
    )
    process_job_impl(
        conn,  # type: ignore[arg-type]
        {
            "id": 9706,
            "job_type": "video",
            "payload": {
                "target_type": "video",
                "target_id": "701",
                "derive_method": enqueue.KEYFRAME_QA_DERIVE_METHOD,
                "source_final_v1_cache_id": 1701,
                "source_final_v1_sha256": source_sha,
                "final_v1_qa_model": enqueue.KEYFRAME_QA_MODEL,
            },
        },
        namespace,
    )
    assert events == ["skipped"]


def test_enqueue_preflight_uses_exact_qa_binding_and_cost_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        enqueue.llm_gateway,
        "budget_preflight",
        lambda prompt, **kwargs: captured.update({"prompt": prompt, **kwargs}) or {"providers": []},
    )
    enqueue._qa_budget_preflight("review video 701")
    assert captured["purpose"] == "keyframe_qa"
    assert captured["model_override"] == enqueue.KEYFRAME_QA_MODEL
    assert captured["model_fallbacks"] == []
    assert captured["cost_tag"] == enqueue.LLM_BUDGET_SCOPE
    assert captured["require_configured"] is False


def test_admin_route_exposes_single_video_queue_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routers import vkpi_kol_pool

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_keyframe_qa_enqueue,
        "enqueue_final_v1_keyframe_qa",
        lambda **kwargs: captured.update(kwargs) or {"status": "queued", "provider_calls": False},
    )
    staff = {"id": 12, "user_id": 34}
    result = vkpi_kol_pool.enqueue_pool_item_video_keyframe_qa(
        88,
        {"evidence_id": 701},
        staff,
    )
    assert result == {"status": "queued", "provider_calls": False}
    assert captured == {"kol_pool_id": 88, "evidence_id": 701, "staff": staff}


def test_paid_worker_rejects_unfenced_keyframe_job() -> None:
    from app.workers.apify_jobs_worker_paid_scope import revalidate_paid_job_scope

    action, reason, actor = revalidate_paid_job_scope(
        {
            "target_type": "video",
            "target_id": "701",
            "derive_method": enqueue.KEYFRAME_QA_DERIVE_METHOD,
        },
        "video",
        connection_scope=lambda: pytest.fail("unfenced job must fail before DB"),
    )
    assert action == "video_analysis"
    assert reason == "video_analysis_authorization_fence_required"
    assert actor is None


def test_worker_preflight_uses_qa_model_not_full_video_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import apify_jobs_worker_prep as prep

    captured: dict[str, Any] = {}
    monkeypatch.setattr(prep, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(
        prep.llm_gateway,
        "budget_preflight",
        lambda prompt, **kwargs: captured.update({"prompt": prompt, **kwargs}) or {"providers": []},
    )
    prep._llm_budget_preflight(
        {"id": 9, "job_type": "video"},
        {
            "target_type": "video",
            "target_id": "701",
            "derive_method": enqueue.KEYFRAME_QA_DERIVE_METHOD,
            "final_v1_qa_model": enqueue.KEYFRAME_QA_MODEL,
        },
    )
    assert captured["purpose"] == "keyframe_qa"
    assert captured["model_override"] == enqueue.KEYFRAME_QA_MODEL


@contextmanager
def _qa_frames() -> Any:
    yield {
        "frames": [{"image_path": "/tmp/frame.jpg", "timestamp": "00:15"}],
        "frame_meta": [{"timestamp": "00:15", "reason": "product"}],
        "keyframe_requests": [{"timestamp": "00:15", "reason": "product"}],
        "download": {"bytes": 1234},
    }


def test_worker_reuses_ready_final_v1_and_runs_only_qa(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import apify_jobs_worker_gemini_judges as judges

    final_v1 = {
        "layer1_visual_content": {"content_summary": "lens review", "scene_timeline": [{"timestamp": "00:15", "what": "lens"}]},
        "layer2_viewer_emotion": {},
        "layer3_three_values": {},
        "layer4_attribution": {},
        "layer5_recommendations": {},
        "layer6_flags_and_scores": {},
    }
    source = {
        "cache_id": 1701,
        "model": "gemini-3.6-flash",
        "prompt_version": "final_v1_pure_video_evidence_v2",
        "payload_sha256": enqueue.final_v1_payload_sha256(final_v1),
        "final_v1": final_v1,
    }
    checkpoints: list[bool] = []
    writes: list[dict[str, Any]] = []
    costs: list[dict[str, Any]] = []

    monkeypatch.setattr(judges, "_keyframe_qa_scope_checkpoint", lambda *_args, **kwargs: checkpoints.append(kwargs["provider_calls_performed"]) or True)
    monkeypatch.setattr(judges, "_load_ready_final_v1_source_for_qa", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(judges, "_provider_budget_preflight", lambda *_args, **_kwargs: {"providers": [{"provider": "google", "provider_calls_allowed": True, "estimated_cost_usd": 0.01}]})
    monkeypatch.setattr(judges, "_provider_allowed", lambda *_args, **_kwargs: (True, "allowed", 0.01))
    monkeypatch.setattr(judges, "_extract_keyframes_for_qa", lambda *_args, **_kwargs: _qa_frames())

    async def _analyze(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["final_v1_result"] is final_v1
        return {
            "analyzed": True,
            "provider_calls_performed": True,
            "model": enqueue.KEYFRAME_QA_MODEL,
            "method": "qa_only",
            "qa_pass": True,
            "final_v1_keyframe_qa": {"qa_pass": True, "checks": []},
            "usage_metadata": {"prompt_token_count": 100, "candidates_token_count": 20},
            "cost_authority": "llm_production_google_generate_content_v1",
            "llm_attempts": [{"state": "settled", "actual_cost_usd": 0.002, "input_tokens": 100, "output_tokens": 20}],
        }

    monkeypatch.setattr(judges.gemini_video_analyzer, "analyze_final_v1_keyframe_qa", _analyze)
    monkeypatch.setattr(judges, "_authoritative_gemini_cost", lambda *_args, **_kwargs: (0.002, "llm_production_atomic_attempt_ledger", 100, 20))
    monkeypatch.setattr(judges, "_record_gemini_cost", lambda **kwargs: costs.append(kwargs))
    monkeypatch.setattr(judges, "_write_gemini_cache", lambda _conn, **kwargs: writes.append(kwargs))

    judges._process_gemini_video_final_v1_keyframe_qa(
        object(),  # type: ignore[arg-type]
        {"id": 9701},
        {
            "target_type": "video",
            "target_id": "701",
            "derive_method": enqueue.KEYFRAME_QA_DERIVE_METHOD,
            "source_final_v1_cache_id": 1701,
            "source_final_v1_sha256": source["payload_sha256"],
            "final_v1_qa_model": enqueue.KEYFRAME_QA_MODEL,
            "staff_id": 12,
            "_llm_execution": {"production_authorized": True, "execution_class": "production"},
        },
        {"id": 701, "content_url": YOUTUBE_URL, "title": "demo", "kol_pool_id": 88},
        0.01,
    )

    assert checkpoints == [False, False, True]
    assert len(costs) == 1
    assert len(writes) == 1
    raw = writes[0]["raw"]
    assert raw["method"] == "final_v1_ready_cache_keyframe_qa"
    assert raw["video_analysis_final_v1"] is final_v1
    assert raw["final_v1_pass"]["reused_ready_cache"] is True
    assert raw["final_v1_pass"]["provider_calls_performed"] is False
    assert raw["final_v1_pass"]["source_target_id"] == "701"
    assert raw["cost_authority"] == "llm_production_google_generate_content_v1"
    assert len(raw["llm_attempts"]) == 1
    assert costs[0]["cost_basis"] == "llm_production_atomic_attempt_ledger"
    assert [segment["stage"] for segment in raw["cost_segments"]] == ["keyframe_qa_pass"]
    assert writes[0]["derive_method"] == enqueue.KEYFRAME_QA_DERIVE_METHOD


def test_worker_blocks_drifted_source_before_qa(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import apify_jobs_worker_gemini_judges as judges

    blocked: list[tuple[Any, ...]] = []
    provider: list[str] = []
    monkeypatch.setattr(judges, "_keyframe_qa_scope_checkpoint", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        judges,
        "_load_ready_final_v1_source_for_qa",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(judges.KeyframeQaSourceError("keyframe_qa_source_drifted")),
    )
    monkeypatch.setattr(judges, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(judges.gemini_video_analyzer, "analyze_final_v1_keyframe_qa", lambda **_kwargs: provider.append("qa"))

    judges._process_gemini_video_final_v1_keyframe_qa(
        object(),  # type: ignore[arg-type]
        {"id": 9702},
        {
            "target_type": "video",
            "target_id": "701",
            "derive_method": enqueue.KEYFRAME_QA_DERIVE_METHOD,
            "source_final_v1_cache_id": 1701,
            "source_final_v1_sha256": "a" * 64,
        },
        {"id": 701, "content_url": YOUTUBE_URL},
        0.01,
    )
    assert provider == []
    assert blocked[0][2] == "keyframe_qa_source_drifted"
    assert blocked[0][3]["provider_calls_performed"] is False


def test_keyframe_qa_authoritative_attempts_never_write_outer_cost_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import apify_jobs_worker_gemini as worker

    monkeypatch.setattr(
        worker.budget_guard, "record_cost",
        lambda **_kwargs: pytest.fail("strict adapter already settled this attempt"),
    )
    raw = {
        "analyzed": True, "model": enqueue.KEYFRAME_QA_MODEL,
        "cost_authority": "llm_production_google_generate_content_v1",
        "llm_attempts": [{
            "state": "settled", "actual_cost_usd": 0.002,
            "input_tokens": 100, "output_tokens": 20,
        }],
    }
    cost, basis, tokens_in, tokens_out = worker._authoritative_gemini_cost(raw, 9.0)
    ledger = worker._record_gemini_cost(
        job={"id": 9701}, payload={"target_type": "video", "target_id": "701"},
        raw=raw, cost=cost, cost_basis=basis, tokens_in=tokens_in,
        tokens_out=tokens_out, latency_ms=10, preflight_cost=9.0,
    )
    assert (cost, basis, tokens_in, tokens_out) == (
        0.002, "llm_production_atomic_attempt_ledger", 100, 20,
    )
    assert ledger["outer_ledger_write"] is False


def test_worker_rechecks_source_after_provider_and_never_writes_drifted_qa(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import apify_jobs_worker_gemini_judges as judges

    source = {
        "cache_id": 1701, "model": "gemini-3.6-flash", "prompt_version": "p1",
        "payload_sha256": "a" * 64,
        "final_v1": {"layer1_visual_content": {}, "layer2_viewer_emotion": {},
                     "layer3_three_values": {}, "layer4_attribution": {},
                     "layer5_recommendations": {}, "layer6_flags_and_scores": {}},
    }
    loads = 0
    blocked: list[tuple[Any, ...]] = []
    writes: list[dict[str, Any]] = []

    def load(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal loads
        loads += 1
        if loads == 2:
            raise judges.KeyframeQaSourceError("keyframe_qa_source_drifted")
        return source

    monkeypatch.setattr(judges, "_keyframe_qa_scope_checkpoint", lambda *_a, **_k: True)
    monkeypatch.setattr(judges, "_load_ready_final_v1_source_for_qa", load)
    monkeypatch.setattr(judges, "_provider_budget_preflight", lambda *_a, **_k: {})
    monkeypatch.setattr(judges, "_provider_allowed", lambda *_a, **_k: (True, "allowed", 0.01))
    monkeypatch.setattr(judges, "_extract_keyframes_for_qa", lambda *_a, **_k: _qa_frames())
    monkeypatch.setattr(judges, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(judges, "_write_gemini_cache", lambda _conn, **kwargs: writes.append(kwargs))

    async def analyzed(**_kwargs: Any) -> dict[str, Any]:
        return {"analyzed": True, "model": enqueue.KEYFRAME_QA_MODEL,
                "provider_calls_performed": True,
                "cost_authority": "llm_production_google_generate_content_v1", "llm_attempts": []}

    monkeypatch.setattr(judges.gemini_video_analyzer, "analyze_final_v1_keyframe_qa", analyzed)
    judges._process_gemini_video_final_v1_keyframe_qa(
        object(), {"id": 9703},
        {"target_id": "701", "source_final_v1_cache_id": 1701,
         "source_final_v1_sha256": "a" * 64},
        {"id": 701, "content_url": YOUTUBE_URL}, 0.01,
    )
    assert loads == 2 and writes == []
    assert blocked[0][2] == "keyframe_qa_source_drifted"
    assert blocked[0][3] == {"stage": "keyframe_qa_source_recheck", "provider_calls_performed": True}


def test_worker_local_qa_early_return_keeps_post_checkpoint_and_cost_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker_gemini_judges as judges

    source = {
        "cache_id": 1701,
        "model": "gemini-3.6-flash",
        "prompt_version": "p1",
        "payload_sha256": "a" * 64,
        "final_v1": {
            "layer1_visual_content": {}, "layer2_viewer_emotion": {},
            "layer3_three_values": {}, "layer4_attribution": {},
            "layer5_recommendations": {}, "layer6_flags_and_scores": {},
        },
    }
    checkpoints: list[bool] = []
    loads = 0

    def load(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal loads
        loads += 1
        return source

    monkeypatch.setattr(
        judges,
        "_keyframe_qa_scope_checkpoint",
        lambda *_a, **kw: checkpoints.append(kw["provider_calls_performed"]) or True,
    )
    monkeypatch.setattr(judges, "_load_ready_final_v1_source_for_qa", load)
    monkeypatch.setattr(judges, "_provider_budget_preflight", lambda *_a, **_k: {})
    monkeypatch.setattr(judges, "_provider_allowed", lambda *_a, **_k: (True, "allowed", 0.01))
    monkeypatch.setattr(judges, "_extract_keyframes_for_qa", lambda *_a, **_k: _qa_frames())

    async def local_early_return(**_kwargs: Any) -> dict[str, Any]:
        return {
            "analyzed": False,
            "provider_calls_performed": False,
            "error": "Gemini not available",
            "cost_authority": "llm_production_google_generate_content_v1",
            "llm_attempts": [],
        }

    monkeypatch.setattr(
        judges.gemini_video_analyzer,
        "analyze_final_v1_keyframe_qa",
        local_early_return,
    )
    monkeypatch.setattr(
        judges,
        "_authoritative_gemini_cost",
        lambda *_a, **_k: pytest.fail("no provider call must not manufacture cost"),
    )
    monkeypatch.setattr(
        judges,
        "_record_gemini_cost",
        lambda **_k: pytest.fail("no provider call must not write an outer ledger"),
    )
    monkeypatch.setattr(
        judges,
        "_write_gemini_cache",
        lambda *_a, **_k: pytest.fail("failed local QA must not publish cache"),
    )

    with pytest.raises(RuntimeError, match="before provider call: Gemini not available"):
        judges._process_gemini_video_final_v1_keyframe_qa(
            object(),
            {"id": 9704},
            {
                "target_id": "701",
                "source_final_v1_cache_id": 1701,
                "source_final_v1_sha256": "a" * 64,
            },
            {"id": 701, "content_url": YOUTUBE_URL},
            0.01,
        )

    assert checkpoints == [False, False, False]
    assert loads == 1
