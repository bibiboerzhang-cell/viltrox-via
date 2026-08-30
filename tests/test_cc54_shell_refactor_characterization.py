"""CC54 四壳函数 characterization —— 降复杂度动刀前先锁行为(改前改后必须同绿)。

覆盖四个 CC 54 的壳:
- kol.audience_stats.refresh_audience_stats(编排:抽样→推断→聚合→写库→部分降级口径)
- market.ai_today.get_ai_today_hot(读端:pipeline v1 选行 / legacy 兜底 / 不可用元数据)
- market.brand_pulse.get_brand_pulse(全池周分桶 + 趋势 + SoV/rank,纯读)
- recommendations.product_analysis.run_recommendations(run/rec/explanation/outcome 落库 + 计数器)

口径:固定输入,断言到字段/小数位;monkeypatch 只打既有门面缝(改刀后这些缝必须原样生效)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


# ── 通用 fake conn(按 SQL 片段路由) ────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _RouteConn:
    def __init__(self, routes: dict[str, list[Any]]):
        self.routes = routes
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):  # noqa: D401
        self.calls.append((sql, tuple(params)))
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])

    def commit(self) -> None:
        self.commits += 1


# ════════════════════════════════════════════════════════════════
# 1) refresh_audience_stats
# ════════════════════════════════════════════════════════════════

from app.domains.kol import audience_stats  # noqa: E402


def _pool_row(platform: str) -> dict[str, Any]:
    return {
        "id": 7,
        "platform": platform,
        "handle": "tester",
        "profile_url": "https://youtube.com/@tester",
        "raw_platform_data": "{}",
    }


def _patch_common(monkeypatch: pytest.MonkeyPatch, conn: _RouteConn) -> None:
    from app.db import connection

    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(audience_stats, "_utcnow_iso", lambda: "2026-08-30T00:00:00Z")


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


def _happy_youtube_patches(
    monkeypatch: pytest.MonkeyPatch,
    conn: _RouteConn,
    *,
    sample_extra: dict[str, Any] | None = None,
    age_raises: bool = False,
) -> dict[str, Any]:
    _patch_common(monkeypatch, conn)
    sample = {
        "status": "ok",
        "comments_scanned": 40,
        "channel_id": "UCabc",
        "commenters": [{"author_key": "u1"}, {"author_key": "u2"}],
        "comments": [{"text": "nice"}],
        "reply_total": 5,
        **(sample_extra or {}),
    }
    monkeypatch.setattr(audience_stats, "_youtube_channel_ref", lambda rec: "@tester")
    monkeypatch.setattr(audience_stats, "sample_youtube_commenters", lambda ref, max_comments: dict(sample))
    monkeypatch.setattr(
        audience_stats,
        "_infer_with_cache",
        lambda conn, platform, commenters: (
            [{"author_key": "u1"}, {"author_key": "u2"}],
            {"cache_hits": 1, "inferred_fresh": 1, "cache_written": 1},
        ),
    )
    if age_raises:
        def _age_boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("age blew up")

        monkeypatch.setattr(audience_stats, "_age_ensemble", _age_boom)
    else:
        monkeypatch.setattr(
            audience_stats,
            "_age_ensemble",
            lambda conn, platform, inferred, llm_max_batches, allow_avatar_provider: {
                "llm": {"status": "ok", "calls": 1, "people_in": 2},
                "m3": "unavailable",
                "counts": {"llm": 2},
            },
        )
    monkeypatch.setattr(
        audience_stats,
        "aggregate_audience",
        lambda kol_pool_id, inferred, conn, platform: {"sample_size": 2, "confidence": "low"},
    )
    from app.domains.kol import comment_intel as ci

    monkeypatch.setattr(
        ci,
        "analyze_comments",
        lambda comments: {"sample_size": 8, "engagement": {"like_pct": 1.0}},
    )
    monkeypatch.setattr(ci, "compute_audience_overlap", lambda kol_pool_id, conn: {"items": [], "self_commenters": 9})
    monkeypatch.setattr(
        audience_stats,
        "_yt_audience_affinity",
        lambda cids, channel_id: {"items": [{"channel_id": "UCother"}], "checked": len(cids)},
    )
    return sample


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


# ════════════════════════════════════════════════════════════════
# 2) get_ai_today_hot
# ════════════════════════════════════════════════════════════════

from app.domains.market import ai_today  # noqa: E402


def _hot_row(snapshot_date: str, content: dict[str, Any] | str, model: str = "test-model", created_at: str = "") -> dict[str, Any]:
    return {
        "snapshot_date": snapshot_date,
        "content_json": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        "model": model,
        "created_at": created_at or f"{snapshot_date}T08:00:00Z",
    }


def _ready_content(generated_at: str, *, pipeline: bool = True) -> dict[str, Any]:
    content: dict[str, Any] = {
        "headline": "今日热点标题",
        "shooting_plans": ["拍摄计划一"],
        "hot_topics": ["话题一"],
        "sources": [
            {"relation_type": "grounding", "url": "https://example.com/a", "title": "A"},
        ],
        "generated_at": generated_at,
    }
    if pipeline:
        content["provenance"] = {"pipeline": "ai_today_evidence_strategy_v1"}
    return content


def _patch_ai_today_read(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    *,
    attempt: dict[str, Any] | None = None,
    market_sources: list[dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _RouteConn({"FROM vkpi_ai_today_hot": rows}))
    monkeypatch.setattr(ai_today, "_latest_scheduler_attempt", lambda conn: dict(attempt or {}))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: list(market_sources or []))
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])


def _fresh_iso() -> str:
    return (
        datetime.now(tz=timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


def test_ai_today_no_rows_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = {"attempted_at": "2026-08-29T00:00:00Z", "status": "failed", "provider": "gemini"}
    _patch_ai_today_read(monkeypatch, [], attempt=attempt)
    result = ai_today.get_ai_today_hot()
    assert result == {
        "available": False,
        "status": "invalid",
        "result_status": "invalid",
        "is_ready": False,
        "reason": "not_generated_yet",
        "latest_attempt": attempt,
    }


def test_ai_today_pipeline_v1_ready_row_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    generated_at = _fresh_iso()
    rows = [_hot_row("2026-08-30", _ready_content(generated_at))]
    extra_source = {"url": "https://market.example.com/x", "relation_type": "market"}
    dup_source = {"url": "https://example.com/a", "relation_type": "market"}
    _patch_ai_today_read(monkeypatch, rows, market_sources=[extra_source, dup_source])
    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["status"] == "ready" and result["result_status"] == "ready"
    assert result["contract_status"] == "ready" and result["is_ready"] is True
    assert result["contract_version"] == "market_intel.v1"
    assert result["model"] == "test-model"
    assert result["snapshot_date"] == "2026-08-30"
    assert result["generated_at"] == generated_at
    assert result["grounding_status"] == "grounded"
    assert result["freshness_status"] == "fresh"
    assert "reason" not in result
    # sources = 快照 grounding 源 + market 源合并(URL 去重)
    assert [s["url"] for s in result["sources"]] == [
        "https://example.com/a",
        "https://market.example.com/x",
    ]

    content = result["content"]
    assert content["headline"] == "今日热点标题"
    assert content["status"] == "ready" and content["is_ready"] is True
    assert content["snapshot_date"] == "2026-08-30"
    assert content["grounding_status"] == "grounded"
    assert [s["url"] for s in content["evidence"]] == ["https://example.com/a"]
    assert content["recommended_videos"] == []
    assert content["validation_errors"] == []
    assert content["provenance"] == {"pipeline": "ai_today_evidence_strategy_v1"}
    # 空 attempt 不注入 latest_attempt 键
    assert "latest_attempt" not in result and "latest_attempt" not in content


def test_ai_today_newer_invalid_row_degrades_selected_older(monkeypatch: pytest.MonkeyPatch) -> None:
    generated_at = _fresh_iso()
    rows = [
        _hot_row("2026-08-30", "not-json{{"),
        _hot_row("2026-08-29", _ready_content(generated_at)),
    ]
    _patch_ai_today_read(monkeypatch, rows)
    result = ai_today.get_ai_today_hot()
    assert result["available"] is True
    assert result["snapshot_date"] == "2026-08-29"
    assert result["contract_status"] == "degraded"
    assert result["status"] == "degraded" and result["is_ready"] is False
    errors = result["content"]["validation_errors"]
    assert errors[0] == "newer_rows_rejected"
    assert "headline:missing" in errors


def test_ai_today_legacy_snapshot_fallback_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    generated_at = _fresh_iso()
    rows = [_hot_row("2026-08-30", _ready_content(generated_at, pipeline=False))]
    _patch_ai_today_read(monkeypatch, rows)
    result = ai_today.get_ai_today_hot()
    assert result["available"] is True
    assert result["reason"] == "legacy_snapshot_pre_pipeline_v1"
    assert result["contract_status"] == "degraded"
    assert result["status"] == "degraded" and result["is_ready"] is False
    content = result["content"]
    assert content["reason"] == "legacy_snapshot_pre_pipeline_v1"
    assert "legacy_snapshot:pipeline_v1_required" in content["validation_errors"]
    assert "newer_rows_rejected" in content["validation_errors"]


def test_ai_today_all_rows_invalid_unavailable_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_hot_row("2026-08-30", {"headline": 123, "shooting_plans": ["p"], "hot_topics": ["t"]})]
    attempt = {"attempted_at": "2026-08-30T01:00:00Z", "status": "failed", "provider": "gemini"}
    _patch_ai_today_read(monkeypatch, rows, attempt=attempt)
    result = ai_today.get_ai_today_hot()
    assert result["available"] is False
    assert result["reason"] == "invalid_result_contract"
    assert result["status"] == "invalid" and result["result_status"] == "invalid"
    assert result["contract_status"] == "invalid"
    assert result["is_ready"] is False
    assert result["grounding_status"] == "ungrounded"
    assert result["model"] == "test-model"
    assert result["snapshot_date"] == "2026-08-30"
    assert result["sources"] == [] and result["evidence"] == []
    assert "headline:expected_string" in result["validation_errors"]
    assert result["latest_attempt"] == attempt
    assert result["content"]["latest_attempt"] == attempt
    # metadata 与 content 同构(除 latest_attempt 注入)
    assert result["content"]["status"] == "invalid"


def test_ai_today_broken_json_rows_degrade_without_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    # 烂 JSON → content {} → 缺字段按 partial 记 → degraded(非 invalid),理由 no_grounded_latest
    rows = [_hot_row("2026-08-30", "broken}{")]
    _patch_ai_today_read(monkeypatch, rows)
    result = ai_today.get_ai_today_hot()
    assert result["available"] is False
    assert result["reason"] == "no_grounded_latest"
    assert result["status"] == "degraded" and result["result_status"] == "degraded"
    assert result["contract_status"] == "degraded"
    assert "headline:missing" in result["validation_errors"]
    assert "sources:missing" in result["validation_errors"]


def test_ai_today_grounded_missing_sources_reports_no_grounded_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _ready_content(_fresh_iso())
    content.pop("sources")
    rows = [_hot_row("2026-08-30", content)]
    _patch_ai_today_read(monkeypatch, rows)
    result = ai_today.get_ai_today_hot()
    assert result["available"] is False
    assert result["reason"] == "no_grounded_latest"
    assert result["status"] == "degraded" and result["result_status"] == "degraded"
    assert result["contract_status"] == "ready"
    assert "sources:missing" in result["validation_errors"]


def test_ai_today_read_error_returns_stable_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)

    class _BoomConn:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("db down")

    monkeypatch.setattr(ai_today, "get_conn", lambda: _BoomConn())
    monkeypatch.setattr(ai_today, "_latest_scheduler_attempt", lambda conn: {})
    assert ai_today.get_ai_today_hot() == {
        "available": False,
        "status": "invalid",
        "result_status": "invalid",
        "is_ready": False,
        "reason": "read_error",
    }


# ════════════════════════════════════════════════════════════════
# 3) get_brand_pulse
# ════════════════════════════════════════════════════════════════

from app.domains.market import brand_pulse as bp  # noqa: E402


def _bp_evidence(eid: int, title: str, day: str, *, kol_id: int | None = 1, views: int = 100) -> dict[str, Any]:
    return {
        "evidence_id": eid,
        "kol_pool_id": kol_id,
        "platform": "youtube",
        "content_url": f"https://youtu.be/v{eid}",
        "view_count": views,
        "video_title": title,
        "title_alt": "",
        "pub_day": day,
        "kol_name": f"kol-{kol_id}",
    }


def _patch_brand_pulse(
    monkeypatch: pytest.MonkeyPatch,
    evidence: list[dict[str, Any]],
    deep_rows: list[dict[str, Any]] | None = None,
) -> None:
    conn = _RouteConn(
        {
            "FROM vkpi_kol_video_evidence e": evidence,
            "FROM vkpi_analysis_cache ac": deep_rows or [],
        }
    )
    monkeypatch.setattr(bp, "get_conn", lambda: conn)
    monkeypatch.setattr(
        bp,
        "_competitor_vocab",
        lambda: {
            "sony": {"keywords": ["sony"], "priority": "p1", "category": "camera", "brand_type": "competitor"},
            "sigma": {"keywords": ["sigma"], "priority": "p2", "category": "lens", "brand_type": "competitor"},
        },
    )
    monkeypatch.setattr(bp, "_viltrox_terms", lambda: ["viltrox"])
    monkeypatch.setattr(bp, "_matcher", lambda: (lambda text, kw: str(kw).lower() in str(text or "").lower()))


def _day(offset: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=offset)).isoformat()


def test_brand_pulse_full_aggregation_golden(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = [
        # 后半窗(近 7 天):sony ×3(升温),viltrox ×1,一条无品牌
        _bp_evidence(1, "Sony a7 test", _day(1), kol_id=11, views=900),
        _bp_evidence(2, "sony fx3 rig", _day(2), kol_id=12, views=500),
        _bp_evidence(3, "SONY lens day", _day(3), kol_id=11, views=700),
        _bp_evidence(4, "Viltrox 85mm review", _day(4), kol_id=13, views=300),
        _bp_evidence(5, "vlog gear misc", _day(5), kol_id=14, views=50),
        # 前半窗(60+ 天前):sigma ×2
        _bp_evidence(6, "Sigma art lens", _day(70), kol_id=15, views=800),
        _bp_evidence(7, "sigma 24-70 field", _day(72), kol_id=16, views=600),
        # 深析文本命中(标题无词);标题与深析都空 → 不计文本行
        _bp_evidence(8, "great lens footage", _day(6), kol_id=17, views=1000),
        _bp_evidence(9, "", _day(6), kol_id=18, views=10),
        # 日期烂行/窗外行:全部跳过
        _bp_evidence(10, "sony ignored", "bad-date", kol_id=19),
        _bp_evidence(11, "sony ignored", _day(400), kol_id=19),
    ]
    deep_rows = [{"evidence_id": 8, "competitor_presence": "Sony appears at 0:31", "content_summary": ""}]
    _patch_brand_pulse(monkeypatch, evidence, deep_rows)

    result = bp.get_brand_pulse(90)

    assert result["schema_version"] == "brand_pulse_v1"
    assert result["status"] == "ok"
    assert result["window_days"] == 90
    assert result["provider_calls"] is False and result["llm_calls"] is False

    coverage = result["coverage"]
    assert coverage["videos_scanned"] == 9
    assert coverage["videos_with_text"] == 8
    assert coverage["brand_hit_videos"] == 7
    assert coverage["vocab_brands"] == 2
    assert coverage["deep_analyzed_in_window"] == 1

    brands = {item["key"]: item for item in result["brands"]}
    assert set(brands) == {"sony", "sigma"}
    sony = brands["sony"]
    assert sony["brand"] == "Sony" and sony["brand_type"] == "competitor"
    assert sony["total_videos"] == 4 and sony["kol_count"] == 3
    assert sony["prev_sum"] == 0 and sony["recent_sum"] == 4
    assert sony["trend"] == "rising"
    sigma = brands["sigma"]
    assert sigma["total_videos"] == 2 and sigma["prev_sum"] == 2 and sigma["recent_sum"] == 0
    assert sigma["trend"] == "falling"
    # 例证:按 view_count 降序,带 matched_via
    assert [ex["evidence_id"] for ex in sony["top_examples"]] == [8, 1, 3]
    assert brands["sony"]["top_examples"][0]["matched_via"] == "deep_analysis"

    viltrox = result["viltrox"]
    assert viltrox["key"] == "viltrox" and viltrox["brand"] == "Viltrox"
    assert viltrox["brand_type"] == "self" and viltrox["priority"] == "self"
    assert viltrox["total_videos"] == 1
    # SoV = 1/(1+6);rank:sony 4 > sigma 2 > viltrox 1
    assert viltrox["share_of_voice"] == round(1 / 7, 3)
    assert viltrox["rank"] == 3 and viltrox["brand_count_ranked"] == 3

    assert result["groups"]["rising"] == ["sony"]
    assert result["groups"]["falling"] == ["sigma"]
    assert result["groups"]["stable"] == []

    # 周桶:窗口逐周,空周 sparse=true
    weeks = result["weeks"]
    assert len(weeks) == len(bp._week_starts(datetime.now(timezone.utc).date() - timedelta(days=89), datetime.now(timezone.utc).date()))
    assert sum(w["videos_scanned"] for w in weeks) == 9
    assert sum(w["brand_videos"] for w in weeks) == 7
    assert coverage["sparse_weeks"] == sum(1 for w in weeks if w["sparse"])
    assert all(w["sparse"] == (w["videos_scanned"] == 0) for w in weeks)


def test_brand_pulse_no_data_and_no_signal_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_brand_pulse(monkeypatch, [])
    empty = bp.get_brand_pulse()
    assert empty["status"] == "no_data_in_window"
    assert empty["brands"] == [] and empty["viltrox"]["total_videos"] == 0
    assert empty["viltrox"]["share_of_voice"] is None and empty["viltrox"]["rank"] is None

    _patch_brand_pulse(monkeypatch, [_bp_evidence(1, "no brand words here", _day(3))])
    silent = bp.get_brand_pulse()
    assert silent["status"] == "no_brand_signal"
    assert silent["coverage"]["videos_scanned"] == 1


def test_brand_pulse_window_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_brand_pulse(monkeypatch, [])
    assert bp.get_brand_pulse(1)["window_days"] == 14
    assert bp.get_brand_pulse(9999)["window_days"] == 365
    assert bp.get_brand_pulse("abc")["window_days"] == 90  # type: ignore[arg-type]


# ════════════════════════════════════════════════════════════════
# 4) run_recommendations(hermetic sqlite 全链落库)
# ════════════════════════════════════════════════════════════════

from app.db.connection import get_conn as _real_get_conn  # noqa: E402
from app.domains.kol import pool as kol_pool  # noqa: E402
from app.domains.kol.competitor_detector import ensure_competitor_relation_schema  # noqa: E402
from app.domains.recommendations import product_analysis  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402

RUN_MARKER = "cc54-runrec-characterization"


def _run_cleanup() -> None:
    conn = _real_get_conn()
    rec_rows = conn.execute(
        "SELECT id, run_id FROM vkpi_kol_recommendations WHERE handle LIKE ?",
        (f"{RUN_MARKER}%",),
    ).fetchall()
    rec_ids = [int(row["id"]) for row in rec_rows]
    run_ids = sorted({int(row["run_id"]) for row in rec_rows if row["run_id"] is not None})
    for rec_id in rec_ids:
        conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
    for run_id in run_ids:
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,))
    pool_rows = conn.execute("SELECT id FROM vkpi_kol_pool WHERE source_ref=?", (RUN_MARKER,)).fetchall()
    for row in pool_rows:
        conn.execute("DELETE FROM vkpi_competitor_relation WHERE kol_pool_id=?", (int(row["id"]),))
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (RUN_MARKER,))
    conn.execute("DELETE FROM vkpi_product_launches WHERE name LIKE ?", (f"{RUN_MARKER}%",))
    conn.commit()
    kol_pool._clear_kol_pool_read_cache()


def _run_insert_pool_row(handle: str, *, fit_score: int, platform: str = "youtube") -> int:
    conn = _real_get_conn()
    now = "2026-08-20T10:00:00Z"
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
          (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
           followers, following, posts_count, avg_views, avg_likes, avg_comments,
           engagement_rate, viltrox_fit_score, source_type, source_ref, raw_platform_data,
           created_by_staff_id, last_seen_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"{handle}-uid",
            platform,
            handle,
            f"https://youtube.com/@{handle}",
            handle,
            "",
            f"{RUN_MARKER} camera lens review",
            "",
            250000,
            None,
            12,
            50000,
            1200,
            80,
            0.035,
            fit_score,
            "unit",
            RUN_MARKER,
            json.dumps({"videos": []}),
            None,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE handle=?", (handle,)).fetchone()["id"])


def test_run_recommendations_persists_full_chain_and_counters() -> None:
    ensure_vkpi_product_industry_schema()
    ensure_competitor_relation_schema()
    _run_cleanup()
    conn = _real_get_conn()
    try:
        high_id = _run_insert_pool_row(f"{RUN_MARKER}-high", fit_score=90)
        low_id = _run_insert_pool_row(f"{RUN_MARKER}-low", fit_score=40)
        avoid_id = _run_insert_pool_row(f"{RUN_MARKER}-avoid", fit_score=99)
        conn.execute(
            """
            INSERT INTO vkpi_competitor_relation
              (kol_pool_id, kol_entity_uid, platform, handle, display_name, competitor_brand,
               collaboration_depth, collaboration_count_90d, collaboration_count_total,
               sentiment, risk_score, risk_tier, evidence_json, evidence_post_uids_json, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                avoid_id,
                f"kol_pool:{avoid_id}",
                "youtube",
                f"{RUN_MARKER}-avoid",
                f"{RUN_MARKER}-avoid",
                "sigma",
                "sponsored",
                4,
                12,
                "positive",
                8.0,
                "avoid",
                "[]",
                "[]",
                "2026-08-20T10:00:00Z",
            ),
        )
        conn.commit()
        kol_pool._clear_kol_pool_read_cache()

        result = product_analysis.run_recommendations({"query": RUN_MARKER, "limit": 10})

        # 顶层响应契约
        assert result["provider_status"] == "local_rule_only"
        assert result["competitor_filter"] == {
            "mode": "exclude_avoid",
            "filtered_avoid": 1,
            "provider_calls": False,
        }
        assert result["feedback_policy"] == {
            "mode": "score_adjust_v1",
            "candidates_with_feedback": 0,
            "positive_adjusted": 0,
            "negative_adjusted": 0,
            "provider_calls": False,
        }
        assert "rerank_policy" in result and "applied" in result["rerank_policy"]
        assert "snapshots" in result["rerank_policy"]

        run = result["run"]
        assert str(run.get("run_uid") or "").startswith("recrun-")
        assert run["status"] == "completed"
        assert int(run["candidate_count"]) == 3
        assert int(run["recommendation_count"]) == 2
        filters = json.loads(run["filters_json"])
        assert filters["competitor_filter"] == "exclude_avoid"
        assert filters["feedback_policy"] == "score_adjust_v1"
        assert "effective_platforms" not in filters

        recs = result["recommendations"]
        assert len(recs) == 2
        handles = [row["handle"] for row in recs]
        assert f"{RUN_MARKER}-avoid" not in handles
        assert [int(row["rank"]) for row in recs] == [1, 2]
        scores = [float(row["score"]) for row in recs]
        assert scores == sorted(scores, reverse=True)
        assert {int(row["kol_pool_id"]) for row in recs} == {high_id, low_id}
        for idx, row in enumerate(recs, start=1):
            assert str(row["recommendation_uid"]).startswith("rec-")
            assert row["status"] == "recommended"
            assert isinstance(row["rerank_adjustment"], float)
            assert isinstance(row["rerank_reason_codes"], list)
            breakdown = json.loads(row["scoring_breakdown_json"])
            assert "rerank_shadow" in breakdown
            assert breakdown["competitor"]["risk_tier"] == "opportunity"
            assert breakdown["operator_feedback"]["counts"] == {}
            explanation = json.loads(row["explanation_json"])
            assert explanation["strengths"][-1] == "未发现强竞品绑定"
            # 反馈调分为 0 → 不追加反馈注解
            assert "暂无历史员工反馈" not in explanation["strengths"]
            assert "暂无历史员工反馈" not in explanation["concerns"]
            # 每条推荐落 explanation 行(固定 rule 文案)+ outcome 底座行
            exp_rows = conn.execute(
                "SELECT explanation_type, explanation_text, model_version FROM vkpi_recommendation_explanations WHERE recommendation_id=?",
                (int(row["id"]),),
            ).fetchall()
            assert len(exp_rows) == 1
            assert exp_rows[0]["explanation_type"] == "rule"
            assert exp_rows[0]["explanation_text"] == "规则评分，未启用大模型或机器学习；已接入竞品风险过滤和员工反馈调分。"
            outcome_rows = conn.execute(
                "SELECT display_position FROM vkpi_recommendation_outcomes WHERE recommendation_id=?",
                (int(row["id"]),),
            ).fetchall()
            assert len(outcome_rows) == 1
            assert int(outcome_rows[0]["display_position"]) == idx
    finally:
        _run_cleanup()


