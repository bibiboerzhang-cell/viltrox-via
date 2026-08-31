"""test_cc52_shell_refactor_characterization 续篇(后半)。"""
from tests.test_cc52_shell_refactor_characterization_support import (  # noqa: F401
    ADVANCED_MODEL_MODE,
    Any,
    DETERMINISTIC_DESCRIPTIVE_MODE,
    NormalizedRequest,
    OPENAI_MODEL,
    Path,
    QueryScope,
    QueryWindow,
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
    VideoCacheCancelled,
    _BudgetGuard,
    _FakeResponse,
    _LedgerConn,
    _NOW,
    _OneRow,
    _Resolved,
    _RouteConn,
    _SOW_PLACEHOLDER,
    _creators,
    _full_routes,
    _good_sources,
    _llm_payload,
    _model_readiness,
    _patch_gateway,
    _patch_policy_env,
    _patch_pool_env,
    _patch_video_env,
    _ready_payload,
    _request,
    cache,
    datetime,
    freshness_status,
    handlers,
    hashlib,
    json,
    ledger,
    llm_gateway,
    llm_production,
    model_policy,
    outreach,
    pytest,
    timezone,
)




def test_policy_readiness_blockers_invalid_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch)
    payload = _ready_payload()
    payload["blockers"] = "oops"
    decision = model_policy.evaluate_report_model_policy(payload, _good_sources())
    assert decision.blockers == (
        "data_readiness:blockers_invalid",
        "data_readiness:not_ready_or_claimable",
    )


def test_policy_sources_container_and_item_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch)
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), "nope")
    assert "sources:invalid_container" in decision.blockers
    assert "sources:missing" in decision.blockers
    assert decision.checks["sources"] == {"passed": False, "required_count": 0, "items": []}

    bad_items = [
        {"key": "", "observed": 1, "minimum": 1, "source_count": 1},
        {"key": "dup", "observed": 5, "minimum": 1, "source_count": 1},
        {"key": "dup", "observed": 0, "minimum": 3, "source_count": 0, "data_status": "sample"},
    ]
    decision2 = model_policy.evaluate_report_model_policy(_ready_payload(), bad_items)
    assert "sources:item_0:invalid:report source key is required" in decision2.blockers
    assert "sources:dup:duplicate" in decision2.blockers
    assert "sources:dup:untrusted_or_missing" in decision2.blockers
    assert "samples:dup:observed<3" in decision2.blockers
    items = decision2.checks["sources"]["items"]
    assert len(items) == 2
    assert items[1]["status"] == "blocked"
    assert items[1]["duplicate"] is True


def test_policy_registry_not_selectable_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch, selectable=False)
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert f"model_registry:{REPORT_PRIMARY_MODEL}:not_selectable" in decision.blockers
    assert f"model_registry:{REPORT_CHALLENGER_MODEL}:not_selectable" in decision.blockers
    assert decision.checks["model_registry"]["passed"] is False


def test_policy_static_runtime_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(
        monkeypatch, static_blocker="missing_pricing", availability="not_checked"
    )
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert decision.provider_calls_allowed is False
    assert f"model_runtime:{REPORT_PRIMARY_MODEL}:missing_pricing" in decision.blockers
    runtime = decision.checks["model_runtime"]
    assert runtime["passed"] is False
    assert runtime["probe_ready"] is False
    item = runtime["items"][REPORT_PRIMARY_MODEL]
    assert item["gate_reason"] == "missing_pricing"
    assert item["passed"] is False
    assert item["runtime_probe_ready"] is False
    assert item["legacy_runtime_execution_gate"] == "not_configured"


def test_policy_readiness_pending_blocked_unless_probe_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_policy_env(
        monkeypatch, production_ready=False, failure_reasons=["probe_missing", "eval_missing"]
    )
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert decision.provider_calls_allowed is False
    assert (
        f"model_readiness:{REPORT_PRIMARY_MODEL}:probe_missing,eval_missing"
        in decision.blockers
    )

    probe = model_policy.evaluate_report_model_policy(
        _ready_payload(), _good_sources(), allow_runtime_probe=True
    )
    assert probe.provider_calls_allowed is True
    assert probe.mode == ADVANCED_MODEL_MODE
    assert probe.claim_level == "runtime_verification_pending"
    assert probe.blockers == ()
    assert probe.checks["model_runtime"]["passed"] is False
    assert probe.checks["model_runtime"]["probe_authorized"] is True
    assert probe.checks["model_runtime"]["probe_ready"] is True


def test_policy_explicit_evidence_argument_marks_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_policy_env(monkeypatch)
    decision = model_policy.evaluate_report_model_policy(
        _ready_payload(), _good_sources(), readiness_evidence={}
    )
    assert decision.checks["model_runtime"]["evidence_source"] == {
        "source": "explicit_argument",
        "parsed": True,
    }


