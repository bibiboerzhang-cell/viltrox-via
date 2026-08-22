"""GTM-1 strategy_sim 补参 golden:country/goal 可选参 + 无参旧行为逐字一致。

fake conn + 全引擎打桩喂固定行(决定性),断言三件事:
1. 无参调用与显式 country=None/goal=None 调用逐字一致(json 全量比对),
   且不泄漏任何新字段(goal/country/audience_geo_match/country_filter/
   audience_geo_filter),推荐 rule 原文与旧版逐字相同 —— 向后兼容红线;
   无参路径绝不调用 audience_geo(打桩即炸证明零多余读)。
2. 同输入不同 goal 推荐排序变化:conversion=默认序(每千播成本升序)、
   exposure=去重触达降序、content=成员人数降序 —— 三种排序两两不同;
   goal 只动推荐排序,三方案选人逐字不变;未知 goal 按默认口径 + goal_note 诚实。
3. country=US:候选按受众地域三档降序(命中>无数据>不命中),头部选人改变;
   无数据候选不淘汰只降序(仍在长尾方案里);basis/摘要计数如实。

固定行设计(预算 900,max_roster 20):
  K1 macro cost500 views100k US80% | K2 macro cost400 views40k DE90%(US 不命中)
  K3 nano  cost200 views1k  geo读挂(无数据) | K4 nano cost210 views3k US60%
  K5 micro cost220 views8k  geo空(无数据)   | K6 micro cost230 views2k JP100%(不命中)
无参三方案:head=[1,2] cpm6.43 / longtail=[3,4,5,6] cpm61.43 / hybrid=[2,3,4] cpm18.41。
"""
import json

from app.domains.market import strategy_sim as ss

SKU = "SKU-GOLD"
BUDGET = 900.0
FROZEN_TS = "2026-07-07T00:00:00+00:00"

COST = {1: 500.0, 2: 400.0, 3: 200.0, 4: 210.0, 5: 220.0, 6: 230.0}
VIEWS = {1: 100_000.0, 2: 40_000.0, 3: 1_000.0, 4: 3_000.0, 5: 8_000.0, 6: 2_000.0}
FOLLOWERS = {1: 500_000, 2: 300_000, 3: 5_000, 4: 8_000, 5: 50_000, 6: 60_000}
REACH = {1: 100.0, 2: 200.0, 3: 3000.0, 4: 100.0, 5: 50.0, 6: 60.0}
GEO = {  # kol_pool_id → 受众分布;3=读挂(raise),5=空分布 → 均按"无数据"档
    1: [("US", 80.0)],
    2: [("DE", 90.0)],
    4: [("US", 60.0)],
    5: [],
    6: [("JP", 100.0)],
}

# 旧版推荐 rule 原文(逐字):无参路径必须一字不差
OLD_RULE = (
    "按 p50 成本效率(每千播放成本)升序推荐;「全员 CPM 兜底价」方案降级 "
    "estimate_only 一律后置(对齐第4轮护栏精神:基准折算非成交价)。"
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, routes):
        self.routes = routes

    def execute(self, sql, params=()):
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])


class _FakeRateCard:
    @staticmethod
    def _tier_for_followers(followers):
        if followers < 10_000:
            return "nano"
        if followers < 100_000:
            return "micro"
        return "macro"

    @staticmethod
    def estimate_rate(kid, conn=None):
        cost = COST[kid]
        return {
            "status": "ready",
            "estimated_usd_p50": cost,
            "estimated_usd_low": cost * 0.8,
            "estimated_usd_high": cost * 1.2,
            "method": "direct_quote_v0",  # 非 cpm_benchmark_v0 → 不触发 estimate_only 降级
            "confidence": "high",
        }


class _FakeForecast:
    @staticmethod
    def forecast_for_kol(kid, sku=None, conn=None, dry_run=False):
        assert dry_run is True
        views = VIEWS[kid]
        return {
            "status": "ready",
            "expected_views_p10": views * 0.5,
            "expected_views_p50": views,
            "expected_views_p90": views * 2.0,
            "confidence": "high",
        }


class _FakeRoster:
    @staticmethod
    def optimize_roster(ids, max_size=None):
        total = float(sum(REACH[i] for i in ids))
        return {
            "status": "ok",
            "coverage": {"total_dedup_reach": total, "raw_reach_sum": total, "dedup_saved_pct": 0.0},
            "confidence": "medium",
            "method": "fake_roster_v0",
        }


def _fake_audience_geo(kol_pool_id, *, conn=None):
    if kol_pool_id == 3:
        raise LookupError("comment rows unavailable")  # 单人读挂 → 无数据档,不拖垮
    dist = GEO[kol_pool_id]
    return {
        "status": "ready",
        "countries": [{"code": code, "name": code, "pct": pct} for code, pct in dist],
        "confidence": "medium",
    }


