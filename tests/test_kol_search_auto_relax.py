"""自动放宽策略的契约(2026-08-26)。

钉死用户的四条红线:
1. 操作员显式勾的条件一格都不许松;
2. 质量合格线(器材证据 / 新鲜度 / 证据词数 / 产品锚 / 账号安全)一格都不许松;
3. 放宽顺序按代价从小到大,够 30 就立刻停;
4. 松不动就如实说「库里就是没有人」,不许假装——降级也要如实标是规则推荐。

估算器用真实的三态语义(``profile_recall_filter_modes``)在一个内存小池上算,
既模拟了另一条车道的零成本 COUNT,也顺带证明「未知」没有被混进「不符合」。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import search_auto_relax as relax
from app.domains.kol.profile_recall_filter_modes import (
    OUTCOME_UNKNOWN,
    normalize_tri_state_filter,
    tri_state_outcome,
)


# ── 内存小池 + 零成本估算器(模拟另一条车道的 SQL COUNT)────────────────────


def _values(raw: Any) -> set[str]:
    if isinstance(raw, dict):
        raw = raw.get("values")
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip().lower() for item in raw if str(item).strip()}
    text = str(raw or "").strip().lower()
    return {text} if text else set()


def _make_pool() -> list[dict[str, Any]]:
    """40 人。刻意复刻线上分布:语言大面积空,国家部分空,少数确认不符。"""
    pool: list[dict[str, Any]] = []
    for index in range(40):
        pool.append(
            {
                "country": "us" if index < 28 else ("jp" if index < 34 else ""),
                # 前 5 个确认英语,6~9 确认西语(确认不符),其余全部没填。
                "language": "en" if index < 5 else ("es" if index < 9 else ""),
                "vertical": "lens_review" if index % 2 == 0 else "lifestyle",
                "followers": 120_000 if index < 12 else 40_000 if index < 26 else 5_000,
                "platform": "youtube" if index % 3 else "instagram",
                "gear_content": "yes",
            }
        )
    return pool


class RecordingEstimator:
    """零成本估算器替身。记录每次调用,证明「模型只提议、数据库定夺」且不碰 provider。"""

    def __init__(self, pool: list[dict[str, Any]]) -> None:
        self.pool = pool
        self.calls: list[dict[str, Any]] = []

    def __call__(self, filters: Any) -> dict[str, int]:
        self.calls.append(dict(filters))
        qualified = unknown = mismatch = 0
        for row in self.pool:
            reasons: dict[str, str] = {}
            for key, field in (("countries", "country"), ("languages", "language")):
                raw, mode, _ = normalize_tri_state_filter(filters.get(key))
                outcome = tri_state_outcome(str(row.get(field) or ""), _values(raw), mode)
                if outcome:
                    reasons[key] = outcome
            wanted_verticals = _values(filters.get("verticals"))
            if wanted_verticals and str(row.get("vertical")) not in wanted_verticals:
                reasons["verticals"] = "mismatch"
            wanted_platforms = _values(filters.get("platforms"))
            if wanted_platforms and str(row.get("platform")) not in wanted_platforms:
                reasons["platforms"] = "mismatch"
            floor = filters.get("followers_min")
            if floor and int(row.get("followers") or 0) < int(floor):
                reasons["followers_min"] = "mismatch"
            if filters.get("gear_content") in ("yes", "no") and row.get("gear_content") != filters["gear_content"]:
                reasons["gear_content"] = "mismatch"
            if not reasons:
                qualified += 1
            elif set(reasons.values()) == {OUTCOME_UNKNOWN}:
                unknown += 1
            else:
                mismatch += 1
        return {"qualified": qualified, "unknown": unknown, "mismatch": mismatch, "pool_total": len(self.pool)}


@pytest.fixture()
def estimator() -> RecordingEstimator:
    return RecordingEstimator(_make_pool())


# ── 红线 1:操作员显式指定的条件,一格都不许松 ──────────────────────────────


def test_operator_filters_are_never_relaxed(estimator: RecordingEstimator) -> None:
    filters = {"countries": ["us"], "languages": ["en"], "verticals": ["lens_review"]}
    origins = {key: relax.ORIGIN_OPERATOR for key in filters}

    payload = relax.plan_auto_relax(filters, origins, estimator=estimator, target=30)

    assert payload["applied"] == []
    assert payload["effective_filters"] == filters
    assert payload["status"] == relax.STATUS_SHORT
    assert {item["key"] for item in payload["skipped"]} == {"countries", "languages", "verticals"}
    assert all(item["reason"] == "operator_explicit" for item in payload["skipped"])


def test_unknown_origin_is_treated_as_operator(estimator: RecordingEstimator) -> None:
    """来源不明 = 当成操作员的。失败方向必须是「不动」。"""
    payload = relax.plan_auto_relax({"languages": ["en"]}, {}, estimator=estimator, target=30)
    assert payload["applied"] == []
    assert payload["effective_filters"] == {"languages": ["en"]}


def test_mixed_origins_relax_only_the_model_half(estimator: RecordingEstimator) -> None:
    filters = {"countries": ["us"], "languages": ["en"]}
    origins = {"countries": relax.ORIGIN_OPERATOR, "languages": relax.ORIGIN_MODEL}

    payload = relax.plan_auto_relax(filters, origins, estimator=estimator, target=20)

    assert [step["key"] for step in payload["applied"]] == ["languages"]
    assert payload["effective_filters"]["countries"] == ["us"]
    assert payload["skipped"] == [{"key": "countries", "reason": "operator_explicit"}]


# ── 红线 2:质量合格线一格都不许松 ──────────────────────────────────────────


def test_quality_gates_are_not_relaxable(estimator: RecordingEstimator) -> None:
    filters = {
        "gear_content": "yes",
        "freshness_days": 30,
        "evidence_min_terms": 2,
        "require_product_anchor": True,
        "languages": ["en"],
    }
    origins = {key: relax.ORIGIN_MODEL for key in filters}

    payload = relax.plan_auto_relax(filters, origins, estimator=estimator, target=30)

    effective = payload["effective_filters"]
    for key in ("gear_content", "freshness_days", "evidence_min_terms", "require_product_anchor"):
        assert effective[key] == filters[key], key
    assert {step["key"] for step in payload["applied"]} <= set(relax.RELAXABLE_FILTER_KEYS)
    assert payload["protected_untouched"] == sorted(
        {"gear_content", "freshness_days", "evidence_min_terms", "require_product_anchor"}
    )


def test_protected_and_relaxable_key_sets_never_overlap() -> None:
    assert not (relax.PROTECTED_FILTER_KEYS & set(relax.RELAXABLE_FILTER_KEYS))


def test_followers_floor_never_goes_below_the_quality_floor(estimator: RecordingEstimator) -> None:
    from app.domains.kol import smart_query_intent

    payload = relax.plan_auto_relax(
        {"followers_min": 500_000},
        {"followers_min": relax.ORIGIN_MODEL},
        estimator=estimator,
        target=40,
    )
    tiers = [step["to_value"] for step in payload["applied"] if step["action"] == relax.ACTION_LOWER]
    assert tiers, "应该真的走过降档"
    assert min(tiers) >= smart_query_intent.AUDIENCE_SCALE_FLOOR
    assert relax.FOLLOWERS_TIERS[-1] == smart_query_intent.AUDIENCE_SCALE_FLOOR


# ── 红线 3:顺序按代价从小到大;够 30 就停 ─────────────────────────────────


def test_ladder_order_is_cost_ascending(estimator: RecordingEstimator) -> None:
    filters = {
        "languages": ["en"],
        "countries": ["us"],
        "verticals": ["lens_review"],
        "followers_min": 100_000,
        "platforms": ["youtube"],
    }
    origins = {key: relax.ORIGIN_MODEL for key in filters}

    steps = relax._candidate_steps(filters, origins)
    ordered = [(step.key, step.action) for step in steps]

    assert ordered[0] == ("languages", relax.ACTION_INCLUDE_UNKNOWN)
    assert ordered[1] == ("countries", relax.ACTION_INCLUDE_UNKNOWN)
    assert ordered[2] == ("verticals", relax.ACTION_DROP)
    lower_positions = [i for i, item in enumerate(ordered) if item == ("followers_min", relax.ACTION_LOWER)]
    platform_position = ordered.index(("platforms", relax.ACTION_DROP))
    assert lower_positions and max(lower_positions) < platform_position
    # 整条丢掉语言 / 国家会真的放进「确认不符」的人,代价最高 —— 必须排在最后两格。
    assert ordered[-2:] == [("languages", relax.ACTION_DROP), ("countries", relax.ACTION_DROP)]


def test_stops_as_soon_as_target_is_met(estimator: RecordingEstimator) -> None:
    filters = {"languages": ["en"], "countries": ["us"], "verticals": ["lens_review"]}
    origins = {key: relax.ORIGIN_MODEL for key in filters}

    # 库里「美国 + 镜头评测」共 14 人,其中 4 人确认说西语。只放宽语言这一格就能到 12 人。
    payload = relax.plan_auto_relax(filters, origins, estimator=estimator, target=12)

    assert payload["status"] == relax.STATUS_RELAXED
    assert [step["key"] for step in payload["applied"]] == ["languages"]
    assert payload["final_count"] >= 12
    # 只放宽了语言,后面的国家 / 垂类原封不动。
    assert payload["effective_filters"]["countries"] == ["us"]
    assert payload["effective_filters"]["verticals"] == ["lens_review"]
    # 1 次基线 + 1 次复核,再没多调一次。
    assert payload["estimates_performed"] == 2 == len(estimator.calls)


def test_no_relax_when_baseline_already_meets_target(estimator: RecordingEstimator) -> None:
    payload = relax.plan_auto_relax(
        {"countries": ["us"]}, {"countries": relax.ORIGIN_MODEL}, estimator=estimator, target=5
    )
    assert payload["status"] == relax.STATUS_NOT_NEEDED
    assert payload["applied"] == []
    assert payload["estimates_performed"] == 1 == len(estimator.calls)
    assert payload["effective_filters"] == {"countries": ["us"]}


def test_relaxing_language_only_readmits_people_who_left_it_blank(estimator: RecordingEstimator) -> None:
    """放宽语言这一格,放回来的必须全是「没填」的人,不含「确认说西语」的人。"""
    payload = relax.plan_auto_relax(
        {"languages": ["en"]}, {"languages": relax.ORIGIN_MODEL}, estimator=estimator, target=40
    )
    first = payload["applied"][0]
    assert first["action"] == relax.ACTION_INCLUDE_UNKNOWN
    assert first["gained_are_unknown_only"] is True
    assert first["count_before"] == 5  # 只有 5 个人确认写了英语
    assert first["gained"] == 31  # 31 个没填的人被放回来
    assert first["count_after"] == 36  # 确认说西语的 4 个人仍然被挡在外面
    assert payload["baseline"]["unknown"] == 31
    assert payload["baseline"]["mismatch"] == 4


# ── 红线 4:松不动就如实说,不许假装 ────────────────────────────────────────


def test_nothing_to_relax_reports_the_truth_not_a_pretence() -> None:
    """全是操作员亲手勾的、而且库里确实一个人都没有 —— 必须如实报 0。"""
    estimator = RecordingEstimator([])
    payload = relax.plan_auto_relax(
        {"countries": ["us"], "languages": ["en"]},
        {"countries": relax.ORIGIN_OPERATOR, "languages": relax.ORIGIN_OPERATOR},
        estimator=estimator,
        target=30,
    )
    assert payload["status"] == relax.STATUS_SHORT
    assert payload["baseline_count"] == 0
    assert payload["final_count"] == 0
    assert payload["applied"] == []


def test_relaxed_everything_and_still_short(estimator: RecordingEstimator) -> None:
    filters = {"countries": ["de"], "languages": ["fr"], "verticals": ["cooking"]}
    origins = {key: relax.ORIGIN_MODEL for key in filters}

    # 池里总共只有 40 人,目标 45 —— 松到底也不可能够。
    payload = relax.plan_auto_relax(filters, origins, estimator=estimator, target=45)

    assert payload["status"] == relax.STATUS_SHORT
    assert payload["final_count"] < 45
    assert payload["applied"], "松过,只是松到底也不够 —— 台账必须留痕"
    assert payload["final_count"] >= payload["baseline_count"]


def test_disabled_keeps_the_operator_filters_untouched(estimator: RecordingEstimator) -> None:
    filters = {"languages": ["en"]}
    payload = relax.plan_auto_relax(
        filters, {"languages": relax.ORIGIN_MODEL}, estimator=estimator, target=30, enabled=False
    )
    assert payload["status"] == relax.STATUS_DISABLED
    assert payload["effective_filters"] == filters
    assert estimator.calls == []


def test_missing_estimator_is_reported_not_faked() -> None:
    filters = {"languages": ["en"]}
    payload = relax.plan_auto_relax(filters, {"languages": relax.ORIGIN_MODEL}, estimator=None, target=30)
    assert payload["status"] == relax.STATUS_UNAVAILABLE
    assert payload["unavailable_reason"] == "estimator_missing"
    assert payload["baseline_count"] is None
    assert payload["effective_filters"] == filters


def test_estimator_failure_never_loosens_beyond_the_last_proven_step() -> None:
    class Flaky(RecordingEstimator):
        def __call__(self, filters: Any) -> dict[str, int]:
            if len(self.calls) >= 2:
                raise RuntimeError("count failed")
            return super().__call__(filters)

    flaky = Flaky(_make_pool())
    filters = {"languages": ["en"], "countries": ["us"]}
    origins = {key: relax.ORIGIN_MODEL for key in filters}
    payload = relax.plan_auto_relax(filters, origins, estimator=flaky, target=40)

    assert payload["status"] == relax.STATUS_SHORT
    assert [step["key"] for step in payload["applied"]] == ["languages"]
    assert payload["effective_filters"]["countries"] == ["us"]


def test_baseline_failure_reports_unavailable() -> None:
    def boom(_filters: Any) -> dict[str, int]:
        raise RuntimeError("count failed")

    payload = relax.plan_auto_relax({"languages": ["en"]}, {"languages": relax.ORIGIN_MODEL}, estimator=boom)
    assert payload["status"] == relax.STATUS_UNAVAILABLE
    assert payload["unavailable_reason"] == "estimate_failed"


def test_estimate_budget_is_bounded(estimator: RecordingEstimator) -> None:
    filters = {
        "languages": ["en"],
        "countries": ["us"],
        "verticals": ["lens_review"],
        "followers_min": 500_000,
        "platforms": ["youtube"],
    }
    origins = {key: relax.ORIGIN_MODEL for key in filters}
    payload = relax.plan_auto_relax(filters, origins, estimator=estimator, target=10_000, max_estimates=3)
    assert payload["estimates_performed"] == 3 == len(estimator.calls)


# ── 红线 6:降级要诚实 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "plan,expected",
    [
        ({"provider": "google", "fallback_used": False}, "model"),
        ({"provider": "rule_v0", "fallback_used": True}, "rules"),
        ({"provider": "provider_free", "fallback_used": False}, "rules"),
        ({"provider": "product_catalog_guard"}, "rules"),
        ({}, "rules"),
        (None, "rules"),
    ],
)
def test_advice_source_never_claims_a_model_it_did_not_use(plan: Any, expected: str) -> None:
    assert relax.advice_source(plan) == expected


def test_payload_declares_zero_cost_and_no_provider_calls(estimator: RecordingEstimator) -> None:
    payload = relax.plan_auto_relax({}, {}, estimator=estimator, target=1)
    assert payload["provider_calls"] is False
    assert payload["estimate_cost"] == "sql_count_only"
    assert payload["schema"] == relax.SCHEMA


# ── 来源判定 ────────────────────────────────────────────────────────────────


def test_body_filters_are_all_operator_origin() -> None:
    filters, origins = relax.assemble_recall_filters(
        {"filters": {"countries": ["us"], "languages": ["en"]}, "verticals": ["lens_review"]}
    )
    assert filters == {"countries": ["us"], "languages": ["en"], "verticals": ["lens_review"]}
    assert origins == {key: relax.ORIGIN_OPERATOR for key in filters}


def test_platform_fallback_chain_matches_the_existing_route() -> None:
    filters, origins = relax.assemble_recall_filters({"platforms": ["youtube"]})
    assert filters["platforms"] == ["youtube"]
    filters, _ = relax.assemble_recall_filters({}, query_platforms=["tiktok"])
    assert filters["platforms"] == ["tiktok"]
    filters, _ = relax.assemble_recall_filters({"filters": {"platforms": ["instagram"]}}, query_platforms=["tiktok"])
    assert filters["platforms"] == ["instagram"]
    assert origins["platforms"] == relax.ORIGIN_OPERATOR


def test_non_dict_filters_are_rejected() -> None:
    with pytest.raises(ValueError):
        relax.assemble_recall_filters({"filters": "us"})


def _proposal(filters: dict[str, Any], relaxable: list[str], *, source: str = "model") -> dict[str, Any]:
    """模拟提议车道(``smart_query_facets.propose_facets``)挂在计划上的那份提议。"""
    return {"filters": filters, "relaxable_fields": relaxable, "source": source, "degraded": False}


def test_proposal_only_fills_gaps_and_respects_its_own_relaxable_set() -> None:
    filters, origins = relax.merge_plan_filters(
        {"countries": ["us"]},
        {"countries": relax.ORIGIN_OPERATOR},
        {
            "filter_proposal": _proposal(
                {"countries": ["jp"], "languages": ["en"], "verticals": ["lens_review"]},
                ["languages", "verticals"],
            )
        },
    )
    assert filters["countries"] == ["us"], "界面上勾过的值,提议不许覆盖"
    assert origins["countries"] == relax.ORIGIN_OPERATOR
    assert filters["languages"] == ["en"]
    assert origins["languages"] == relax.ORIGIN_MODEL
    assert origins["verticals"] == relax.ORIGIN_MODEL


def test_proposal_explicit_fields_are_locked_even_though_they_came_from_the_plan() -> None:
    """提议车道按操作员原话判定「明确」——那也是操作员的意思,一格都不许自动松。"""
    filters, origins = relax.merge_plan_filters(
        {},
        {},
        {"filter_proposal": _proposal({"countries": ["us"], "languages": ["en"]}, ["languages"])},
    )
    assert origins["countries"] == relax.ORIGIN_OPERATOR
    assert origins["languages"] == relax.ORIGIN_MODEL
    assert relax._candidate_steps(filters, origins)[0].key == "languages"
    assert all(step.key != "countries" for step in relax._candidate_steps(filters, origins))


def test_proposal_min_followers_maps_onto_the_recall_filter_key() -> None:
    assert relax.FACET_TO_FILTER_KEY["min_followers"] == "followers_min"
    _filters, origins = relax.merge_plan_filters(
        {}, {}, {"filter_proposal": _proposal({"followers_min": 100_000}, ["min_followers"])}
    )
    assert origins["followers_min"] == relax.ORIGIN_MODEL


def test_real_proposal_from_the_upstream_lane_wires_through() -> None:
    """真正用提议车道的产出跑一遍,证明两条车道的契约对得上(不是我这边自说自话)。"""
    from app.domains.kol import smart_query_facets

    proposal = smart_query_facets.propose_facets("找美国的镜头评测博主", {"market": "US"})
    filters, origins = relax.merge_plan_filters({}, {}, {"filter_proposal": proposal})
    for key, value in (proposal.get("filters") or {}).items():
        assert filters[key] == value
    relaxable_keys = {relax.FACET_TO_FILTER_KEY[field] for field in proposal["relaxable_fields"]}
    for key in filters:
        expected = relax.ORIGIN_MODEL if key in relaxable_keys else relax.ORIGIN_OPERATOR
        assert origins[key] == expected, key
    # 「美国」是操作员原话里就有的 —— 提议车道判成明确项,自动放宽必须绕开它。
    assert "countries" in proposal["locked_fields"]
    assert all(step.key != "countries" for step in relax._candidate_steps(filters, origins))


def test_advice_source_follows_the_proposal_lane() -> None:
    assert relax.advice_source({"filter_proposal": _proposal({}, [], source="model")}) == "model"
    assert relax.advice_source({"filter_proposal": _proposal({}, [], source="rule")}) == "rules"


def test_legacy_plan_without_a_proposal_still_honours_the_operator() -> None:
    filters, origins = relax.merge_plan_filters(
        {"followers_min": 8_000}, {"followers_min": relax.ORIGIN_OPERATOR}, {"min_followers_hint": 100_000}
    )
    assert filters["followers_min"] == 8_000
    assert origins["followers_min"] == relax.ORIGIN_OPERATOR

    filters, origins = relax.merge_plan_filters({}, {}, {"min_followers_hint": 100_000})
    assert filters["followers_min"] == 100_000
    assert origins["followers_min"] == relax.ORIGIN_MODEL


# ── 一步到位入口 ────────────────────────────────────────────────────────────


def test_run_auto_relax_returns_filters_and_ledger(estimator: RecordingEstimator) -> None:
    effective, payload = relax.run_auto_relax(
        {"filters": {}},
        {"filter_proposal": _proposal({"languages": ["en"]}, ["languages"], source="model")},
        estimator=estimator,
        target=20,
    )
    assert payload["advice_source"] == "model"
    assert payload["origins"]["languages"] == relax.ORIGIN_MODEL
    assert effective == payload["effective_filters"]


def test_run_auto_relax_honours_the_operator_opt_out(estimator: RecordingEstimator) -> None:
    effective, payload = relax.run_auto_relax(
        {"filters": {"languages": ["en"]}, "auto_relax": False}, {}, estimator=estimator
    )
    assert payload["status"] == relax.STATUS_DISABLED
    assert effective == {"languages": ["en"]}
    assert estimator.calls == []


# ── 与产量预估车道的接线 ────────────────────────────────────────────────────


def test_default_estimator_resolves_the_yield_lane() -> None:
    """首选产量车道给本车道开的取数口(三态总账摊平在顶层)。"""
    from app.domains.kol import search_yield_estimate

    assert relax.default_estimator() is search_yield_estimate.estimate_yield


def test_estimator_falls_back_to_the_algorithm_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """取数口不在时退到算法本体,而不是当成「估不出来」。"""
    import builtins

    from app.domains.kol import pool_yield_estimate

    real_import = builtins.__import__

    def _blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "app.domains.kol.search_yield_estimate":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert relax.default_estimator() is pool_yield_estimate.estimate_pool_yield


def test_both_estimator_shapes_yield_the_same_headcount() -> None:
    """取数口报 ``qualified``、算法本体报 ``estimated`` —— 两种形状都必须读得出同一个数。"""
    assert relax._count_of({"qualified": 6, "unknown": 43, "mismatch": 5, "pool_total": 2036}) == 6
    assert relax._count_of({"estimated": 6, "totals": {"qualified": 6}}) == 6


def test_count_reads_the_yield_lanes_own_key() -> None:
    """产量车道把人数放在 ``estimated`` 里。读错键 = 永远看见 0,那会假装「库里没人」。"""
    assert relax._count_of({"estimated": 6, "pool_total": 2036}) == 6
    assert relax._count_of({"qualified": 49}) == 49
    assert relax._count_of("6") == 0
    assert relax._count_of({"estimated": "not a number"}) == 0


def test_scope_note_reaches_the_operator_unchanged() -> None:
    """「这是库内可选人数;联网还能补多少人不在此列」必须原样透到界面,不许被吃掉。"""
    from app.domains.kol import pool_yield_estimate

    def estimator(_filters: Any) -> dict[str, Any]:
        return {
            "estimated": 6,
            "pool_total": 2036,
            "scope": pool_yield_estimate.SCOPE,
            "scope_note": pool_yield_estimate.SCOPE_NOTE,
            "tri_state": [{"filter": "languages", "qualified": 6, "unknown": 43, "mismatch": 5}],
        }

    payload = relax.plan_auto_relax({}, {}, estimator=estimator, target=1)
    assert payload["scope_note"] == pool_yield_estimate.SCOPE_NOTE
    assert payload["pool_total"] == 2036
    assert payload["baseline"]["tri_state"][0]["unknown"] == 43


def test_missing_counts_are_not_faked_as_zero() -> None:
    """估算器没给「未知 / 不符」就是没给 —— 补 0 等于替它说了「确认为零」。"""
    payload = relax.plan_auto_relax({}, {}, estimator=lambda _f: {"estimated": 12}, target=1)
    assert payload["baseline"] == {"qualified": 12}


# ── 路由接线:台账真的到得了操作员眼前 ──────────────────────────────────────


def _route_harness(monkeypatch: pytest.MonkeyPatch, estimator: RecordingEstimator) -> list[dict[str, Any]]:
    """把搜索路由夹成只跑「拼筛选 → 放宽 → 挂台账」这一段,不碰库、不碰 provider。"""
    from app.api.routers import vkpi_kol_pool_search as route

    monkeypatch.setattr(
        route.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_a, **_kw: {
            "status": "ready",
            "search_query": "camera reviewer",
            "filter_proposal": {
                # 「美国」是操作员原话里的 -> 明确项;语言是推断的 -> 可放宽项。
                "filters": {"countries": ["us"], "languages": ["en"]},
                "relaxable_fields": ["languages"],
                "source": "model",
                "degraded": False,
            },
        },
    )
    monkeypatch.setattr(route.kol_search_auto_relax, "default_estimator", lambda: estimator)
    recall_calls: list[dict[str, Any]] = []

    def _recall(**kwargs: Any) -> dict[str, Any]:
        recall_calls.append(kwargs)
        return {
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "result_state": "empty", "empty_reason": "no_evidence_match"},
        }

    monkeypatch.setattr(route.kol_profile_recall, "recall_kol_profiles", _recall)
    monkeypatch.setattr(route, "_attach_smart_recall_session", lambda **kwargs: kwargs["result"])
    return recall_calls


def test_route_relaxes_the_inferred_filter_and_hands_the_ledger_to_the_operator(
    monkeypatch: pytest.MonkeyPatch, estimator: RecordingEstimator
) -> None:
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route

    recall_calls = _route_harness(monkeypatch, estimator)
    response = asyncio.run(
        route.smart_kol_search(
            {"auto_relax": True, "auto_filters": True, "input": "美国的镜头评测博主", "create_session": False, "gear_content": "yes", "result_limit": 12},
            staff={"id": 42},
        )
    )

    ledger = response["result"]["auto_relax"]
    assert ledger["schema"] == relax.SCHEMA
    assert ledger["status"] == relax.STATUS_RELAXED
    assert [step["key"] for step in ledger["applied"]] == ["languages"]
    assert ledger["advice_source"] == "model"

    applied_filters = recall_calls[0]["filters"]
    # 推断项被放宽成「确认是 + 未知」。
    assert applied_filters["languages"] == {"values": ["en"], "mode": "include_unknown"}
    # 明确项与合格线原封不动。
    assert applied_filters["countries"] == ["us"]
    assert applied_filters["gear_content"] == "yes"


def test_route_honours_the_operator_opt_out_end_to_end(
    monkeypatch: pytest.MonkeyPatch, estimator: RecordingEstimator
) -> None:
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route

    recall_calls = _route_harness(monkeypatch, estimator)
    response = asyncio.run(
        route.smart_kol_search(
            {"input": "美国的镜头评测博主", "create_session": False, "auto_relax": False, "auto_filters": True},
            staff={"id": 42},
        )
    )

    assert response["result"]["auto_relax"]["status"] == relax.STATUS_DISABLED
    assert recall_calls[0]["filters"]["languages"] == ["en"]
    assert estimator.calls == [], "关掉之后连一次 COUNT 都不该跑"