def test_cache_video_guard_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cache.cache_video_for_item("", "vid", "u") == {
        "status": "failed",
        "cached": False,
        "platform": "",
        "video_id": "vid",
        "reason": "platform_video_id_required",
    }
    assert cache.cache_video_for_item("YouTube ", "vid", "u") == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "youtube_embed_ok",
        "platform": "youtube",
        "video_id": "vid",
    }
    assert cache.cache_video_for_item("myspace", "vid", "u") == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "platform_not_supported",
        "platform": "myspace",
        "video_id": "vid",
    }


def test_cache_video_existing_hit_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path, existing="/api/vkpi-media/video-cache/abc")
    monkeypatch.setattr(
        cache,
        "_read_json_file",
        lambda p: {"size_bytes": 111, "digest": "d" * 64, "storage_backend": "r2", "r2_key": "k1"},
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "cached",
        "cached": True,
        "platform": "instagram",
        "video_id": "vid1",
        "cached_url": "/api/vkpi-media/video-cache/abc",
        "size_bytes": 111,
        "digest": "d" * 64,
        "storage_backend": "r2",
        "r2_key": "k1",
    }


def test_cache_video_existing_hit_with_empty_sidecar_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path, existing="/u")
    monkeypatch.setattr(cache, "_read_json_file", lambda p: None)
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["size_bytes"] == 0
    assert result["storage_backend"] == "local"
    assert result["digest"] == ""
    assert result["r2_key"] == ""


def test_cache_video_blocked_state_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_video_env(
        monkeypatch,
        tmp_path,
        state={
            "blocked": True,
            "skip_reason": "rf",
            "reason": "rr",
            "error": "ee",
            "resolver": "res",
            "retry_after_seconds": 30,
        },
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "platform": "instagram",
        "video_id": "vid1",
        "skip_reason": "rf",
        "reason": "rr",
        "error": "ee",
        "resolver": "res",
        "retry_after_seconds": 30,
    }


def test_cache_video_blocked_state_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path, state={"blocked": True})
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["skip_reason"] == "recent_failed_source"
    assert result["reason"] == "recent_failed_source"
    assert result["error"] == ""
    assert result["resolver"] == ""
    assert result["retry_after_seconds"] == 0


def test_cache_video_page_url_delegates_to_ytdlp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(
        monkeypatch, tmp_path, normalized=None, page_url="https://instagram.com/p/x"
    )
    seen: dict[str, Any] = {}

    def fake_ytdlp(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "cached", "via": "ytdlp"}

    monkeypatch.setattr(cache, "_cache_video_for_item_via_ytdlp", fake_ytdlp)
    cb = object()
    cc = None
    result = cache.cache_video_for_item(
        "instagram", "vid1", "https://instagram.com/p/x", timeout=44, progress_callback=cb
    )
    assert result == {"status": "cached", "via": "ytdlp"}
    assert seen == {
        "platform_key": "instagram",
        "video_key": "vid1",
        "page_url": "https://instagram.com/p/x",
        "force_refresh": False,
        "timeout": 44,
        "progress_callback": cb,
        "cancel_check": cc,
    }


def test_cache_video_not_allowlisted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_video_env(monkeypatch, tmp_path, normalized=None, page_url="")
    result = cache.cache_video_for_item("instagram", "vid1", "https://elsewhere/v")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "not_allowlisted",
        "platform": "instagram",
        "video_id": "vid1",
    }


def test_cache_video_head_too_large(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, head=(5000, "video/mp4"), max_bytes=1000)
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "too_large",
        "platform": "instagram",
        "video_id": "vid1",
        "content_length": 5000,
    }
    assert captured["failures"] == [
        {
            "platform_key": "instagram",
            "video_key": "vid1",
            "source_url": "https://cdn.example.com/v.mp4",
            "status": "skipped",
            "reason": "too_large",
            "retryable": False,
            "metadata": {"content_length": 5000, "max_file_bytes": 1000},
        }
    ]


def test_cache_video_gc_full(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_video_env(monkeypatch, tmp_path, gc={"free_bytes": 1})
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "global_cache_full",
        "platform": "instagram",
        "video_id": "vid1",
        "gc": {"free_bytes": 1},
    }


