"""车道 4(A7+A8)可观测性契约:纯增记账,不许改变任何过滤结果。

两条主张各自有对应断言:
1. **契约** —— 诊断字段的键名与形状稳定(既有键原样保留,新键形状固定);
2. **纯增** —— 同一输入下,加了记账之后的通过集合与「按 ``_candidate_filter_verdict``
   独立算出来的应通过集合」逐条一致,记账没有多杀也没有多放一个人。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import profile_recall, search_session_diagnostics as diag
from app.domains.kol.profile_recall_funnel import RECALL_FUNNEL_SCHEMA, RecallStageLedger
from app.domains.kol.profile_recall_projection import _candidate_filter_verdict


def _install_recall_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: dict[int, dict[str, Any]],
    hits: list[profile_recall.RecallHit],
    resolved_text: str = "camera reviewer",
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: (resolved_text, {"query_profile": ""}),
    )
    monkeypatch.setattr(profile_recall, "_embed_query", lambda _text: ([0.1], {}))
    monkeypatch.setattr(profile_recall, "_search_qdrant", lambda _vector, _limit: hits)
    monkeypatch.setattr(
        profile_recall,
        "_entry_rows",
        lambda ids: {item_id: dict(rows[item_id]) for item_id in ids if item_id in rows},
    )
    monkeypatch.setattr(profile_recall, "_evidence_summaries", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})


def _row(item_id: int, *, country: str = "US", language: str = "en") -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": f"creator-{item_id}",
        "display_name": f"Creator {item_id}",
        "platform": "youtube",
        "profile_type": "creator" if item_id % 2 else "reviewer",
        "creator_type_score": 80,
        "reviewer_type_score": 80,
        "followers": 10_000 + item_id,
        "country": country,
        "language": language,
        "primary_topic": "camera lens review",
        "bio": "Camera gear reviewer and filmmaker",
    }


def _mixed_country_rows() -> dict[int, dict[str, Any]]:
    """1-10 = US(应通过),11-18 = 国家为空(未知驳回),19-24 = DE(值不匹配驳回)。"""

    rows: dict[int, dict[str, Any]] = {}
    for item_id in range(1, 11):
        rows[item_id] = _row(item_id, country="US")
    for item_id in range(11, 19):
        rows[item_id] = _row(item_id, country="")
    for item_id in range(19, 25):
        rows[item_id] = _row(item_id, country="DE")
    return rows


def _run_recall(monkeypatch: pytest.MonkeyPatch, rows: dict[int, dict[str, Any]], filters: dict[str, Any]):
    hits = [
        profile_recall.RecallHit(item_id, 0.9 - item_id / 1000, f"q-{item_id}")
        for item_id in rows
    ]
    _install_recall_fixture(monkeypatch, rows=rows, hits=hits)
    return profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        candidate_limit=len(rows),
        limit=len(rows),
        creator_quota=len(rows),
        reviewer_quota=len(rows),
        filters=filters,
    )


# ── A8:库内召回分层记账 ────────────────────────────────────────────────────────


def test_recall_ledger_does_not_change_the_pass_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """通过集合 == 按硬筛判据独立算出的应通过集合。记账既没多杀也没多放。"""

    rows = _mixed_country_rows()
    filters = {"platforms": ["youtube"], "countries": ["United States"]}
    result = _run_recall(monkeypatch, rows, filters)

    applied = result["diagnostics"]["applied_filters"]
    expected = {
        item_id
        for item_id, row in rows.items()
        if _candidate_filter_verdict(row, {}, applied)[0]
    }
    assert expected == set(range(1, 11))
    assert {item["kol_pool_id"] for item in result["items"]} == expected


def test_recall_diagnostics_split_unknown_from_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """未知驳回与值不匹配驳回必须分开数——「勾了美国」的未知人群要看得见。"""

    rows = _mixed_country_rows()
    result = _run_recall(
        monkeypatch, rows, {"platforms": ["youtube"], "countries": ["United States"]},
    )
    diagnostics = result["diagnostics"]

    # 既有键(前端 / 既有契约依赖)语义原样不动。
    assert diagnostics["hard_filter_rejected_count"] == 14
    assert diagnostics["hard_filter_rejected_by"] == {"countries": 14}
    assert diagnostics["filtered_low_reach"] == 0
    assert diagnostics["filtered_unknown_reach"] == 0
    assert diagnostics["filtered_excluded_region"] == 0
    assert diagnostics["missing_type_count"] == 0
    assert diagnostics["filtered_no_match_evidence"] == 0

    # 新增拆账:8 个国家未知 + 6 个国家不匹配。
    assert diagnostics["hard_filter_rejected_unknown_by"] == {"countries": 8}
    assert diagnostics["hard_filter_rejected_mismatch_by"] == {"countries": 6}
    assert diagnostics["hard_filter_sole_reason_by"] == {"countries": 14}
    assert diagnostics["hard_filter_evaluated_count"] == 24
    assert diagnostics["unknown_field_counts"]["country"] == 8


def test_recall_stage_funnel_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _mixed_country_rows()
    result = _run_recall(
        monkeypatch, rows, {"platforms": ["youtube"], "countries": ["United States"]},
    )
    funnel = result["diagnostics"]["stage_funnel"]

    assert funnel["schema"] == RECALL_FUNNEL_SCHEMA
    assert set(funnel) == {
        "schema", "entered_row_lookup", "entered_region_gate", "entered_reach_gate",
        "entered_hard_filter", "entered_evidence_gate", "survivors", "dropped_by_gate",
    }
    assert set(funnel["dropped_by_gate"]) == {
        "row_missing", "excluded_region", "low_reach", "unknown_reach",
        "hard_filter", "no_match_evidence",
    }
    assert funnel["entered_row_lookup"] == 24
    assert funnel["entered_hard_filter"] == 24
    assert funnel["entered_evidence_gate"] == 10
    assert funnel["survivors"] == 10
    # 每一层的进入量 = 上一层进入量 - 该层丢弃量,漏斗必须自洽。
    dropped = funnel["dropped_by_gate"]
    assert funnel["entered_region_gate"] == funnel["entered_row_lookup"] - dropped["row_missing"]
    assert funnel["entered_reach_gate"] == funnel["entered_region_gate"] - dropped["excluded_region"]
    assert funnel["entered_hard_filter"] == (
        funnel["entered_reach_gate"] - dropped["low_reach"] - dropped["unknown_reach"]
    )
    assert funnel["entered_evidence_gate"] == funnel["entered_hard_filter"] - dropped["hard_filter"]


def test_ledger_counts_multi_field_rejection_without_sole_reason() -> None:
    ledger = RecallStageLedger()
    ledger.note_hard_filter(["countries", "followers_min"], ["country"], passed=False)

    assert ledger.rejected_by == {"countries": 1, "followers_min": 1}
    assert ledger.rejected_unknown_by == {"countries": 1}
    assert ledger.rejected_mismatch_by == {"followers_min": 1}
    assert ledger.sole_reason_by == {}  # 两道闸同时命中 → 不算任何一道的独因


# ── A7-a:发现段漏斗 ───────────────────────────────────────────────────────────


def _provider_slice() -> dict[str, Any]:
    return diag.provider_gate_funnel(
        platform_results=[
            {"platform": "youtube", "returned": 30, "filtered_platform_mismatch": 1},
            {"platform": "instagram", "returned": 20, "filtered_platform_mismatch": 0},
            {"platform": "tiktok", "returned": 10, "filtered_platform_mismatch": 0},
        ],
        gate_dropped={
            "hard_avoid": 4, "no_camera_signal": 5, "low_reach": 2, "brand_official": 3,
            "brand_official_lexicon": 2, "brand_official_dynamic": 1, "bio_irrelevant": 1,
            "persona_avoid_penalized": 7,
        },
        survivors=40,
        returned_new_creators=12,
        existing_matched=5,
    )


def test_provider_gate_funnel_excludes_subcounts_and_penalties() -> None:
    slice_ = _provider_slice()

    assert slice_["platform_returned"] == {"instagram": 20, "tiktok": 10, "youtube": 30}
    assert slice_["platform_returned_total"] == 60
    assert slice_["platform_mismatch_dropped"] == 1
    # brand_official_lexicon/_dynamic 是子计数,persona_avoid_penalized 是扣分不是丢弃。
    assert slice_["gate_dropped_total"] == 4 + 5 + 2 + 3 + 1
    assert slice_["gate_dropped"]["persona_avoid_penalized"] == 7
    assert slice_["survivors"] == 40
    assert slice_["returned_new_creators"] == 12
    assert slice_["truncated_by_limit"] == 28
    # 余项诚实标注,不硬塞进某一道闸:60 - 1(平台不符) - 5(库内已有) - 15(分项闸) - 40(存活)
    assert slice_["unattributed_dropped"] == 0
    assert diag.provider_gate_funnel(
        platform_results=[{"platform": "youtube", "returned": 60}],
        gate_dropped={"hard_avoid": 3},
        survivors=40,
        returned_new_creators=12,
        existing_matched=5,
    )["unattributed_dropped"] == 12


def test_build_discovery_funnel_aggregates_rounds_and_online_contract() -> None:
    funnel = diag.build_discovery_funnel(
        lane="online_strict",
        provider_funnels=[_provider_slice(), _provider_slice()],
        online_contract={
            "evaluated_count": 47,
            "net_new_accepted_count": 12,
            "returned_count": 12,
            "rejected_count": 20,
            "pending_count": 15,
            "shortfall": 18,
            "rejected_by_reason": {"market_mismatch": 9, "language_mismatch": 11},
        },
        discovery_counts={"new_creators": 12, "existing_matches": 5, "auto_enrolled": 0},
        returned_count=12,
    )

    assert funnel["schema"] == diag.DISCOVERY_FUNNEL_SCHEMA
    assert funnel["lane"] == "online_strict"
    assert funnel["provider_rounds"] == 2
    assert funnel["platform_returned"] == {"instagram": 40, "tiktok": 20, "youtube": 60}
    assert funnel["platform_returned_total"] == 120
    assert funnel["gate_dropped"]["hard_avoid"] == 8
    assert funnel["gate_dropped_total"] == 30
    assert funnel["survivors"] == 80
    assert funnel["online_strict"]["evaluated_count"] == 47
    assert funnel["online_strict"]["rejected_by_reason"] == {
        "market_mismatch": 9, "language_mismatch": 11,
    }
    assert funnel["session_returned_count"] == 12
    assert funnel["provider_counts"]["new_creators"] == 12


def test_build_discovery_funnel_is_honest_when_provider_gave_nothing() -> None:
    funnel = diag.build_discovery_funnel(lane="legacy_discovery")

    assert funnel["provider_rounds"] == 0
    assert funnel["platform_returned"] == {}
    assert funnel["platform_returned_total"] == 0
    assert "online_strict" not in funnel
    assert "session_returned_count" not in funnel


# ── A7-b:profile-advance 第二段请求 filter 快照 ────────────────────────────────


def test_filter_snapshot_keeps_filters_and_drops_free_text() -> None:
    snapshot = diag.project_filter_snapshot(
        {
            "query_text": "找美国的电影摄影师 联系 someone@example.com",
            "target_persona": "cinematographer",
            "filters": {
                "countries": ["United States", "us", "US"],
                "languages": ["en"],
                "followers_min": 50_000,
            },
            "platforms": ["youtube", "instagram"],
            "exclude_chinese": True,
            "market": "US",
        },
        stage="text_recall",
        source="kol_smart_search_profile_pipeline",
    )

    assert snapshot["schema"] == diag.FILTER_SNAPSHOT_SCHEMA
    assert snapshot["stage"] == "text_recall"
    assert snapshot["filters"] == {
        "countries": ["US", "UnitedStates", "us"],
        "languages": ["en"],
        "followers_min": 50_000,
    }
    assert snapshot["body_filters"]["platforms"] == ["instagram", "youtube"]
    assert snapshot["body_filters"]["market"] == "US"
    assert snapshot["body_filters"]["exclude_chinese"] is True
    assert snapshot["filters_present"] is True
    # 自由文本一律不进快照:留痕的是筛选口径,不是请求体副本。
    blob = repr(snapshot)
    assert "example.com" not in blob and "cinematographer" not in blob


def test_advance_request_snapshot_shape_gate_drops_foreign_entries() -> None:
    kept = diag.project_advance_request_snapshots([
        {"schema": "not_ours", "filters": {"countries": ["US"]}},
        "junk",
        {
            "schema": diag.FILTER_SNAPSHOT_SCHEMA,
            "stage": "text_recall",
            "source": "x",
            "recorded_at": "2026-08-25T00:00:00+00:00",
            "filters": {"countries": ["US"], "evil": "drop me"},
            "body_filters": {},
            "filters_present": True,
        },
    ])

    assert len(kept) == 1
    assert kept[0]["filters"] == {"countries": ["US"]}
    assert "evil" not in kept[0]["filters"]


class _FakeRow(dict):
    pass


class _FakeConn:
    def __init__(self, payload_json: str | None) -> None:
        self.payload_json = payload_json
        self.written: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()):  # noqa: D401 - test double
        self.written.append((sql, params))
        if sql.strip().upper().startswith("SELECT"):
            row = _FakeRow({"input_payload_json": self.payload_json})
            return type("_Cur", (), {"fetchone": lambda _self: (row if self.payload_json is not None else None)})()
        return type("_Cur", (), {"fetchone": lambda _self: None})()

    def commit(self) -> None:
        self.commits += 1


def test_record_advance_request_snapshot_uses_a_separate_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    conn = _FakeConn(json.dumps({"query_text": "camera reviewer", "countries": ["DE"]}))
    result = diag.record_advance_request_snapshot(
        7,
        body={"filters": {"countries": ["United States"]}},
        stage="text_recall",
        source="pipeline",
        get_conn_fn=lambda: conn,
    )

    assert result["status"] == "recorded"
    update_sql, params = conn.written[-1]
    assert "UPDATE vkpi_kol_search_sessions" in update_sql
    written = json.loads(params[0])
    # 第一段原始 input 原样不动,第二段快照挂独立键。
    assert written["query_text"] == "camera reviewer"
    assert written["countries"] == ["DE"]
    assert written[diag.ADVANCE_REQUEST_SNAPSHOTS_KEY][0]["filters"] == {"countries": ["UnitedStates"]}
    assert conn.commits == 1


def test_record_advance_request_snapshot_skips_identical_replays(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    body = {"filters": {"countries": ["United States"]}}
    first = diag.project_filter_snapshot(body, stage="text_recall", source="pipeline")
    conn = _FakeConn(json.dumps({diag.ADVANCE_REQUEST_SNAPSHOTS_KEY: [first]}))

    result = diag.record_advance_request_snapshot(
        7, body=body, stage="text_recall", source="pipeline", get_conn_fn=lambda: conn,
    )

    assert result["status"] == "skipped"
    assert conn.commits == 0
    assert all("UPDATE" not in sql for sql, _ in conn.written)


def test_record_advance_request_snapshot_fails_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("db is down")

    result = diag.record_advance_request_snapshot(
        7, body={}, stage="text_recall", source="pipeline", get_conn_fn=_boom,
    )

    # 诊断写失败绝不上抛(否则搜索被诊断拖垮),但也绝不静默:返回诚实的失败态。
    assert result == {"status": "failed", "reason": "snapshot_write_failed"}


def test_strict_online_pipeline_persists_the_collapse(monkeypatch: pytest.MonkeyPatch) -> None:
    """严格在线模式跳过 attach_new_discovery_result —— 漏斗必须由诊断补上留痕。"""

    import asyncio

    from app.domains.kol import profile_discovery_pipeline as pipeline

    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        pipeline.profile_recall, "recall_kol_profiles",
        lambda **_kwargs: {
            "method": "test", "items": [], "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0}, "local_qualification": None,
        },
    )
    monkeypatch.setattr(pipeline, "filter_recall_result_platforms", lambda result, _v: result)
    monkeypatch.setattr(pipeline, "filter_recall_result_market", lambda result, _v: result)
    monkeypatch.setattr(pipeline.search_sessions, "attach_recall_result", lambda _sid, _r: {"id": 77})
    monkeypatch.setattr(
        pipeline.search_sessions, "attach_online_qualified_result", lambda _sid, _r: {"id": 77},
    )
    monkeypatch.setattr(pipeline.search_sessions, "update_session_result_summary", lambda *_a, **_k: {})
    monkeypatch.setattr(pipeline, "_profile_advance_pipeline_status", lambda *_a: "partial")
    monkeypatch.setattr(
        pipeline, "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty", "selected": 0, "counts": {}, "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        pipeline.search_session_diagnostics, "record_search_diagnostics",
        lambda session_id, patch: recorded.append({"session_id": session_id, **patch}) or {"status": "recorded"},
    )

    async def _fake_discover(**_kwargs: Any) -> dict[str, Any]:
        # 平台给 60 → 本地闸砍到 47。
        return {
            "status": "ready", "items": [], "new_creators": [], "existing_matches": [],
            "counts": {"new_creators": 47, "existing_matches": 0},
            "discovery_funnel": diag.provider_gate_funnel(
                platform_results=[
                    {"platform": "youtube", "returned": 30},
                    {"platform": "instagram", "returned": 20},
                    {"platform": "tiktok", "returned": 10},
                ],
                gate_dropped={"hard_avoid": 8, "no_camera_signal": 5},
                survivors=47,
                returned_new_creators=47,
                existing_matched=0,
            ),
        }

    async def _fake_collect(**kwargs: Any) -> dict[str, Any]:
        # 真实实现同样按轮调用 fetch_batch;这里照做,才能证明每轮切片被收走。
        await kwargs["fetch_batch"](round_no=1, limit=150, cursor=None)
        # 在线严格闸再砍到 12。
        return {
            "status": "shortfall", "items": [], "evaluated_count": 47,
            "net_new_accepted_count": 12, "returned_count": 12, "rejected_count": 35,
            "shortfall": 18, "rejected_by_reason": {"market_unknown": 35},
            "provider_calls_performed": True,
        }

    monkeypatch.setattr(pipeline, "discover_new_creators", _fake_discover)
    monkeypatch.setattr(
        pipeline.profile_online_qualification, "collect_strict_online_for_session", _fake_collect,
    )

    asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(
        session_id=77,
        payload={
            "query_text": "camera reviewer",
            "platforms": ["youtube"],
            "_worker_planned": True,
            "_smart_online_30_contract": True,
            "include_new_discovery": True,
            "include_content_fit": False,
            "include_lazy_video_backfill": False,
        },
    ))

    assert len(recorded) == 1
    funnel = recorded[0][diag.DISCOVERY_FUNNEL_KEY]
    assert funnel["lane"] == "online_strict"
    assert funnel["platform_returned_total"] == 60          # 平台原始给量
    assert funnel["gate_dropped_total"] == 13               # 本地闸砍掉的
    assert funnel["survivors"] == 47                        # 进在线严格闸的
    assert funnel["online_strict"]["evaluated_count"] == 47
    assert funnel["online_strict"]["net_new_accepted_count"] == 12
    assert funnel["online_strict"]["rejected_by_reason"] == {"market_unknown": 35}


def test_ensure_session_for_result_records_the_second_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import search_sessions

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        search_sessions, "get_session",
        lambda session_id, **_kwargs: {"id": int(session_id), "status": "running"},
    )
    monkeypatch.setattr(
        search_sessions.search_session_diagnostics,
        "record_advance_request_snapshot",
        lambda session_id, **kwargs: calls.append({"session_id": session_id, **kwargs}) or {"status": "recorded"},
    )

    session = search_sessions.ensure_session_for_result(
        session_id=11,
        create=False,
        query_text="camera reviewer",
        query_type="text_recall",
        source="kol_smart_search_profile_pipeline",
        input_payload={"filters": {"countries": ["United States"]}},
        staff={"user_id": 3},
    )

    assert session == {"id": 11, "status": "running"}
    assert len(calls) == 1
    assert calls[0]["session_id"] == 11
    assert calls[0]["body"] == {"filters": {"countries": ["United States"]}}
    assert calls[0]["stage"] == "text_recall"


def test_serde_allowlist_carries_the_snapshot_key_through_reads() -> None:
    from app.domains.kol.search_sessions_serde import _sanitize_session_input_payload

    projected = _sanitize_session_input_payload({
        "query_text": "camera reviewer",
        "not_allowlisted": "dropped",
        diag.ADVANCE_REQUEST_SNAPSHOTS_KEY: [
            diag.project_filter_snapshot({"filters": {"countries": ["US"]}}),
        ],
    })

    assert projected["query_text"] == "camera reviewer"
    assert "not_allowlisted" not in projected
    assert projected[diag.ADVANCE_REQUEST_SNAPSHOTS_KEY][0]["filters"] == {"countries": ["US"]}
