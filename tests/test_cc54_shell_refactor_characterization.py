"""前半(共享层见 _support)。"""
from tests.test_cc54_shell_refactor_characterization_support import (  # noqa: F401
    Any,
    RUN_MARKER,
    _Result,
    _RouteConn,
    _bp_evidence,
    _day,
    _fresh_iso,
    _happy_youtube_patches,
    _hot_row,
    _patch_ai_today_read,
    _patch_brand_pulse,
    _patch_common,
    _pool_row,
    _ready_content,
    _real_get_conn,
    _run_cleanup,
    _run_insert_pool_row,
    ai_today,
    audience_stats,
    bp,
    datetime,
    ensure_competitor_relation_schema,
    ensure_vkpi_product_industry_schema,
    json,
    kol_pool,
    product_analysis,
    pytest,
    timedelta,
    timezone,
)




def test_refresh_missing_row_raises_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": []})
    _patch_common(monkeypatch, conn)
    with pytest.raises(LookupError, match="kol pool item not found"):
        audience_stats.refresh_audience_stats(7)


def test_refresh_unsupported_platform_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("twitter")]})
    _patch_common(monkeypatch, conn)
    result = audience_stats.refresh_audience_stats(7)
    assert result == {
        "status": "unsupported_platform",
        "platform": "twitter",
        "kol_pool_id": 7,
        "reason": "P0 支持 youtube/instagram/tiktok",
    }


def test_refresh_youtube_no_channel_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("youtube")]})
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(audience_stats, "_youtube_channel_ref", lambda rec: "")
    result = audience_stats.refresh_audience_stats(7)
    assert result == {"status": "skipped", "reason": "no_channel_reference", "kol_pool_id": 7}


def test_refresh_youtube_sample_error_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("youtube")]})
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(audience_stats, "_youtube_channel_ref", lambda rec: "@tester")
    monkeypatch.setattr(
        audience_stats,
        "sample_youtube_commenters",
        lambda ref, max_comments: {"status": "network_error", "reason": "proxy required"},
    )
    result = audience_stats.refresh_audience_stats(7, max_comments=99)
    assert result == {"status": "network_error", "reason": "proxy required", "kol_pool_id": 7}


def test_refresh_local_no_posts_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn(
        {
            "FROM vkpi_kol_pool WHERE id=?": [_pool_row("instagram")],
            "FROM vkpi_kol_video_evidence WHERE kol_pool_id=?": [{"n": 0}],
        }
    )
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(
        audience_stats,
        "sample_local_commenters",
        lambda kol_pool_id, conn: {"status": "ok", "comments_scanned": 3, "commenters": []},
    )
    result = audience_stats.refresh_audience_stats(7)
    assert result == {
        "status": "no_posts",
        "kol_pool_id": 7,
        "platform": "instagram",
        "comments_found": 3,
        "min_required": audience_stats.MIN_LOCAL_COMMENTS,
        "enqueued": False,
        "reason": "池内暂无该 KOL 的帖子记录,先对该 KOL 跑一次账号/视频分析再生成受众统计",
    }


def test_refresh_local_insufficient_enqueues_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn(
        {
            "FROM vkpi_kol_pool WHERE id=?": [_pool_row("tiktok")],
            "FROM vkpi_kol_video_evidence WHERE kol_pool_id=?": [{"n": 4}],
        }
    )
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(
        audience_stats,
        "sample_local_commenters",
        lambda kol_pool_id, conn: {"status": "ok", "comments_scanned": 5, "commenters": []},
    )
    from app.domains.comments import collector

    captured: list[tuple[int, dict[str, Any]]] = []
    monkeypatch.setattr(
        collector,
        "enqueue_kol_pool_comments_job",
        lambda kol_pool_id, **kwargs: captured.append((kol_pool_id, kwargs)) or {"status": "queued"},
    )
    result = audience_stats.refresh_audience_stats(7)
    assert captured == [(7, {"queue_lane": "batch"})]
    assert result == {
        "status": "pending_comments",
        "kol_pool_id": 7,
        "platform": "tiktok",
        "comments_found": 5,
        "min_required": audience_stats.MIN_LOCAL_COMMENTS,
        "enqueued": True,
        "enqueue_status": "queued",
        "reason": "本地评论不足,已入队抓评论,稍后再刷新",
    }