def test_cache_video_reuses_local_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    captured["cache_path"].write_bytes(b"z" * 7)
    captured["content_type_path"].write_text("video/mp4", encoding="utf-8")
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["status"] == "cached"
    assert result["cached"] is True
    assert result["size_bytes"] == 7
    assert result["content_type"] == "video/mp4"
    assert result["cached_url"] == f"/api/vkpi-media/video-cache/{'f' * 64}"
    assert result["storage_backend"] == "local"
    assert result["r2_key"] == ""
    assert result["updated_at"] == "2026-08-30T00:00:00Z"
    assert result["gc"] == {"free_bytes": 10_000_000}
    assert len(captured["r2_calls"]) == 1
    assert captured["r2_calls"][0]["media_kind"] == "video"
    assert captured["assets"][0]["checksum"] == "checksum-1"
    assert captured["assets"][0]["status"] == "cached"
    assert captured["assets"][0]["metadata"] == {
        "gc": {"free_bytes": 10_000_000},
        "r2_status": "disabled",
        "r2_error": None,
    }
    sidecar = json.loads(captured["sidecar_path"].read_text(encoding="utf-8"))
    assert sidecar["digest"] == "f" * 64
    assert sidecar["size_bytes"] == 7


def test_cache_video_download_success_full_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, r2={"storage_backend": "r2", "r2_key": "rk", "cache_url": "https://r2/x", "r2_status": "uploaded"})
    progress: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "video/mp4; charset=binary", "content-length": "10"},
            [b"x" * 6, b"y" * 4],
        ),
    )
    result = cache.cache_video_for_item(
        "instagram",
        "vid1",
        "https://x",
        progress_callback=lambda pct, text: progress.append((pct, text)),
    )
    assert result["status"] == "cached"
    assert result["cached"] is True
    assert result["platform"] == "instagram"
    assert result["video_id"] == "vid1"
    assert result["source_url"] == "https://cdn.example.com/v.mp4"
    assert result["digest"] == "f" * 64
    assert result["cached_url"] == "https://r2/x"
    assert result["content_type"] == "video/mp4"
    assert result["size_bytes"] == 10
    assert result["storage_backend"] == "r2"
    assert result["r2_key"] == "rk"
    assert result["updated_at"] == "2026-08-30T00:00:00Z"
    assert result["gc"] == {"free_bytes": 10_000_000}
    assert captured["cache_path"].read_bytes() == b"x" * 6 + b"y" * 4
    assert not captured["cache_path"].with_suffix(".part").exists()
    assert captured["content_type_path"].read_text(encoding="utf-8") == "video/mp4"
    assert progress == [(10, "视频缓存预检查"), (30, "下载视频缓存"), (80, "写入视频 sidecar")]
    asset = captured["assets"][0]
    assert asset["media_kind"] == "video"
    assert asset["cache_url"] == "https://r2/x"
    assert asset["metadata"]["r2_status"] == "uploaded"
    sidecar = json.loads(captured["sidecar_path"].read_text(encoding="utf-8"))
    assert sidecar["cached_url"] == "https://r2/x"


def test_cache_video_download_not_video_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse({"content-type": "text/html"}, []),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "failed",
        "cached": False,
        "platform": "instagram",
        "video_id": "vid1",
        "reason": "not_video",
    }
    assert captured["failures"][0]["reason"] == "not_video"
    assert captured["failures"][0]["status"] == "failed"
    assert captured["failures"][0]["metadata"] == {"content_type": "text/html"}


def test_cache_video_download_header_too_large(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, max_bytes=1000)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "video/mp4", "content-length": "4000"}, []
        ),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "too_large",
        "platform": "instagram",
        "video_id": "vid1",
        "content_length": 4000,
    }
    assert captured["failures"][0]["metadata"] == {
        "content_length": 4000,
        "max_file_bytes": 1000,
    }


def test_cache_video_download_midstream_too_large(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, max_bytes=1000)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "application/octet-stream"}, [b"x" * 600, b"y" * 600]
        ),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "too_large",
        "platform": "instagram",
        "video_id": "vid1",
        "content_length": 1200,
    }
    assert not captured["cache_path"].with_suffix(".part").exists()
    assert not captured["cache_path"].exists()
    assert captured["failures"][0]["metadata"] == {
        "content_length": 1200,
        "max_file_bytes": 1000,
    }


def test_cache_video_octet_stream_normalized_to_mp4(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "application/octet-stream"}, [b"ok"]
        ),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["content_type"] == "video/mp4"
    assert captured["content_type_path"].read_text(encoding="utf-8") == "video/mp4"


def test_cache_video_cancel_propagates_and_cleans_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    calls = {"n": 0}

    def cancel_check() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "video/mp4"}, [b"x" * 10, b"y" * 10]
        ),
    )
    with pytest.raises(VideoCacheCancelled):
        cache.cache_video_for_item("instagram", "vid1", "https://x", cancel_check=cancel_check)
    assert not captured["cache_path"].with_suffix(".part").exists()
    assert not captured["cache_path"].exists()


def test_cache_video_download_exception_returns_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path)

    def boom(request: Any, timeout: Any) -> Any:
        raise OSError("boom-network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "failed",
        "cached": False,
        "platform": "instagram",
        "video_id": "vid1",
        "reason": "OSError",
        "error": "boom-network",
    }
