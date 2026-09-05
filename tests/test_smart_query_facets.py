"""车道「模型提议筛选」契约测试(2026-08-25)。

用户原话:「我觉得我输入完之后最好是有个大模型计算,然后自动化选择输入,别总是 00000」。
线上真数(池 2036 人)坐实的漏斗:

    只勾美国 160 → +粉丝 1 万 75 → +粉丝 5 万 49 → +英语 **6** → +生活方式 **0**。

而规划器过去只产出检索词 / platforms / min_followers_hint,国家 / 语言 / 垂类三项
零产出 —— 恰恰是杀伤力最大的三刀全靠操作员自己勾。本测试钉死补上这三项之后的四条契约:

1. **明确项 vs 推断项分开**,且判定权只在操作员原话的规则手里,模型无权自封;
2. **语言默认「包含未知」**(池里 71.2% 的人 language 是空的,按「必须匹配」筛
   等于一提议就砍掉七成);
3. **降级标记诚实**(模型没给提议就如实标规则推荐,门面照此显示);
4. **既有产出字段逐字节不变**(检索词 / platforms / min_followers_hint / product_focus)。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import smart_query_facets as facets
from app.domains.kol import smart_query_planner as planner
from app.domains.kol.profile_recall_contract import SUPPORTED_RECALL_FILTERS
from app.domains.kol.profile_recall_filter_modes import TRI_STATE_MODES
from app.domains.kol.profile_vertical_lexicon import VERTICAL_KEYS


VAGUE_QUERY = "帮我找一批适合 55mm F1.8 EVO 的创作者"

#: 一个典型的已归一 plan:market=US 是 planner prompt 写死的默认值,**不是操作员说的**。
BASE_PLAN: dict[str, Any] = {
    "market": "US",
    "platforms": ["youtube", "instagram", "tiktok"],
    "min_followers_hint": 50000,
    "product_focus": ["filmmaker", "videographer", "lifestyle"],
    "fallback_used": False,
    "provider_calls_performed": False,
}


def _propose(query: str, plan: dict[str, Any] | None = None, raw: dict[str, Any] | None = None):
    return facets.propose_facets(query, dict(plan if plan is not None else BASE_PLAN), raw_plan=raw)


# ── 1. 明确项与推断项分开 ─────────────────────────────────────────────────────

def test_operator_named_country_is_explicit_and_locked():
    """「找美国的」= 操作员明确说的 → 锁死,自动松绑不许动。"""
    proposal = _propose("找美国的摄影师")
    country = proposal["facets"]["countries"]
    assert country["values"] == ["US"]
    assert country["origin"] == facets.ORIGIN_EXPLICIT
    assert country["source"] == facets.SOURCE_OPERATOR
    assert country["relaxable"] is False
    assert "美国" in country["evidence"]
    assert "countries" in proposal["locked_fields"]
    assert "countries" not in proposal["relaxable_fields"]


def test_operator_named_city_maps_to_its_country_instead_of_default_us():
    proposal = _propose("Find London photographers")
    country = proposal["facets"]["countries"]
    assert country["values"] == ["GB"]
    assert country["origin"] == facets.ORIGIN_EXPLICIT
    assert country["source"] == facets.SOURCE_OPERATOR
    assert country["relaxable"] is False


def test_default_market_country_is_inferred_and_relaxable():
    """操作员一个国家都没说时,US 只是默认主力市场 —— 必须标成推断项、可松。

    这正是「只勾美国就从 2036 掉到 160」那一刀的隐形源头:过去它被当成硬约束
    一路带下去,操作员却从没说过要美国人。
    """
    proposal = _propose(VAGUE_QUERY)
    country = proposal["facets"]["countries"]
    assert country["values"] == ["US"]
    assert country["origin"] == facets.ORIGIN_INFERRED
    assert country["relaxable"] is True
    assert proposal["locked_fields"] == []
    assert "countries" in proposal["relaxable_fields"]


def test_model_values_can_never_claim_to_be_operator_stated():
    """模型只提议**取值**;它自称「用户明确要求」一律不作数(否则松绑车道被冻死)。"""
    raw = {
        "filter_proposal": {
            "countries": ["gb"],
            "countries_explicit": True,          # 模型自封,必须被无视
            "origin": facets.ORIGIN_EXPLICIT,    # 同上
            "languages": ["en"],
            "verticals": ["video_creation"],
            "min_followers": 20000,
            "platforms": ["youtube"],
        }
    }
    proposal = _propose(VAGUE_QUERY, raw=raw)
    for field in facets.FACET_FIELDS:
        facet = proposal["facets"][field]
        assert facet["origin"] == facets.ORIGIN_INFERRED, field
        assert facet["relaxable"] is True, field
    assert proposal["locked_fields"] == []
    assert proposal["facets"]["countries"]["values"] == ["GB"]
    assert proposal["facets"]["countries"]["source"] == facets.SOURCE_MODEL


def test_operator_words_beat_model_values():
    """操作员点名了国家,模型提的另一个国家不许覆盖它。"""
    raw = {"filter_proposal": {"countries": ["de"]}}
    proposal = _propose("找美国的摄影师", raw=raw)
    assert proposal["facets"]["countries"]["values"] == ["US"]
    assert proposal["facets"]["countries"]["origin"] == facets.ORIGIN_EXPLICIT


def test_operator_can_explicitly_switch_a_filter_off():
    """「全球都行」也是明确表态:该项明确为空且锁死,模型不许再往里塞国家。"""
    raw = {"filter_proposal": {"countries": ["us"], "languages": ["en"]}}
    proposal = _propose("全球都行,不限语言,找拍视频的", raw=raw)
    assert proposal["facets"]["countries"]["values"] == []
    assert proposal["facets"]["countries"]["origin"] == facets.ORIGIN_EXPLICIT
    assert proposal["facets"]["languages"]["values"] == []
    assert proposal["facets"]["languages"]["origin"] == facets.ORIGIN_EXPLICIT
    assert "countries" not in proposal["filters"]
    assert "languages" not in proposal["filters"]


def test_verticals_explicit_from_operator_words_inferred_from_product():
    operator = _propose("找人像摄影和镜头评测的达人")
    vertical = operator["facets"]["verticals"]
    assert set(vertical["values"]) == {"portrait", "lens_review"}
    assert vertical["origin"] == facets.ORIGIN_EXPLICIT
    assert vertical["relaxable"] is False

    inferred = _propose(VAGUE_QUERY)["facets"]["verticals"]
    # product_focus 里的 filmmaker / videographer / lifestyle 是**推断**,不是操作员的话。
    assert inferred["origin"] == facets.ORIGIN_INFERRED
    assert inferred["relaxable"] is True
    assert set(inferred["values"]) <= set(VERTICAL_KEYS)


def test_min_followers_explicit_only_when_operator_talked_about_reach():
    explicit = _propose("找粉丝多的大号 filmmaker")["facets"]["min_followers"]
    assert explicit["origin"] == facets.ORIGIN_EXPLICIT
    assert explicit["relaxable"] is False
    assert explicit["value"] and explicit["value"] > 0

    inferred = _propose(VAGUE_QUERY)["facets"]["min_followers"]
    assert inferred["origin"] == facets.ORIGIN_INFERRED
    assert inferred["value"] == BASE_PLAN["min_followers_hint"]
    assert inferred["relaxable"] is True


def test_platforms_explicit_from_operator_text():
    explicit = _propose("在 youtube 上找 filmmaker")["facets"]["platforms"]
    assert explicit["values"] == ["youtube"]
    assert explicit["origin"] == facets.ORIGIN_EXPLICIT
    assert explicit["relaxable"] is False


def test_second_wave_platforms_require_explicit_user_scope():
    for query, expected in [("Find creators on X", ["x"]), ("找推特创作者", ["x"]),
                            ("Find Reddit reviewers", ["reddit"])]:
        facet = _propose(query)["facets"]["platforms"]
        assert facet["values"] == expected
        assert facet["origin"] == facets.ORIGIN_EXPLICIT
    proposed = _propose("找摄影师", raw={"filter_proposal": {"platforms": ["x", "reddit"]}})
    assert not set(proposed["facets"]["platforms"]["values"]) & {"x", "reddit"}
    assert "x" not in _propose("FUJIFILM X mount portrait creators")["facets"]["platforms"]["values"]


def test_all_three_platforms_is_reported_as_no_platform_filter():
    """planner 默认的「三平台全要」等于没在筛平台,如实报成不筛,不摆假的三选三。"""
    platforms = _propose(VAGUE_QUERY)["facets"]["platforms"]
    assert platforms["values"] == []
    assert platforms["origin"] == facets.ORIGIN_INFERRED
    assert "platforms" not in _propose(VAGUE_QUERY)["filters"]


# ── 2. 语言默认「包含未知」───────────────────────────────────────────────────

def test_language_defaults_to_include_unknown_even_when_operator_named_it():
    """语言这一刀要特别克制:池里 1450/2036(71.2%)的人 language 是空的。

    操作员点名的语言**取值**一个都不丢(仍是 explicit / locked),但**模式**默认
    「包含未知」—— 已知语言对不上的排除,没填的保留。按「必须匹配」筛只剩个位数。
    """
    facet = _propose("找说英语的美国摄影师")["facets"]["languages"]
    assert facet["values"] == ["en"]
    assert facet["origin"] == facets.ORIGIN_EXPLICIT
    assert facet["mode"] == facets.LANGUAGE_DEFAULT_MODE == "include_unknown"
    assert facet["relaxable"] is False


def test_language_from_model_also_defaults_to_include_unknown():
    facet = _propose(VAGUE_QUERY, raw={"filter_proposal": {"languages": ["EN"]}})["facets"]["languages"]
    assert facet["values"] == ["en"]
    assert facet["mode"] == "include_unknown"
    assert facet["origin"] == facets.ORIGIN_INFERRED


def test_country_keeps_require_mode():
    """用户已拍板「勾『美国』就是要美国人」—— 国家不许偷偷改成未知放行。"""
    assert facets.COUNTRY_DEFAULT_MODE == "require"
    assert _propose("找美国的摄影师")["facets"]["countries"]["mode"] == "require"


def test_every_mode_is_a_known_tri_state_mode():
    proposal = _propose("找说英语的美国人像摄影师")
    for field in facets.FACET_FIELDS:
        assert proposal["facets"][field]["mode"] in TRI_STATE_MODES, field


# ── 3. 降级标记诚实 ───────────────────────────────────────────────────────────

def test_rule_fallback_is_labelled_as_rule_and_degraded():
    plan = {**BASE_PLAN, "fallback_used": True, "reason": "rule_fallback"}
    proposal = _propose(VAGUE_QUERY, plan=plan)
    assert proposal["source"] == facets.SOURCE_RULE
    assert proposal["degraded"] is True
    assert proposal["degraded_reason"] == "rule_fallback"
    assert "规则" in proposal["notice"]


def test_model_backed_proposal_is_labelled_as_model_and_not_degraded():
    plan = {**BASE_PLAN, "provider_calls_performed": True}
    proposal = _propose(VAGUE_QUERY, plan=plan, raw={"filter_proposal": {"countries": ["us"]}})
    assert proposal["source"] == facets.SOURCE_MODEL
    assert proposal["degraded"] is False
    assert "规则" not in proposal["notice"]


def test_model_call_without_usable_proposal_is_honestly_degraded():
    """真发生过一次调用却没拿到可用提议 —— 不许假装是模型算的。"""
    plan = {**BASE_PLAN, "provider_calls_performed": True}
    proposal = _propose(VAGUE_QUERY, plan=plan, raw={"search_query": "x"})
    assert proposal["source"] == facets.SOURCE_RULE
    assert proposal["degraded"] is True


def test_provider_free_first_screen_is_not_a_degradation():
    """首屏免调用是设计好的路径,不是事故,不许在门面上喊降级。"""
    proposal = _propose(VAGUE_QUERY, plan={**BASE_PLAN, "fallback_used": False})
    assert proposal["source"] == facets.SOURCE_RULE
    assert proposal["degraded"] is False
    assert "没能" not in proposal["notice"]


def test_operator_facing_text_carries_no_internal_jargon_or_vendor_names():
    banned = ("llm", "gpt", "gemini", "claude", "anthropic", "openai", "rule_v0", "fallback", "provider", "lexicon")
    proposal = _propose("找说英语的美国人像大号", raw={"filter_proposal": {"countries": ["us"]}})
    blobs = [proposal["notice"]]
    for facet in proposal["facets"].values():
        blobs.extend([facet["evidence"], facet["note"]])
    for blob in blobs:
        lowered = blob.lower()
        for word in banned:
            assert word not in lowered, f"门面文案出现内部术语 {word!r}: {blob}"


# ── 4. 产出可直接喂召回,且既有字段行为不变 ────────────────────────────────────

def test_filters_block_only_uses_supported_recall_filter_keys():
    proposal = _propose("找说英语的美国人像摄影师,粉丝多的,在 youtube")
    assert set(proposal["filters"]) <= set(SUPPORTED_RECALL_FILTERS)
    # 三态项按既有形态出:{"values": [...], "mode": ...}
    assert proposal["filters"]["languages"] == {"values": ["en"], "mode": "include_unknown"}
    assert proposal["filters"]["countries"] == ["US"]


def test_filters_block_never_touches_quality_gates():
    """红线:只提议操作员的筛选偏好。新鲜度 / 器材证据 / 产品锚 / 账号安全一个都不许出现。"""
    proposal = _propose("找说英语的美国人像摄影师,粉丝多的")
    forbidden = {"gear_content", "freshness_days", "fresh_days", "evidence_terms", "product_anchor", "brand_safety"}
    assert set(proposal["filters"]).isdisjoint(forbidden)
    assert set(proposal["facets"]) == set(facets.FACET_FIELDS)


def test_model_supplied_unknown_vertical_key_is_dropped():
    raw = {"filter_proposal": {"verticals": ["video_creation", "not_a_real_vertical"]}}
    assert _propose(VAGUE_QUERY, raw=raw)["facets"]["verticals"]["values"] == ["video_creation"]


@pytest.mark.parametrize(
    "query, expected",
    [
        (
            "找美国的 55mm 人像摄影师",
            {
                    "search_query": "portrait photographer",
                "product_focus": ["portrait", "portrait photographer", "lens review", "videographer", "photographer", "camera gear"],
                "platforms": ["youtube", "instagram", "tiktok"],
                "min_followers_hint": None,
                "audience_scale": "",
                "market": "US",
            },
        ),
    ],
)
def test_existing_plan_fields_unchanged(query: str, expected: dict[str, Any]):
    """筛选提议不能改变 people-first 计划的其余稳定字段。"""
    plan = planner._fallback_plan(query)
    for key, value in expected.items():
        assert plan[key] == value, key
    assert plan["provider"] == "rule_v0"
    assert plan["fallback_used"] is True


def test_filter_proposal_is_purely_additive_on_every_plan_path():
    """三条产 plan 的路径都挂上提议,且只多这一个键。"""
    rule_plan = planner._fallback_plan("找美国的 55mm 人像摄影师")
    assert rule_plan["filter_proposal"]["facets"]["countries"]["origin"] == facets.ORIGIN_EXPLICIT

    normalised = planner._normalise_plan(
        "在 youtube 上找 55mm 创作者",
        {"search_query": "Viltrox 55mm portrait", "filter_proposal": {"countries": ["de"]}},
        {"provider": "p", "model": "m", "status": "success"},
        None,
    )
    proposal = normalised["filter_proposal"]
    assert proposal["facets"]["platforms"]["values"] == ["youtube"]
    assert proposal["facets"]["platforms"]["origin"] == facets.ORIGIN_EXPLICIT
    assert proposal["facets"]["countries"]["values"] == ["DE"]
    assert proposal["facets"]["countries"]["origin"] == facets.ORIGIN_INFERRED
    # 既有键一个不少
    for key in ("search_query", "search_queries", "platforms", "min_followers_hint", "product_focus"):
        assert key in normalised


def test_cached_plan_without_proposal_is_backfilled_by_rules(monkeypatch: pytest.MonkeyPatch):
    """7 天缓存里那些旧计划没有筛选提议 —— 就地按规则补,而不是作废缓存重烧一轮钱。"""
    import json
    from datetime import datetime, timezone

    from app.domains.analysis import cache_repo

    cached = {
        "status": "ready",
        "search_query": "Viltrox 55mm portrait creator",
        "platforms": ["youtube", "instagram", "tiktok"],
        "market": "US",
        "product_focus": ["portrait photographer"],
        "fallback_used": False,
    }
    monkeypatch.setattr(
        cache_repo,
        "get_analysis_cache_entry",
        lambda *args, **kwargs: {
            "status": "ready",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "result": json.dumps(cached, ensure_ascii=False),
        },
    )
    plan = planner.plan_text_query("找美国的 55mm 人像摄影师")
    assert plan["plan_cache"] == "hit"
    proposal = plan["filter_proposal"]
    assert proposal["source"] == facets.SOURCE_RULE
    assert proposal["facets"]["countries"]["origin"] == facets.ORIGIN_EXPLICIT
    assert proposal["facets"]["countries"]["values"] == ["US"]


def test_empty_query_still_returns_a_complete_proposal():
    proposal = facets.propose_facets("", {})
    assert proposal["status"] == "ready"
    assert set(proposal["facets"]) == set(facets.FACET_FIELDS)
    assert proposal["locked_fields"] == []
    assert proposal["filters"] == {}
