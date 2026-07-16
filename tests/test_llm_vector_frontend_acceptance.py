from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.api.routers import kol_ops, vkpi_kol_pool_intel, vkpi_kol_pool_jobs, vkpi_kol_pool_search, vkpi_recall
from app.domains.kol import audience_stats, outreach_pack, profile_discovery_provider, recall_pipeline, url_deep_crawl
from app.domains.market import ai_today
from app.domains.projects import outreach as project_outreach
from app.services.kol import content_analyzer
from app.services.via import vector_memory


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "llm_vector_frontend_acceptance_manifest.json"
SECRET = "sk-live-contract-secret-123456"


@pytest.fixture(autouse=True)
def _provider_free_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", "1")
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str).lower()


def _assert_redacted(value: Any) -> None:
    text = _serialized(value)
    assert SECRET.lower() not in text
    assert "traceback" not in text


def test_manifest_inventories_frontend_paths_and_contract_owners() -> None:
    manifest = _manifest()

    assert manifest["provider_execution"] == "forbidden"
    assert manifest["credentials_required"] is False
    assert manifest["enforced_env"] == {"VKPI_LLM_GATEWAY_FORCE_OFFLINE": "1"}
    assert set(manifest["invariants"]) == {
        "bounded_timeout",
        "stable_non_500",
        "no_false_ready",
        "no_raw_exception",
    }
    assert set(manifest["families"]) == {
        "intelligent_ask",
        "kol_deep_analysis",
        "audience",
        "collaboration_outreach",
        "url_profile_discovery",
        "vector_semantic_search",
        "ai_today_evidence",
    }

    paths = manifest["paths"]
    ids = [item["id"] for item in paths]
    assert len(paths) >= 35
    assert len(ids) == len(set(ids))
    assert any("bounded_timeout" in item["contracts"] for item in paths)

    excluded = tuple(manifest["excluded_owners"])
    for item in paths:
        assert item["family"] in manifest["families"]
        assert item["coverage"] in {"direct", "family", "inventory_only"}
        assert set(item["contracts"]).issubset(manifest["invariants"])
        assert not item["backend_owner"].startswith(excluded)

        frontend = ROOT / item["frontend_source"]
        owner = ROOT / item["backend_owner"]
        assert frontend.is_file(), item["id"]
        assert owner.is_file(), item["id"]
        assert item["frontend_marker"] in frontend.read_text(encoding="utf-8"), item["id"]

    for family in manifest["families"].values():
        assert family["direct_tests"]
        for test_path in family["direct_tests"]:
            assert (ROOT / test_path).is_file(), test_path


def test_semantic_route_exception_is_stable_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recall_pipeline,
        "semantic_recall",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )

    result = vkpi_recall.get_semantic_recall(query="camera reviewer", limit=20, staff={"id": 7})

    assert result["status"] == "error"
    assert result["reason"] == "semantic_recall_unavailable"
    assert result["items"] == []
    assert result["stages"]["rerank"]["status"] == "skipped"
    _assert_redacted(result)


def test_semantic_embedding_and_rerank_timeouts_degrade_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_pipeline,
        "_embedding_recall",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError(SECRET)),
    )
    monkeypatch.setattr(recall_pipeline, "_ngram_recall", lambda *_args, **_kwargs: [])

    result = recall_pipeline.semantic_recall("camera reviewer", conn=object())

    assert result["status"] == "empty"
    assert result["stages"]["recall"]["method"] == recall_pipeline.RECALL_METHOD_FALLBACK
    assert result["stages"]["recall"]["degraded_reason"] == "embedding_timeout"
    _assert_redacted(result)

    from app.platform import llm_gateway

    monkeypatch.setattr(
        llm_gateway,
        "invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError(SECRET)),
    )
    scores, reason = recall_pipeline._invoke_rerank_llm(
        "camera reviewer",
        [
            {"kol_pool_id": 1, "display_name": "One", "coarse_score": 0.7},
            {"kol_pool_id": 2, "display_name": "Two", "coarse_score": 0.6},
        ],
    )

    assert scores == {}
    assert reason == "llm_timeout"
    _assert_redacted({"reason": reason})