def _setup(monkeypatch, *, audience_geo=None):
    pool_rows = [
        {"id": k, "followers": FOLLOWERS[k], "avg_views": None, "platform": "youtube"}
        for k in sorted(COST)
    ]
    conn = _Conn({"FROM vkpi_kol_pool": pool_rows})
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    monkeypatch.setattr(ss, "_load_engines", lambda: (_FakeRateCard, _FakeForecast, _FakeRoster, ""))
    monkeypatch.setattr(ss, "_utcnow_iso", lambda: FROZEN_TS)
    items = [
        {"kol_pool_id": k, "handle": f"kol{k}", "display_name": f"KOL {k}",
         "platform": "youtube", "country": "", "score": 50}
        for k in sorted(COST)
    ]
    monkeypatch.setattr(
        "app.domains.projects.launch_assembly._resolve_product",
        lambda sku: {"sku": SKU, "name": "Golden Lens"},
    )
    monkeypatch.setattr(
        "app.domains.projects.launch_assembly._product_basic",
        lambda product: {"sku": SKU, "name": "Golden Lens"},
    )
    monkeypatch.setattr(
        "app.domains.projects.launch_assembly._candidate_pool",
        lambda sku, limit: {"status": "ok", "reason": "", "items": items, "basis": {"note": "fake"}},
    )
    if audience_geo is None:
        def _forbidden(kol_pool_id, *, conn=None):
            raise AssertionError("audience_geo 不应在无 country 参数时被调用")
        audience_geo = _forbidden
    monkeypatch.setattr("app.domains.audience.geo_ensemble.audience_geo", audience_geo)


def _ranking_keys(payload):
    return [r["key"] for r in payload["recommendation"]["ranking"]]


def _members(payload, plan_key):
    plan = next(p for p in payload["plans"] if p["key"] == plan_key)
    return [m["kol_pool_id"] for m in plan["members"]]


# ── 1. 无参 = 旧行为逐字一致 ─────────────────────────────────────────


def test_noparam_identical_to_explicit_none_and_no_new_fields(monkeypatch):
    _setup(monkeypatch)
    plain = ss.simulate(SKU, BUDGET)
    explicit = ss.simulate(SKU, BUDGET, country=None, goal=None)
    # 逐字一致(时间已冻结):country=None/goal=None 与不传完全同一输出
    assert json.dumps(plain, ensure_ascii=False, sort_keys=True) == json.dumps(
        explicit, ensure_ascii=False, sort_keys=True
    )
    # 新字段零泄漏:无参输出与旧版键结构一致
    assert "goal" not in plain and "country" not in plain
    assert "goal_note" not in plain and "country_note" not in plain
    assert "audience_geo_filter" not in plain["candidate_pool"]
    for plan in plain["plans"]:
        assert "country_filter" not in (plan.get("basis") or {})
        for member in plan.get("members", []):
            assert "audience_geo_match" not in member
    rec = plain["recommendation"]
    assert "goal" not in rec and "basis" not in rec
    # 旧版推荐 rule 原文逐字相同 + 排名行键集不变
    assert rec["rule"] == OLD_RULE
    assert set(rec["ranking"][0]) == {
        "rank", "key", "name", "cost_per_1k_views_p50", "estimate_only", "risk_level",
    }
    # golden:选人与推荐序(固定行手算)
    assert _members(plain, "head_concentration") == [1, 2]
    assert _members(plain, "longtail_spread") == [3, 4, 5, 6]
    assert _members(plain, "hybrid_5050") == [2, 3, 4]
    assert rec["recommended_key"] == "head_concentration"
    assert _ranking_keys(plain) == ["head_concentration", "hybrid_5050", "longtail_spread"]
    assert [r["cost_per_1k_views_p50"] for r in rec["ranking"]] == [6.43, 18.41, 61.43]
    # 决定性:再跑一遍逐字相同
    assert json.dumps(ss.simulate(SKU, BUDGET), ensure_ascii=False, sort_keys=True) == json.dumps(
        plain, ensure_ascii=False, sort_keys=True
    )


# ── 2. goal 切换推荐排序(选人不动) ─────────────────────────────────


