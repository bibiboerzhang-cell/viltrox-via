"""续篇(后半)。"""
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