def test_audience_timeout_and_upstream_reason_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def direct_call(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(vkpi_kol_pool_intel, "run_in_threadpool", direct_call)
    monkeypatch.setattr(
        audience_stats,
        "refresh_audience_stats",
        lambda _kol_id: (_ for _ in ()).throw(TimeoutError(SECRET)),
    )
    with pytest.raises(HTTPException) as timeout_error:
        asyncio.run(vkpi_kol_pool_intel.refresh_kol_audience_stats(7, staff={"id": 3}))

    assert timeout_error.value.status_code == 503
    assert timeout_error.value.detail["reason"] == "audience_refresh_timeout"
    _assert_redacted(timeout_error.value.detail)

    monkeypatch.setattr(
        audience_stats,
        "refresh_audience_stats",
        lambda _kol_id: {"status": "network_error", "reason": SECRET},
    )
    with pytest.raises(HTTPException) as upstream_error:
        asyncio.run(vkpi_kol_pool_intel.refresh_kol_audience_stats(7, staff={"id": 3}))

    assert upstream_error.value.status_code == 502
    assert upstream_error.value.detail["reason"] == "audience_provider_unavailable"
    assert "message" not in upstream_error.value.detail
    _assert_redacted(upstream_error.value.detail)


def test_deep_analysis_read_failure_never_claims_analysis_or_leaks_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vkpi_kol_pool_intel.kol_llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )

    result = vkpi_kol_pool_intel.get_pool_item_llm_deep_analysis(19, limit=20, staff={"id": 2})

    assert result["status"] == "unavailable"
    assert result["reason"] == "deep_analysis_read_failed"
    assert result["primary_result"] is None
    assert result["items"] == []
    assert "error_type" not in result
    _assert_redacted(result)


def test_outreach_pack_provider_exception_uses_template_without_false_llm_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outreach_pack.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError(SECRET)),
    )

    draft, provenance = outreach_pack._generate_email_draft(
        {"id": 1, "display_name": "Creator", "handle": "creator", "platform": "youtube"},
        {"why_fit": ["camera reviews"]},
        staff={"id": 3},
    )

    assert draft["personalized"] is False
    assert draft["email_en"]
    assert provenance["llm_used"] is False
    assert provenance["reason"] == "llm_unavailable_used_template"
    _assert_redacted({"draft": draft, "provenance": provenance})