def test_refresh_local_insufficient_enqueue_failure_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn(
        {
            "FROM vkpi_kol_pool WHERE id=?": [_pool_row("tiktok")],
            "FROM vkpi_kol_video_evidence WHERE kol_pool_id=?": [{"n": 4}],
        }
    )
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(
        audience_stats,
        "sample_local_commenters",
        lambda kol_pool_id, conn: {"status": "ok", "comments_scanned": 5, "commenters": []},
    )
    from app.domains.comments import collector

    def _boom(kol_pool_id: int, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("queue down")

    monkeypatch.setattr(collector, "enqueue_kol_pool_comments_job", _boom)
    result = audience_stats.refresh_audience_stats(7)
    assert result["status"] == "partial"
    assert result["enqueued"] is False
    assert result["enqueue_status"] == "enqueue_failed: queue down"
    assert result["reason"] == "comment_collection_enqueue_failed"


def test_refresh_local_insufficient_without_enqueue_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn(
        {
            "FROM vkpi_kol_pool WHERE id=?": [_pool_row("tiktok")],
            "FROM vkpi_kol_video_evidence WHERE kol_pool_id=?": [{"n": 4}],
        }
    )
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(
        audience_stats,
        "sample_local_commenters",
        lambda kol_pool_id, conn: {"status": "ok", "comments_scanned": 5, "commenters": []},
    )
    result = audience_stats.refresh_audience_stats(7, enqueue_if_missing=False)
    assert result["status"] == "partial"
    assert result["enqueue_status"] == ""
    assert result["reason"] == "local_comments_insufficient"


def test_refresh_no_commenters_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("youtube")]})
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(audience_stats, "_youtube_channel_ref", lambda rec: "@tester")
    monkeypatch.setattr(
        audience_stats,
        "sample_youtube_commenters",
        lambda ref, max_comments: {"status": "ok", "comments_scanned": 12, "commenters": []},
    )
    result = audience_stats.refresh_audience_stats(7)
    assert result == {
        "status": "no_commenters",
        "kol_pool_id": 7,
        "platform": "youtube",
        "comments_scanned": 12,
        "reason": "no_commenter_identities_in_sample",
    }


def test_refresh_youtube_happy_path_payload_and_result(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("youtube")]})
    _happy_youtube_patches(monkeypatch, conn)
    result = audience_stats.refresh_audience_stats(7)

    assert result["status"] == "ok"
    assert result["kol_pool_id"] == 7 and result["platform"] == "youtube"
    assert result["sample_size"] == 2 and result["confidence"] == "low"
    assert "reason" not in result and "partial_components" not in result
    assert isinstance(result["elapsed_sec"], float)

    payload = result["audience"]
    assert payload["generated_at"] == "2026-08-30T00:00:00Z"
    assert payload["comments_scanned"] == 40
    assert payload["cache"] == {"cache_hits": 1, "inferred_fresh": 1, "cache_written": 1}
    assert payload["age_coverage"]["llm"] == {"status": "ok", "calls": 1, "people_in": 2}
    assert payload["channel_id"] == "UCabc"
    # YT 抽样带回评论 → comment_intel 走 API 样本口径,补算 reply_pct
    intel = payload["comment_intel"]
    assert intel["source"] == "youtube_api_sample"
    assert intel["engagement"]["reply_basis"] == "thread_total_reply_count"
    assert intel["engagement"]["reply_pct"] == audience_stats._pct(5, 8 + 5)
    assert payload["overlap"] == {"items": [], "self_commenters": 9}
    assert payload["audience_affinity"] == {"items": [{"channel_id": "UCother"}], "checked": 2}
    contract = payload["source_contract"]
    assert contract["contract_version"] == "audience_sources_v1"
    assert contract["profile_sample"] == {
        "source": "youtube_data_api_live_sample",
        "durable": False,
        "commenters": 2,
        "comments_scanned": 40,
    }
    assert contract["overlap"] == {
        "source": "vkpi_comments_pool_evidence",
        "durable": True,
        "commenters": 9,
    }

    # 一次写库:audience_estimated_json + updated_at,随后 commit
    update_calls = [c for c in conn.calls if "UPDATE vkpi_kol_pool SET audience_estimated_json" in c[0]]
    assert len(update_calls) == 1
    written_json, written_at, written_id = update_calls[0][1]
    assert json.loads(written_json) == payload
    assert written_at == "2026-08-30T00:00:00Z" and written_id == 7
    assert conn.commits == 1


