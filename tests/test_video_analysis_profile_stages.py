"""final_v1 视频链阶段埋点(零 LLM 成本)+ 剖面脚本口径。

覆盖:分析器子进程里 stage_timings_ms / youtube_direct / download_diagnostics 的落点;
worker 合并阶段写进 cost.stage_timings_ms 与 apify_jobs.payload.diagnostics(成功与失败两条路);
剖面脚本 build_report 对埋点行 / 旧行 / 失败行的汇总。全程 fake,不调 Gemini/Apify。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ai.analyzers import gemini_video, gemini_video_youtube
from app.workers import apify_jobs_worker_gemini_stages as stages


YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"
SIX_LAYERS = {
    "layer1_visual_content": {
        "content_summary": "A creator compares autofocus and flare performance.",
        "scene_timeline": [{"timestamp": "00:04", "what": "Lens close-up followed by autofocus samples."}],
        "evidence": {"timestamps": ["00:04 lens close-up"]},
        "brand_product_evidence": {
            "viltrox_status": "unknown",
            "inspection_complete": True,
            "checked_modalities": ["visual", "audio"],
            "viltrox_evidence": [],
            "viltrox_products": [],
            "competitors": [],
        },
    },
    "layer2_viewer_emotion": {"hook_analysis": "x"},
    "layer3_three_values": {"entertainment": 7},
    "layer4_attribution": {"why_it_worked": "y"},
    "layer5_recommendations": {"next": ["z"]},
    "layer6_flags_and_scores": {
        "risk_flags": [],
        "scores": {
            key: {"score": None, "confidence": 0.0, "rationale": "Evidence unavailable."}
            for key in (
                "content_quality_score",
                "viewer_heart_score",
                "channel_value_score",
                "asset_reuse_score",
                "product_proof_score",
                "marketing_value_score",
            )
        },
        "final_verdict": "Useful category evidence for creator discovery.",
        "key_hook": "Autofocus evidence needs closer review.",
    },
}


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.text = json.dumps(payload)
        self.model_version = "gemini-3.6-flash"
        self.usage_metadata = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15)


def _youtube_env(monkeypatch: pytest.MonkeyPatch, generate: Any) -> None:
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "gemini_client", object())
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video_youtube, "fetch_youtube_subtitles", lambda _url: "[00:01] hi")
    monkeypatch.setattr(gemini_video, "_strict_generate_content", generate)
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {"enabled": False}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)
    monkeypatch.setattr(gemini_video, "final_v1_gemini_models", lambda *_args: ["gemini-3.6-flash"])
    monkeypatch.setattr(
        gemini_video_youtube.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("yt-dlp must not run in this test")),
    )


def _run_youtube() -> dict[str, Any]:
    return asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(
            YOUTUBE_URL,
            "demo",
            schema_version="final_v1",
            models=["gemini-3.6-flash"],
        )
    )


def test_youtube_direct_success_records_stage_timings_and_direct_diag(monkeypatch: pytest.MonkeyPatch) -> None:
    _youtube_env(monkeypatch, lambda **_kwargs: _Resp(SIX_LAYERS))
    result = _run_youtube()
    assert result["analyzed"] is True
    timings = result["stage_timings_ms"]
    assert set(timings) >= {"subtitles", "youtube_direct"}
    assert all(isinstance(v, int) and v >= 0 for v in timings.values())
    assert result["subtitle_chars"] == len("[00:01] hi")
    assert result["requested_model_chain"] == ["gemini-3.6-flash"]
    assert result["selected_model"] == "gemini-3.6-flash"
    assert result["provider_reported_model"] == "gemini-3.6-flash"
    direct = result["youtube_direct"]
    assert direct["attempted"] is True and direct["success"] is True
    assert direct["attempts"][0]["model"] == "gemini-3.6-flash" and direct["attempts"][0]["ok"] is True
    assert direct["fallback_reason"] == ""


def test_youtube_direct_failure_persists_error_then_download_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _generate(**_kwargs: Any) -> Any:
        calls.append("generate")
        raise RuntimeError("400 INVALID_ARGUMENT: unsupported youtube url")

    _youtube_env(monkeypatch, _generate)
    monkeypatch.setattr(
        gemini_video_youtube.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr=b"ERROR: Sign in to confirm you're not a bot", stdout=b""),
    )
    result = _run_youtube()
    assert result["analyzed"] is False
    direct = result["youtube_direct"]
    assert direct["success"] is False
    assert direct["attempts"][0]["ok"] is False
    assert "unsupported youtube url" in direct["attempts"][0]["error"]
    assert "unsupported youtube url" in direct["fallback_reason"]
    dl = result["download_diagnostics"]
    assert dl["returncode"] == 1 and dl["bytes"] == 0
    assert "not a bot" in dl["stderr_tail"]
    assert "download" in result["stage_timings_ms"]
    assert result["error"] == "yt-dlp video download failed for Gemini analysis"
    assert calls == ["generate"]


def test_local_analyzer_records_upload_wait_generation_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 4096)
    deleted: list[str] = []

    class _Files:
        def upload(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(name="files/abc", uri="https://files/abc", state=SimpleNamespace(name="ACTIVE"))

        def get(self, *, name: str) -> Any:
            return SimpleNamespace(name=name, uri="https://files/abc", state=SimpleNamespace(name="ACTIVE"))

        def delete(self, *, name: str) -> None:
            deleted.append(name)

    monkeypatch.setattr(gemini_video, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video, "gemini_client", SimpleNamespace(files=_Files()))
    monkeypatch.setattr(gemini_video, "genai_types", SimpleNamespace(Part=SimpleNamespace(from_uri=lambda **kw: kw)))
    monkeypatch.setattr(gemini_video, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video, "_strict_generate_content", lambda **_kwargs: _Resp(SIX_LAYERS))
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {"enabled": False}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)
    result = asyncio.run(
        gemini_video.analyze_local_video_with_gemini(
            str(video), "demo", schema_version="final_v1", models=["gemini-3.6-flash"]
        )
    )
    assert result["analyzed"] is True
    assert set(result["stage_timings_ms"]) >= {"upload", "file_active_wait", "cache_setup", "generation", "cleanup"}
    assert result["local_video_bytes"] == 4096
    assert result["requested_model_chain"] == ["gemini-3.6-flash"]
    assert result["selected_model"] == "gemini-3.6-flash"
    assert result["provider_reported_model"] == "gemini-3.6-flash"
    assert deleted == ["files/abc"]


def test_merged_stage_timings_and_redaction() -> None:
    raw = {
        "stage_timings_ms": {"subtitles": 1200, "youtube_direct": "30000", "bad": "x"},
        "worker_stage_timings_ms": {"analyzer_subprocess": 33000},
    }
    merged = stages.merged_stage_timings(raw, {"persist": 40})
    assert merged == {"subtitles": 1200, "youtube_direct": 30000, "analyzer_subprocess": 33000, "persist": 40}
    diag = stages.analyzer_failure_diagnostics(
        {
            "method": "gemini_youtube",
            "youtube_direct": {"attempted": True, "attempts": [{"error": "proxy http://user:secret@gate:1"}]},
            "download_diagnostics": {"stderr_tail": "Authorization: Bearer abc123"},
        },
        platform="youtube",
        error="boom",
    )
    assert "secret" not in json.dumps(diag)
    assert "abc123" not in json.dumps(diag)
    assert diag["error"] == "boom" and diag["platform"] == "youtube"


class _DiagConn:
    """只接 UPDATE apify_jobs ... diagnostics 的假连接(其余 SQL 记录下来供断言)。"""

    def __init__(self) -> None:
        self.sql: list[tuple[str, tuple[Any, ...]]] = []
        self._cur = self

    def transaction(self) -> Any:
        conn = self

        class _Tx:
            def __enter__(self_inner) -> None:
                return None

            def __exit__(self_inner, *_args: Any) -> None:
                return None

        return _Tx()

    def cursor(self, *_args: Any, **_kwargs: Any) -> Any:
        return self

    def __enter__(self) -> "_DiagConn":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self) -> tuple[int]:
        return (4242,)


def test_persist_job_stage_diagnostics_merges_into_payload() -> None:
    conn = _DiagConn()
    ok = stages.persist_job_stage_diagnostics(conn, job_id=7, stage_timings_ms={"persist": 12}, extra={"platform": "youtube"})
    assert ok is True
    sql, params = conn.sql[-1]
    assert "UPDATE apify_jobs" in sql and "'diagnostics'" in sql
    payload = json.loads(params[0])
    assert payload["stage_timings_ms"] == {"persist": 12}
    assert payload["platform"] == "youtube"
    assert params[1] == 7


def test_persist_job_stage_diagnostics_never_raises() -> None:
    class _Broken:
        def transaction(self) -> Any:
            raise RuntimeError("db down")

    assert stages.persist_job_stage_diagnostics(_Broken(), job_id=1, stage_timings_ms={}) is False


def _worker_env(monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]) -> Any:
    from app.workers import apify_jobs_worker_gemini as gemini
    from app.workers import apify_jobs_worker_paid_scope

    monkeypatch.setattr(
        gemini,
        "_load_video_evidence",
        lambda _conn, _target_id: {"id": 701, "kol_pool_id": 88, "content_url": YOUTUBE_URL, "title": "demo"},
    )
    monkeypatch.setattr(gemini, "_run_gemini_analyzer_with_timeout", lambda *_a, **_k: raw)
    monkeypatch.setattr(gemini, "_authoritative_gemini_cost", lambda *_a, **_k: (0.01, "test", 10, 10))
    monkeypatch.setattr(gemini, "_record_gemini_cost", lambda **_k: {"recorded": True})
    monkeypatch.setattr(
        apify_jobs_worker_paid_scope,
        "revalidate_paid_job_scope",
        lambda *_a, **_k: ("video_analysis", "", {"id": 1}),
    )
    monkeypatch.setattr(gemini, "_sync_deep_analysis_result_from_cache", lambda *_a, **_k: {"status": "ready", "kol_pool_id": 88})
    monkeypatch.setattr(gemini, "_enqueue_account_dossier_extract_after_final_v1", lambda *_a, **_k: None)
    monkeypatch.setattr(gemini, "_enqueue_content_fit_after_final_v1", lambda *_a, **_k: None)
    monkeypatch.setattr(gemini, "extract_lens_evidence_after_final_v1", lambda **_k: None)
    monkeypatch.setattr(gemini, "_search_session_analysis_summary_from_result", lambda **_k: None)
    monkeypatch.setattr(gemini, "_sync_search_session_job", lambda *_a, **_k: None)
    return gemini


def _authorized_execution(model: str) -> dict[str, Any]:
    snapshot = {
        "binding": f"google/{model}",
        "model": model,
        "execution_class": "production",
        "authorization_scope": "production",
        "evaluation_only": False,
        "production_authorized": True,
        "model_readiness_status": "production_ready",
        "execution_authorization_at_run": {
            "scope": "execution_time_snapshot",
            "authorized": True,
            "production_authorized": True,
            "evaluation_only": False,
            "status": "operationally_authorized",
            "source": "test",
            "temporary": False,
        },
        "signed_readiness_at_run": {
            "scope": "execution_time_snapshot",
            "production_ready": True,
            "status": "production_ready",
            "claim_status": "descriptive_only",
            "evidence_source": "test",
        },
    }
    return {
        **snapshot,
        "requested_model_chain": [model],
        "ready_model_chain": [model],
        "execution_authorizations_by_model": {model: snapshot},
        "execution_authorizations_by_binding": {f"google/{model}": snapshot},
    }


def test_worker_success_path_writes_stage_timings_to_cache_and_job_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import apify_jobs_worker

    raw = {
        "analyzed": True,
        "status": "completed",
        "method": f"gemini_direct_{apify_jobs_worker.WORKER_GEMINI_MODEL}",
        "model": apify_jobs_worker.WORKER_GEMINI_MODEL,
        "selected_model": apify_jobs_worker.WORKER_GEMINI_MODEL,
        "provider_reported_model": apify_jobs_worker.WORKER_GEMINI_MODEL,
        "video_analysis_final_v1": SIX_LAYERS,
        "stage_timings_ms": {"subtitles": 900, "youtube_direct": 30000},
        "youtube_direct": {"attempted": True, "success": True, "attempts": [], "fallback_reason": ""},
    }
    gemini = _worker_env(monkeypatch, raw)
    lens_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        gemini,
        "extract_lens_evidence_after_final_v1",
        lambda **kwargs: lens_calls.append(kwargs),
    )
    conn = _DiagConn()
    gemini._process_gemini_video(
        conn,
        {"id": 99},
        {
            "target_type": "video",
            "target_id": "701",
            "derive_method": "video_analysis_final_v1",
            "_llm_execution": _authorized_execution(
                apify_jobs_worker.WORKER_GEMINI_MODEL
            ),
        },
        0.01,
    )
    inserts = [p for s, p in conn.sql if "INSERT INTO vkpi_analysis_cache" in s]
    assert len(inserts) == 1
    shaped = json.loads(inserts[0][4])
    # 六层契约不变,只在 cost 下追加阶段耗时
    assert set(SIX_LAYERS) <= set(shaped)
    assert shaped["layer6_flags_and_scores"]["final_verdict"] == SIX_LAYERS["layer6_flags_and_scores"]["final_verdict"]
    timings = shaped["cost"]["stage_timings_ms"]
    assert timings["subtitles"] == 900 and timings["youtube_direct"] == 30000
    assert "analyzer_subprocess" in timings and "cost_record" in timings
    diag_updates = [p for s, p in conn.sql if "'diagnostics'" in s]
    assert len(diag_updates) == 1
    diag = json.loads(diag_updates[0][0])
    assert diag["outcome"] == "done"
    assert {"persist", "followups", "analyzer_subprocess"} <= set(diag["stage_timings_ms"])
    assert diag["youtube_direct"]["success"] is True
    assert diag_updates[0][1] == 99
    assert lens_calls == [
        {
            "cache_id": 4242,
            "derive_method": "video_analysis_final_v1",
            "job_id": 99,
        }
    ]


def test_worker_failure_path_persists_direct_and_download_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {
        "analyzed": False,
        "method": "gemini_youtube",
        "error": "yt-dlp video download failed for Gemini analysis",
        "stage_timings_ms": {"subtitles": 800, "youtube_direct": 2100, "download": 41000},
        "youtube_direct": {"attempted": True, "success": False, "attempts": [{"model": "m", "ok": False, "error": "400 bad url", "elapsed_ms": 2100}], "fallback_reason": "400 bad url"},
        "download_diagnostics": {"returncode": 1, "stderr_tail": "Sign in to confirm you're not a bot", "bytes": 0},
    }
    gemini = _worker_env(monkeypatch, raw)
    conn = _DiagConn()
    with pytest.raises(RuntimeError, match="yt-dlp video download failed"):
        gemini._process_gemini_video(
            conn,
            {"id": 100},
            {"target_type": "video", "target_id": "701", "derive_method": "video_analysis_final_v1", "_llm_execution": {}},
            0.01,
        )
    assert not any("INSERT INTO vkpi_analysis_cache" in s for s, _ in conn.sql)
    diag_updates = [p for s, p in conn.sql if "'diagnostics'" in s]
    assert len(diag_updates) == 1
    diag = json.loads(diag_updates[0][0])
    assert diag["outcome"] == "failed"
    assert diag["youtube_direct"]["fallback_reason"] == "400 bad url"
    assert "not a bot" in diag["download_diagnostics"]["stderr_tail"]
    assert diag["stage_timings_ms"]["download"] == 41000


def test_profile_build_report_buckets_stages_and_platforms() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from ops import profile_video_analysis as profile

    rows = [
        {
            "platform": "youtube",
            "method": "gemini_direct_gemini-3.6-flash",
            "latency_ms": 40000.0,
            "stage_timings": {"subtitles": 1000, "youtube_direct": 30000, "cost_record": 200},
            "youtube_direct": {"attempted": True, "success": True, "attempts": []},
            "job_id": 1,
            "job_diagnostics": {"total_ms": 41000, "stage_timings_ms": {"persist": 100, "followups": 300}},
        },
        {
            "platform": "youtube",
            "method": "gemini_fileapi_gemini-3.6-flash",
            "latency_ms": 200000.0,
            "stage_timings": {"youtube_direct": 2000, "download": 120000, "upload": 30000, "generation": 40000},
            "youtube_direct": {"attempted": True, "success": False, "fallback_reason": "400 bad url", "attempts": [{"ok": False, "error": "400 bad url"}]},
            "job_id": 2,
            "job_diagnostics": None,
        },
        {
            # 旧行:无埋点,gemini_call 靠 llm_calls 回填
            "platform": "instagram",
            "method": "gemini_local_fileapi_gemini-3.6-flash",
            "latency_ms": 60000.0,
            "stage_timings": None,
            "youtube_direct": None,
            "job_id": 3,
            "job_diagnostics": None,
        },
    ]
    llm_calls = {3: [{"latency_ms": 25000, "status": "success"}]}
    failures = [
        {
            "status": "failed",
            "category": "download",
            "error_head": "yt-dlp video download failed",
            "platform": "youtube",
            "diagnostics": {"youtube_direct": {"attempted": True, "success": False, "fallback_reason": "400 bad url"}, "download_diagnostics": {"stderr_tail": "not a bot"}},
        }
    ]
    report = profile.build_report(rows, llm_calls, failures, limit=10, days=30)
    fam = report["stages_p50_p95"]["families"]
    assert fam["download"]["n"] == 2 and fam["upload"]["n"] == 1
    assert fam["gemini_call"]["n"] == 4  # youtube_direct×2 + generation×2(含回填)
    assert fam["persist"]["n"] == 3
    assert report["by_platform"]["youtube"]["total"]["n"] == 2
    assert report["by_platform"]["youtube"]["paths"] == {"youtube_direct": 1, "youtube_fileapi_fallback": 1}
    assert report["by_path"]["youtube_fileapi_fallback"]["p50_ms"] == 200000
    assert report["youtube_direct"] == {
        "attempted": 2,
        "success": 1,
        "hit_rate": 0.5,
        "fallback_reasons": [("400 bad url", 1)],
        "attempt_errors": [("400 bad url", 1)],
        "download_stderr_on_ready_rows": [],
    }
    assert report["failures"]["youtube_direct_fallback_reasons"] == [("400 bad url", 1)]
    assert report["failures"]["download_stderr_tails"] == [("not a bot", 1)]
    assert report["instrumentation_coverage"]["instrumented_rows"] == 2
    assert report["instrumentation_coverage"]["legacy_rows_gemini_backfilled_from_llm_calls"] == 1
    assert profile.percentile([1, 2, 3, 4], 0.5) == 2.5