def test_run_recommendations_include_avoid_and_launch_platform_filter() -> None:
    ensure_vkpi_product_industry_schema()
    ensure_competitor_relation_schema()
    _run_cleanup()
    conn = _real_get_conn()
    try:
        avoid_id = _run_insert_pool_row(f"{RUN_MARKER}-avoid2", fit_score=95)
        _run_insert_pool_row(f"{RUN_MARKER}-ig", fit_score=88, platform="instagram")
        conn.execute(
            """
            INSERT INTO vkpi_competitor_relation
              (kol_pool_id, kol_entity_uid, platform, handle, display_name, competitor_brand,
               collaboration_depth, collaboration_count_90d, collaboration_count_total,
               sentiment, risk_score, risk_tier, evidence_json, evidence_post_uids_json, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                avoid_id,
                f"kol_pool:{avoid_id}",
                "youtube",
                f"{RUN_MARKER}-avoid2",
                f"{RUN_MARKER}-avoid2",
                "sigma",
                "sponsored",
                4,
                12,
                "positive",
                9.0,
                "avoid",
                "[]",
                "[]",
                "2026-08-20T10:00:00Z",
            ),
        )
        conn.commit()
        kol_pool._clear_kol_pool_read_cache()

        launch = product_analysis.create_launch(
            {"name": f"{RUN_MARKER}-launch", "product_sku": "AF85", "target_platforms": ["youtube"]}
        )["launch"]

        result = product_analysis.run_recommendations(
            {
                "query": RUN_MARKER,
                "limit": 10,
                "launch_id": int(launch["id"]),
                "include_avoid_competitors": "true",
            }
        )

        filters = json.loads(result["run"]["filters_json"])
        assert filters["competitor_filter"] == "include_avoid"
        assert filters["effective_platforms"] == ["youtube"]
        assert filters["platform_filter_source"] == "launch.target_platforms"
        assert result["competitor_filter"]["mode"] == "include_avoid"
        assert result["competitor_filter"]["filtered_avoid"] == 0
        assert int(result["run"]["launch_id"]) == int(launch["id"])

        recs = result["recommendations"]
        handles = [row["handle"] for row in recs]
        # avoid 保留(include_avoid);IG 行被 launch 平台过滤挡掉
        assert f"{RUN_MARKER}-avoid2" in handles
        assert f"{RUN_MARKER}-ig" not in handles
        avoid_row = next(row for row in recs if row["handle"] == f"{RUN_MARKER}-avoid2")
        # avoid 档调分 -999 → 分数压到 0
        assert float(avoid_row["score"]) == 0.0
        breakdown = json.loads(avoid_row["scoring_breakdown_json"])
        assert breakdown["competitor"]["risk_tier"] == "avoid"
        explanation = json.loads(avoid_row["explanation_json"])
        assert any(text.startswith("竞品强绑定 SIGMA") for text in explanation["concerns"])
        assert int(avoid_row["launch_id"]) == int(launch["id"])
    finally:
        _run_cleanup()