def test_refresh_partial_when_sample_partial_and_age_error(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("youtube")]})
    _happy_youtube_patches(
        monkeypatch,
        conn,
        sample_extra={"partial": True, "reason": "quota_exhausted"},
        age_raises=True,
    )
    result = audience_stats.refresh_audience_stats(7)
    assert result["status"] == "partial"
    assert result["partial_components"] == ["comment_sample", "age_llm"]
    assert result["reason"] == "quota_exhausted,age_inference_unavailable"
    assert result["audience"]["age_coverage"]["error"] == "age blew up"
    assert result["audience"]["age_coverage"]["llm"] == {"status": "skipped", "calls": 0}


def test_refresh_local_happy_path_uses_durable_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("instagram")]})
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(
        audience_stats,
        "sample_local_commenters",
        lambda kol_pool_id, conn: {
            "status": "ok",
            "comments_scanned": 60,
            "commenters": [{"author_key": "iu1"}],
        },
    )
    monkeypatch.setattr(
        audience_stats,
        "_infer_with_cache",
        lambda conn, platform, commenters: ([{"author_key": "iu1"}], {"cache_hits": 0, "inferred_fresh": 1, "cache_written": 1}),
    )
    monkeypatch.setattr(
        audience_stats,
        "_age_ensemble",
        lambda conn, platform, inferred, llm_max_batches, allow_avatar_provider: {"llm": {"status": "skipped", "calls": 0}},
    )
    monkeypatch.setattr(
        audience_stats,
        "aggregate_audience",
        lambda kol_pool_id, inferred, conn, platform: {"sample_size": 1, "confidence": "low"},
    )
    from app.domains.kol import comment_intel as ci

    monkeypatch.setattr(ci, "comment_intel_for_kol", lambda kol_pool_id, conn: {"sample_size": 33, "source": "vkpi_comments"})
    monkeypatch.setattr(ci, "compute_audience_overlap", lambda kol_pool_id, conn: {"items": []})
    result = audience_stats.refresh_audience_stats(7)
    payload = result["audience"]
    assert result["status"] == "ok"
    assert "channel_id" not in payload and "audience_affinity" not in payload
    assert payload["comment_intel"] == {"sample_size": 33, "source": "vkpi_comments"}
    contract = payload["source_contract"]
    assert contract["profile_sample"]["source"] == "vkpi_comments_pool_evidence"
    assert contract["profile_sample"]["durable"] is True
    assert contract["comment_intelligence"] == {
        "source": "vkpi_comments",
        "durable": True,
        "comments": 33,
    }


def test_refresh_comment_intel_and_overlap_failures_degrade_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RouteConn({"FROM vkpi_kol_pool WHERE id=?": [_pool_row("youtube")]})
    _happy_youtube_patches(monkeypatch, conn)
    from app.domains.kol import comment_intel as ci

    def _intel_boom(comments: Any) -> dict[str, Any]:
        raise ValueError("intel broken")

    def _overlap_boom(kol_pool_id: int, conn: Any) -> dict[str, Any]:
        raise ValueError("overlap broken")

    monkeypatch.setattr(ci, "analyze_comments", _intel_boom)
    monkeypatch.setattr(ci, "compute_audience_overlap", _overlap_boom)
    result = audience_stats.refresh_audience_stats(7)
    payload = result["audience"]
    assert payload["comment_intel"] == {"sample_size": 0, "error": "intel broken"}
    assert payload["overlap"] == {"items": [], "error": "overlap broken"}
    # comment_intel/overlap 失败不降级整体 status
    assert result["status"] == "ok"