def test_goal_switches_recommendation_ranking(monkeypatch):
    _setup(monkeypatch)
    base = ss.simulate(SKU, BUDGET)
    conversion = ss.simulate(SKU, BUDGET, goal="conversion")
    exposure = ss.simulate(SKU, BUDGET, goal="exposure")
    content = ss.simulate(SKU, BUDGET, goal="content")

    # conversion = 现状默认口径:排序与无参一致
    assert _ranking_keys(conversion) == _ranking_keys(base)
    assert conversion["goal"] == "conversion"
    assert conversion["recommendation"]["goal"] == "conversion"

    # exposure:去重触达降序 head=300 / longtail=3210 / hybrid=3300
    assert _ranking_keys(exposure) == ["hybrid_5050", "longtail_spread", "head_concentration"]
    assert exposure["recommendation"]["recommended_key"] == "hybrid_5050"
    assert "total_dedup_reach=3300" in exposure["recommendation"]["ranking"][0]["goal_metric"]

    # content:成员人数降序 longtail=4 / hybrid=3 / head=2
    assert _ranking_keys(content) == ["longtail_spread", "hybrid_5050", "head_concentration"]
    assert content["recommendation"]["recommended_key"] == "longtail_spread"
    assert "member_count=4" in content["recommendation"]["ranking"][0]["goal_metric"]

    # 三种 goal 排序两两不同(同输入不同 goal 排序变化)
    orders = {tuple(_ranking_keys(p)) for p in (conversion, exposure, content)}
    assert len(orders) == 3

    # goal 只动推荐排序:三方案选人逐字不变
    for payload in (conversion, exposure, content):
        for key in ("head_concentration", "longtail_spread", "hybrid_5050"):
            assert _members(payload, key) == _members(base, key)

    # basis 写明 goal 如何影响排序
    for payload, goal in ((exposure, "exposure"), (content, "content"), (conversion, "conversion")):
        basis = payload["recommendation"]["basis"]
        assert basis["goal_weighting"] == ss.GOAL_RULES[goal]
        assert payload["recommendation"]["rule"] == ss.GOAL_RULES[goal]


def test_unknown_goal_falls_back_to_default_with_note(monkeypatch):
    _setup(monkeypatch)
    base = ss.simulate(SKU, BUDGET)
    channel = ss.simulate(SKU, BUDGET, goal="channel")
    assert channel["goal"] == "channel"
    assert "goal_note" in channel  # 诚实说明:未知 goal 按默认口径
    # 推荐块与无参逐字一致(默认 conversion 口径,无 goal 字段)
    assert json.dumps(channel["recommendation"], ensure_ascii=False, sort_keys=True) == json.dumps(
        base["recommendation"], ensure_ascii=False, sort_keys=True
    )


# ── 3. country 受众地域三档过滤(无数据不淘汰只降序) ─────────────────


def test_country_reorders_pool_without_dropping_no_data(monkeypatch):
    _setup(monkeypatch, audience_geo=_fake_audience_geo)
    base = ss.simulate(SKU, BUDGET)
    us = ss.simulate(SKU, BUDGET, country="US")

    assert us["country"] == "US"
    # 头部选人改变:US 受众命中档优先(K1 80% + K4 60%),K2(DE)被降序挤出
    assert _members(base, "head_concentration") == [1, 2]
    assert _members(us, "head_concentration") == [1, 4]
    # 长尾:命中档 K4 提到最前;无数据的 K3/K5 与不命中的 K6 不淘汰只降序
    assert _members(us, "longtail_spread") == [4, 3, 5, 6]
    # 混合:同一三档序作用于头尾两段
    assert _members(us, "hybrid_5050") == [4, 5, 3, 6]

    # 摘要计数如实:命中2 / 无数据2(读挂+空分布) / 不命中2
    summary = us["candidate_pool"]["audience_geo_filter"]
    assert summary["status"] == "applied"
    assert (summary["matched"], summary["no_data"], summary["unmatched"]) == (2, 2, 2)

    # 成员带受众命中标注(仅 country 生效时出现)
    head_members = next(p for p in us["plans"] if p["key"] == "head_concentration")["members"]
    tags = {m["kol_pool_id"]: m["audience_geo_match"] for m in head_members}
    assert tags[1]["status"] == "matched" and tags[1]["match_pct"] == 80.0
    assert tags[4]["status"] == "matched" and tags[4]["match_pct"] == 60.0
    longtail_members = next(p for p in us["plans"] if p["key"] == "longtail_spread")["members"]
    ltags = {m["kol_pool_id"]: m["audience_geo_match"] for m in longtail_members}
    assert ltags[3]["status"] == "no_data" and ltags[3]["match_pct"] is None
    assert ltags[6]["status"] == "not_matched" and ltags[6]["match_pct"] == 0.0

    # basis 写明 country 如何影响排序
    for plan in us["plans"]:
        cf = plan["basis"]["country_filter"]
        assert cf["country"] == "US"
        assert cf["rule"] == ss.COUNTRY_RULE_TEXT
        assert cf["counts"] == {"matched": 2, "no_data": 2, "unmatched": 2}
