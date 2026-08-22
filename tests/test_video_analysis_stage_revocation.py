"""Negative gates: a revoked actor stops every paid/external video-analysis stage.

Covers the P1 close-out items that the provider-chain tests do not:
YouTube subtitle / direct-URL / slow-download / File API stage gates inside the
analyzer child, the child -> parent ``scope_revoked`` hand-off, cache-hit
follow-up suppression, reused-video session lineage, and the HTTP ban on
``local_evaluation``.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import Any

import pytest

from app.services.ai.analyzers import gemini_video, gemini_video_youtube


YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


class _RevokeAt:
    """Checkpoint that raises once the named stage is reached."""

    def __init__(self, stage: str, reason: str = "provider_job_actor_inactive") -> None:
        self.stage = stage
        self.reason = reason
        self.seen: list[str] = []

    def __call__(self, stage: str) -> None:
        self.seen.append(stage)
        if stage == self.stage:
            raise gemini_video.AnalysisScopeRevoked(self.reason, stage=stage)


def _youtube_env(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "gemini_client", object())
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(
        gemini_video_youtube,
        "fetch_youtube_subtitles",
        lambda _url: calls.append("subtitles") or "",
    )
    monkeypatch.setattr(
        gemini_video,
        "_strict_generate_content",
        lambda **_kwargs: calls.append("generate") or (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)
    monkeypatch.setattr(gemini_video, "final_v1_gemini_models", lambda *_args: ["gemini-3.6-flash"])
    monkeypatch.setattr(
        gemini_video_youtube.subprocess,
        "run",
        lambda *_args, **_kwargs: calls.append("yt-dlp") or None,
    )


def _run_youtube(checkpoint: _RevokeAt) -> dict[str, Any]:
    return asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(
            YOUTUBE_URL,
            "demo",
            schema_version="final_v1",
            authorization_checkpoint=checkpoint,
        )
    )


def test_youtube_revocation_before_subtitles_makes_no_external_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _youtube_env(monkeypatch, calls)
    checkpoint = _RevokeAt("youtube_subtitles")
    result = _run_youtube(checkpoint)
    assert calls == []
    assert result["analyzed"] is False
    assert result["scope_revoked"] == "provider_job_actor_inactive"
    assert result["scope_revoked_stage"] == "youtube_subtitles"
    assert result["error"] == "scope_revoked:provider_job_actor_inactive"


def test_youtube_revocation_before_direct_attempt_stops_after_subtitles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _youtube_env(monkeypatch, calls)
    checkpoint = _RevokeAt("youtube_direct_attempt", "provider_job_permission_revoked")
    result = _run_youtube(checkpoint)
    assert calls == ["subtitles"]
    assert result["scope_revoked"] == "provider_job_permission_revoked"
    assert result["scope_revoked_stage"] == "youtube_direct_attempt"


def test_youtube_revocation_between_direct_failure_and_slow_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _youtube_env(monkeypatch, calls)
    checkpoint = _RevokeAt("youtube_download")
    result = _run_youtube(checkpoint)
    # direct attempt ran (and failed), download never started
    assert calls == ["subtitles", "generate"]
    assert "yt-dlp" not in calls
    assert result["scope_revoked_stage"] == "youtube_download"
    assert checkpoint.seen == ["youtube_subtitles", "youtube_direct_attempt", "youtube_download"]


def test_youtube_revocation_before_file_api_upload_after_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _youtube_env(monkeypatch, calls)
    monkeypatch.setattr(gemini_video_youtube.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(gemini_video_youtube.os.path, "getsize", lambda _path: 5_000_000)
    uploads: list[str] = []

    class _Files:
        def upload(self, **_kwargs: Any) -> Any:
            uploads.append("upload")
            raise AssertionError("upload must not run after revocation")

        def delete(self, **_kwargs: Any) -> None:
            return None

    class _Client:
        files = _Files()

    monkeypatch.setattr(gemini_video_youtube, "gemini_client", _Client())
    checkpoint = _RevokeAt("file_api_upload")
    result = _run_youtube(checkpoint)
    assert calls == ["subtitles", "generate", "yt-dlp"]
    assert uploads == []
    assert result["scope_revoked_stage"] == "file_api_upload"


def test_youtube_revocation_before_file_api_attempt_deletes_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _youtube_env(monkeypatch, calls)
    monkeypatch.setattr(gemini_video_youtube.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(gemini_video_youtube.os.path, "getsize", lambda _path: 5_000_000)
    deleted: list[str] = []

    class _State:
        name = "ACTIVE"

    class _File:
        name = "files/abc"
        uri = "https://generativelanguage/files/abc"
        state = _State()

    class _Files:
        def upload(self, **_kwargs: Any) -> Any:
            calls.append("upload")
            return _File()

        def get(self, *, name: str) -> Any:
            return _File()

        def delete(self, *, name: str) -> None:
            deleted.append(name)

    class _Client:
        files = _Files()

    monkeypatch.setattr(gemini_video_youtube, "gemini_client", _Client())
    checkpoint = _RevokeAt("file_api_attempt")
    result = _run_youtube(checkpoint)
    assert calls == ["subtitles", "generate", "yt-dlp", "upload"]
    assert deleted == ["files/abc"]
    assert result["analyzed"] is False
    assert result["scope_revoked_stage"] == "file_api_attempt"


def test_local_video_revocation_before_upload_and_before_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 4096)
    calls: list[str] = []

    class _State:
        name = "ACTIVE"

    class _File:
        name = "files/local"
        uri = "https://generativelanguage/files/local"
        state = _State()

    class _Files:
        def upload(self, **_kwargs: Any) -> Any:
            calls.append("upload")
            return _File()

        def get(self, *, name: str) -> Any:
            return _File()

        def delete(self, *, name: str) -> None:
            calls.append("delete")

    class _Client:
        files = _Files()

    monkeypatch.setattr(gemini_video, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video, "gemini_client", _Client())
    monkeypatch.setattr(gemini_video, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(
        gemini_video,
        "_strict_generate_content",
        lambda **_kwargs: calls.append("generate") or None,
    )
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)

    before_upload = _RevokeAt("file_api_upload")
    result = asyncio.run(
        gemini_video.analyze_local_video_with_gemini(
            str(video), "demo", schema_version="final_v1", authorization_checkpoint=before_upload
        )
    )
    assert calls == []
    assert result["scope_revoked_stage"] == "file_api_upload"

    before_attempt = _RevokeAt("file_api_attempt")
    result = asyncio.run(
        gemini_video.analyze_local_video_with_gemini(
            str(video), "demo", schema_version="final_v1", authorization_checkpoint=before_attempt
        )
    )
    assert calls == ["upload", "delete"]
    assert "generate" not in calls
    assert result["scope_revoked_stage"] == "file_api_attempt"


def _child_namespace() -> dict[str, Any]:
    from app.workers.apify_jobs_worker_media import _gemini_analyzer_child_code

    code = _gemini_analyzer_child_code()
    head = code[: code.index("async def _run(")]
    namespace: dict[str, Any] = {}
    exec(compile(head, "gemini_child_head", "exec"), namespace)  # noqa: S102 - own child source
    return namespace


def test_child_checkpoint_revalidates_signed_final_v1_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker_paid_scope as paid_scope

    namespace = _child_namespace()
    factory = namespace["_scope_checkpoint_for"]
    assert factory({"derive_method": "gemini_video_v2"}) is None
    assert factory({"derive_method": "video_analysis_final_v1", "local_evaluation": True}) is None

    seen: list[tuple[str, str]] = []

    def fake_revalidate(payload: dict[str, Any], job_type: str, *, connection_scope: Any) -> tuple:
        seen.append((job_type, str(payload.get("target_id"))))
        return ("video_analysis", "provider_job_actor_inactive", None)

    monkeypatch.setattr(paid_scope, "revalidate_paid_job_scope", fake_revalidate)
    checkpoint = factory({"derive_method": "video_analysis_final_v1", "target_id": "701"})
    assert checkpoint is not None
    with pytest.raises(gemini_video.AnalysisScopeRevoked) as raised:
        checkpoint("youtube_download")
    assert raised.value.reason == "provider_job_actor_inactive"
    assert raised.value.stage == "youtube_download"
    assert seen == [("video", "701")]


def test_parent_blocks_on_child_reported_revocation_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker_gemini as gemini
    from app.workers import apify_jobs_worker_paid_scope as paid_scope

    blocked: list[tuple[Any, ...]] = []
    writes: list[str] = []
    monkeypatch.setattr(
        gemini,
        "_load_video_evidence",
        lambda *_args: {"id": 701, "kol_pool_id": 88, "content_url": YOUTUBE_URL, "title": "demo"},
    )
    monkeypatch.setattr(
        gemini,
        "_run_gemini_analyzer_with_timeout",
        lambda *_args, **_kwargs: {
            "analyzed": False,
            "error": "scope_revoked:provider_job_permission_revoked",
            "scope_revoked": "provider_job_permission_revoked",
            "scope_revoked_stage": "file_api_upload",
        },
    )
    monkeypatch.setattr(
        paid_scope,
        "revalidate_paid_job_scope",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("child verdict is final")),
    )
    monkeypatch.setattr(gemini, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(gemini, "_record_gemini_cost", lambda **_kwargs: writes.append("cost"))
    monkeypatch.setattr(gemini, "_write_gemini_cache", lambda *_args, **_kwargs: writes.append("cache"))

    gemini._process_gemini_video(
        object(),  # type: ignore[arg-type]
        {"id": 906},
        {"target_type": "video", "target_id": "701", "derive_method": "video_analysis_final_v1"},
        0.01,
    )
    assert writes == []
    assert blocked[0][2] == "provider_job_permission_revoked"
    assert blocked[0][3]["provider_calls_performed"] is True
    assert blocked[0][3]["stage"] == "file_api_upload"


def test_cache_hit_revocation_blocks_before_result_sync_and_followups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker
    from app.workers import apify_jobs_worker_gemini as gemini
    from app.workers import apify_jobs_worker_paid_scope as paid_scope
    from app.workers.apify_jobs_worker_runtime import process_job_impl

    blocked: list[tuple[Any, ...]] = []
    skipped: list[str] = []
    monkeypatch.setattr(gemini, "_block_job", lambda *args: blocked.append(args))
    namespace = dict(vars(apify_jobs_worker))
    namespace.update(
        {
            "_advisory_lock": lambda *_args: True,
            "_advisory_unlock": lambda *_args: None,
            "_acquire_llm_slot": lambda _conn: "slot-1",
            "_analysis_cache_exists": lambda *_args: True,
            "_finish_skipped": lambda *args, **kwargs: skipped.append(str(args[2])),
            "_process_gemini_video": lambda *_args: skipped.append("gemini"),
        }
    )
    monkeypatch.setattr(
        paid_scope,
        "revalidate_paid_job_scope",
        lambda *_args, **_kwargs: ("video_analysis", "provider_job_actor_inactive", None),
    )
    process_job_impl(
        None,  # type: ignore[arg-type]
        {
            "id": 907,
            "job_type": "video",
            "payload": {
                "target_type": "video",
                "target_id": "701",
                "derive_method": "video_analysis_final_v1",
            },
        },
        namespace,
    )
    assert skipped == []
    assert blocked[0][2] == "provider_job_actor_inactive"
    assert blocked[0][3]["provider_calls_performed"] is False


def test_cache_hit_with_valid_actor_still_finishes_as_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker
    from app.workers import apify_jobs_worker_gemini as gemini
    from app.workers import apify_jobs_worker_paid_scope as paid_scope
    from app.workers.apify_jobs_worker_runtime import process_job_impl

    blocked: list[tuple[Any, ...]] = []
    skipped: list[str] = []
    monkeypatch.setattr(gemini, "_block_job", lambda *args: blocked.append(args))
    namespace = dict(vars(apify_jobs_worker))
    namespace.update(
        {
            "_advisory_lock": lambda *_args: True,
            "_advisory_unlock": lambda *_args: None,
            "_acquire_llm_slot": lambda _conn: "slot-1",
            "_analysis_cache_exists": lambda *_args: True,
            "_finish_skipped": lambda *args, **kwargs: skipped.append(str(args[2])),
        }
    )
    monkeypatch.setattr(
        paid_scope,
        "revalidate_paid_job_scope",
        lambda *_args, **_kwargs: ("video_analysis", "", {"id": 1}),
    )
    process_job_impl(
        None,  # type: ignore[arg-type]
        {
            "id": 908,
            "job_type": "video",
            "payload": {
                "target_type": "video",
                "target_id": "701",
                "derive_method": "video_analysis_final_v1",
            },
        },
        namespace,
    )
    assert blocked == []
    assert skipped == ["skipped_existing_analysis_cache"]


def _url_router_env(monkeypatch: pytest.MonkeyPatch, enqueued: list[dict[str, Any]]) -> Any:
    from app.api.routers import vkpi_kol_pool_search as router_module

    class _Classified:
        url_type = "video"
        platform = "youtube"

    monkeypatch.setattr(router_module.kol_url_deep_crawl, "classify_url", lambda _url: _Classified())
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: {
            "url_type": "video",
            "matched_kol_pool_id": 88,
            "video_flow": {"evidence_id": 7, "operation": "existing_creator_video_analysis"},
        },
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_stored_video_analysis_job",
        lambda **kwargs: enqueued.append(dict(kwargs)) or {"status": "queued", "job_id": 1, "write_db": True},
    )
    monkeypatch.setattr(
        router_module.kol_search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: {"id": 42},
    )
    return router_module


def test_reused_video_without_recorded_session_item_never_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[dict[str, Any]] = []
    router_module = _url_router_env(monkeypatch, enqueued)
    monkeypatch.setattr(
        router_module.kol_search_sessions,
        "attach_url_result",
        lambda _session_id, _result: {"id": 42, "items": [{"id": 1, "item_type": "url_profile", "kol_pool_id": 88}]},
    )
    with pytest.raises(RuntimeError, match="video_analysis_session_item_required"):
        router_module._run_url_deep_crawl(
            {"url": YOUTUBE_URL, "execute": True},
            staff={"id": 1},
            default_defer_profile=True,
            default_create_session=False,
            default_source="kol_url",
        )
    assert enqueued == []


def test_reused_video_with_ambiguous_session_items_never_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[dict[str, Any]] = []
    router_module = _url_router_env(monkeypatch, enqueued)
    duplicate = {"id": 1, "item_type": "url_video", "kol_pool_id": 88, "evidence_id": 7}
    monkeypatch.setattr(
        router_module.kol_search_sessions,
        "attach_url_result",
        lambda _session_id, _result: {"id": 42, "items": [duplicate, {**duplicate, "id": 2}]},
    )
    with pytest.raises(RuntimeError, match="video_analysis_session_item_required"):
        router_module._run_url_deep_crawl(
            {"url": YOUTUBE_URL, "execute": True},
            staff={"id": 1},
            default_defer_profile=True,
            default_create_session=False,
            default_source="kol_url",
        )
    assert enqueued == []


def test_url_endpoint_rejects_local_evaluation_before_any_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    enqueued: list[dict[str, Any]] = []
    router_module = _url_router_env(monkeypatch, enqueued)
    touched: list[str] = []
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: touched.append("dry_run") or {},
    )
    with pytest.raises(ValueError, match="local_evaluation_http_forbidden"):
        router_module._run_url_deep_crawl(
            {"url": YOUTUBE_URL, "execute": True, "local_evaluation": True},
            staff={"id": 1},
            default_defer_profile=True,
            default_create_session=False,
            default_source="kol_url",
        )
    assert touched == []
    assert enqueued == []
    with pytest.raises(HTTPException) as raised:
        router_module.dry_run_kol_url_deep_crawl(
            {"url": YOUTUBE_URL, "execute": True, "local_evaluation": True},
            staff={"id": 1},
        )
    assert raised.value.status_code == 422


def test_enqueue_refuses_local_evaluation_with_any_user_lineage() -> None:
    from app.domains.kol import video_analysis_enqueue
    from app.platform.llm_local_evaluation import LocalEvaluationCapabilityError

    for lineage in (
        {"staff": {"id": 1}},
        {"enforce_target_write": True},
        {"search_session_id": 5},
        {"search_session_item_id": 7},
        {"parent_job_id": 9},
        {"provider_parent_payload": {"x": 1}},
    ):
        with pytest.raises(LocalEvaluationCapabilityError, match="local_evaluation_server_scope_required"):
            video_analysis_enqueue._enqueue_final_v1_video_analysis(
                object(),
                kol_pool_id=88,
                evidence_id=701,
                local_evaluation=True,
                **lineage,
            )


def test_content_fit_checkpoint_revocation_blocks_with_honest_provider_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import content_fit_analysis
    from app.workers import apify_jobs_worker, apify_jobs_worker_handlers as handlers
    from app.workers import apify_jobs_worker_paid_scope as paid_scope

    blocked: list[tuple[Any, ...]] = []
    verdicts = iter([("content_fit_analysis", "", {"id": 1}), ("content_fit_analysis", "my_kol_paid_action_actor_inactive", None)])
    monkeypatch.setattr(paid_scope, "revalidate_paid_job_scope", lambda *_a, **_k: next(verdicts))
    monkeypatch.setattr(apify_jobs_worker, "_block_job", lambda *args: blocked.append(args))
    monkeypatch.setattr(handlers, "_resolve_job_staff", lambda *_args: {"id": 1})
    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())

    def fake_analyze(_kid: int, _sku: Any, *, staff: Any, authorization_checkpoint: Any) -> dict[str, Any]:
        authorization_checkpoint(False)  # attempt 1: allowed
        authorization_checkpoint(True)  # attempt 2 / pre-write: revoked
        raise AssertionError("analysis must stop at the revoked checkpoint")

    monkeypatch.setattr(content_fit_analysis, "analyze_content_fit", fake_analyze)
    handlers._process_kol_content_fit_analysis(
        None,  # type: ignore[arg-type]
        {"id": 909},
        {"kol_pool_id": 88, "product_sku": "AF-35-PRO"},
    )
    assert blocked[0][2] == "my_kol_paid_action_actor_inactive"
    assert blocked[0][3]["provider_calls_performed"] is True
    assert blocked[0][3]["paid_action"] == "content_fit_analysis"