@pytest.mark.parametrize(
    "gateway_behavior",
    [
        {"status": "success", "provider": "openai", "model": "test", "text": "not json"},
        TimeoutError(SECRET),
    ],
)
def test_project_outreach_never_marks_invalid_or_failed_output_as_llm_used(
    monkeypatch: pytest.MonkeyPatch,
    gateway_behavior: dict[str, Any] | Exception,
) -> None:
    monkeypatch.setattr(
        project_outreach,
        "_load_creators",
        lambda _ids: ([{"id": 1, "display_name": "Creator", "handle": "creator", "platform": "youtube"}], []),
    )

    def fake_invoke(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if isinstance(gateway_behavior, Exception):
            raise gateway_behavior
        return gateway_behavior

    monkeypatch.setattr(project_outreach.llm_gateway, "invoke", fake_invoke)

    result = project_outreach.generate_outreach([1], brief={"query_text": "camera reviewer"})

    assert result["ok"] is True
    assert result["llm_used"] is False
    assert result["messages"][0]["personalized"] is False
    assert result["reason"] == "llm_unavailable_used_template"
    _assert_redacted(result)


def test_outreach_optimize_provider_exception_returns_original_non_ready_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.platform import llm_gateway

    monkeypatch.setattr(
        llm_gateway,
        "invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )
    result = vkpi_kol_pool_jobs.optimize_kol_outreach(
        {"subject": "Original", "body": "Original body", "kol_pool_id": 1},
        staff={"id": 3},
    )

    assert result == {
        "ok": False,
        "reason": "outreach_provider_unavailable",
        "retryable": True,
        "subject": "Original",
        "body": "Original body",
        "model": "",
    }
    _assert_redacted(result)


def test_smart_search_and_url_routes_map_runtime_errors_to_stable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def direct_call(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(vkpi_kol_pool_search, "run_in_threadpool", direct_call)
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )
    with pytest.raises(HTTPException) as search_error:
        asyncio.run(
            vkpi_kol_pool_search.smart_kol_search(
                {"input": "camera reviewer", "mode": "text", "create_session": False},
                staff={"id": 3},
            )
        )

    assert search_error.value.status_code == 503
    assert search_error.value.detail["reason"] == "kol_search_unavailable"
    _assert_redacted(search_error.value.detail)

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )
    with pytest.raises(HTTPException) as url_error:
        vkpi_kol_pool_search.dry_run_kol_url_deep_crawl(
            {"url": "https://www.youtube.com/watch?v=test"},
            staff={"id": 3},
        )

    assert url_error.value.status_code == 503
    assert url_error.value.detail["reason"] == "url_deep_crawl_unavailable"
    _assert_redacted(url_error.value.detail)


def test_discovery_provider_failure_isolated_without_raw_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_search(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(profile_discovery_provider, "search_platform_content", broken_search)
    result = asyncio.run(
        profile_discovery_provider.discover_new_creators(
            query_text="camera reviewer",
            platforms=["youtube"],
            limit=5,
            per_platform_limit=5,
        )
    )

    assert result["status"] != "ready"
    _assert_redacted(result)
    assert "platform_search_unavailable" in _serialized(result)


def test_url_analysis_scrape_only_result_is_partial_not_analyzed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_scrape(_url: str) -> dict[str, Any]:
        return {
            "scraped_ok": True,
            "platform": "YouTube",
            "scraper": "fixture",
            "title": "Camera review",
            "metrics": {"views": 100},
        }

    async def fake_analysis(**_kwargs: Any) -> dict[str, Any]:
        return {"analyzed": False, "status": "failed", "error": SECRET, "layers_used": []}

    monkeypatch.setattr(content_analyzer, "scrape_url", fake_scrape)
    monkeypatch.setattr(content_analyzer, "analyze_url_content_smart", fake_analysis)

    result = asyncio.run(content_analyzer.analyze_kol_url_standalone("https://example.test/video"))

    assert result["status"] == "partial"
    assert result["analysis_status"] == "unavailable"
    assert result["analysis_reason"] == "provider_unavailable"
    assert result["analysis"].get("analyzed") is False
    assert "error" not in result["analysis"]
    assert result["steps"][2]["status"] == "skipped"
    _assert_redacted(result)


@pytest.mark.parametrize("route_name", ["platform_search", "url_analysis"])
def test_kol_ops_provider_exceptions_are_structured_503(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    async def broken(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(SECRET)

    if route_name == "platform_search":
        class BrokenQueue:
            async def enqueue(self, *_args: Any, **_kwargs: Any) -> str:
                raise RuntimeError(SECRET)

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(job_queue=BrokenQueue()))
        )
        call = kol_ops.search_kol_platform(
            request,
            {"query": "camera", "platform": "youtube"},
            staff={"id": 3},
        )
        expected = "platform_search_unavailable"
    else:
        monkeypatch.setattr(kol_ops, "analyze_kol_url_standalone", broken)
        call = kol_ops.analyze_kol_url_tool({"url": "https://example.test/video"}, staff={"id": 3})
        expected = "url_analysis_unavailable"

    with pytest.raises(HTTPException) as raised:
        asyncio.run(call)

    assert raised.value.status_code == 503
    assert raised.value.detail["reason"] == expected
    assert raised.value.detail["retryable"] is True
    _assert_redacted(raised.value.detail)


def test_url_video_metadata_exception_uses_stable_public_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        url_deep_crawl,
        "_fetch_video_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )
    classified = url_deep_crawl.ClassifiedUrl(
        original_url="https://www.youtube.com/watch?v=test",
        normalized_url="https://www.youtube.com/watch?v=test",
        url_type="video",
        platform="youtube",
        handle="",
        channel_id="",
        video_id="test",
        confidence="high",
    )

    flow, matches = url_deep_crawl._video_flow_plan(classified, [])

    assert matches == []
    assert flow["status"] == "metadata_failed"
    assert flow["metadata_error"] == "video_metadata_unavailable"
    _assert_redacted(flow)


def test_vector_operation_errors_are_deterministic_and_redacted() -> None:
    request = httpx.Request("GET", "https://qdrant.invalid/collections/test")
    response = httpx.Response(503, json={"status": {"error": SECRET}}, request=request)
    http_error = httpx.HTTPStatusError("upstream failed", request=request, response=response)

    assert vector_memory._operation_error("qdrant_readiness", http_error) == "qdrant_readiness: HTTP 503"
    assert vector_memory._operation_error("qdrant_search", RuntimeError(SECRET)) == "qdrant_search: unavailable"
    assert vector_memory._operation_error("embedding", TimeoutError(SECRET)) == "embedding timeout"
    _assert_redacted(
        {
            "http": vector_memory._operation_error("qdrant_readiness", http_error),
            "runtime": vector_memory._operation_error("qdrant_search", RuntimeError(SECRET)),
            "timeout": vector_memory._operation_error("embedding", TimeoutError(SECRET)),
        }
    )


def test_ai_today_read_failure_is_invalid_not_ready_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )

    result = ai_today.get_ai_today_hot()

    assert result == {
        "available": False,
        "status": "invalid",
        "result_status": "invalid",
        "is_ready": False,
        "reason": "read_error",
    }
    _assert_redacted(result)
