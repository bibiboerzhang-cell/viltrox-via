"""车道A 契约测试(2026-08-25):planner 的「意图契约」重做。

坐实的原始事故:用户说「55evo e 卡口找一些消费群体多的推广人」,planner 产出

    "Sony E-mount portrait photographer natural light photography full frame
     prime lens creator street photographer lifestyle photography"

三处把用户的话理解错了:
  A1 「消费群体多」= **受众规模**,被译成题材词(lifestyle / street / portrait);
  A2 改写后的检索词**不含产品词**,证据闸的 intent 腿与产品腿吃两套完全分开的词表,
      人为制造跨词表 AND。prod a05e48dd3 只读复验(vkpi_kol_pool 全量 2034 人 + 代表作
      标题):无锚 70 人过闸、带锚 88 人过闸(1.26×,零人掉队)。
      注:早期口径写成「1744 人 78 → 334」/「109 → 298(2.7×)」均不可复现——2026-08-25
      复测证明那是把「人数」与「证据条数」两个指标混读(followers≥3000 且关掉产品腿时,
      带锚一次跑出的正是 109 人 / 298 条证据,二者同属「带锚」侧,并非前后对照);
  A3 一条长 query 同时服务向量召回 / YouTube 搜索 / IG 标签,对向量稀释、对平台搜索过长。

红线:本波只改「怎么理解用户的话」,不改「合格标准」——这里同时钉死粉丝下限建议值
永不低于 smart-local 策略的 min_followers,且操作员自己填的值永远压过 planner 建议。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import product_resolver, smart_query_planner
from app.domains.kol import smart_query_intent as intent
from app.domains.kol import profile_recall_match_evidence as match_evidence
from app.domains.kol import profile_recall_qualification as qualification


AUDIT_QUERY = "55evo e 卡口找一些消费群体多的推广人"

_LENS_CATALOG: list[dict[str, Any]] = [
    {
        "sku": "AF-55MM-F18-EVO-FE",
        "model_name": "Viltrox AF 55mm F1.8 EVO Full- Frame Lens for Sony E-Mount",
        "marketing_name": "Viltrox AF 55mm F1.8 EVO Full- Frame Lens for Sony E-Mount",
        "series": "EVO",
        "category_main": "Lens",
        "category_detail": "Prime Lens",
        "price_usd": 199,
        "description": "Full-frame autofocus prime lens for Sony E-mount",
    },
]

_LENS_PRODUCT = _LENS_CATALOG[0]


def _fake_catalog(rows: list[dict[str, Any]]):
    def _list(limit: int = 300, *, query: str = "", **_kwargs: Any) -> dict[str, Any]:
        needle = str(query or "").strip().lower()
        if not needle:
            return {"products": list(rows)[: int(limit)]}
        hits = [
            row
            for row in rows
            if needle
            in " ".join(
                str(row.get(field) or "").lower()
                for field in ("sku", "model_name", "marketing_name", "description")
            )
        ]
        return {"products": hits[: int(limit)]}

    return _list


@pytest.fixture()
def lens_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_resolver, "list_product_catalog", _fake_catalog(_LENS_CATALOG))
    # persona 知识库是另一条数据依赖,这里钉死为空以确定性地测规则/契约路径。
    monkeypatch.setattr(
        smart_query_planner, "_plan_from_product_persona", lambda *_a, **_kw: None
    )


def _llm_plan(**overrides: Any) -> dict[str, Any]:
    """事故当天 LLM 真实返回的那种 plan(一条长 query、无产品锚、题材词化的受众规模)。"""
    raw = {
        "search_query": (
            "Sony E-mount portrait photographer natural light photography full frame "
            "prime lens creator street photographer lifestyle photography"
        ),
        "product_focus": [
            "portrait photographer", "natural light", "street photography", "lifestyle",
        ],
        "target_persona": "Sony E-mount portrait shooters",
    }
    raw.update(overrides)
    return smart_query_planner._normalise_plan(
        AUDIT_QUERY,
        raw,
        {"provider": "google", "model": "gemini-2.5-flash", "status": "success"},
        _LENS_PRODUCT,
    )


# ── A1 · 受众规模落成阈值,不再变成题材词 ──────────────────────────────────────


def test_audience_scale_floor_matches_smart_local_policy() -> None:
    # 建议值的地板必须等于 smart-local 策略的粉丝下限;任一侧漂移都要在这里红。
    assert intent.AUDIENCE_SCALE_FLOOR == qualification.SMART_LOCAL_MIN_FOLLOWERS


@pytest.mark.parametrize(
    ("phrase", "tier"),
    [
        ("消费群体多", "large"),
        ("粉丝多", "large"),
        ("影响力大", "large"),
        ("受众广", "large"),
        ("large audience", "large"),
        ("big following", "large"),
        ("头部", "mega"),
        ("mega influencer", "mega"),
        ("腰部", "mid"),
        ("素人", "micro"),
    ],
)
def test_audience_phrases_become_a_scale_tier(phrase: str, tier: str) -> None:
    detected = intent.detect_audience_scale(f"找一些{phrase}的创作者")
    assert detected is not None
    assert detected["audience_scale"] == tier
    assert detected["min_followers_hint"] == intent.AUDIENCE_SCALE_TIERS[tier]


def test_audit_query_scale_lands_on_a_threshold_not_on_genre_words() -> None:
    plan = _llm_plan()
    assert plan["audience_scale"] == "large"
    assert plan["min_followers_hint"] == 100_000
    assert plan["audience_scale_source"] == "operator_text"
    # 事故本体:受众规模绝不能变成题材检索词。
    blob = " ".join([plan["search_query"], *plan["search_queries"]]).lower()
    for genre_word in ("audience", "following", "followers", "popular"):
        assert genre_word not in blob


def test_scale_words_never_survive_as_search_terms() -> None:
    kept = intent.strip_audience_scale_terms(
        ["portrait photographer", "large audience", "big following", "street photography"]
    )
    assert kept == ["portrait photographer", "street photography"]


def test_operator_followers_min_always_beats_the_planner_hint() -> None:
    plan = _llm_plan()
    assert plan["min_followers_hint"] == 100_000
    resolved = intent.resolve_audience_scale(plan, {"followers_min": 5_000})
    # 操作员填了 5000 就用 5000,planner 的 100000 建议不得覆盖(哪怕更"严")。
    assert resolved["applied_followers_min"] == 5_000
    assert resolved["source"] == "operator"
    assert resolved["hint_applied"] is False
    assert resolved["planner_hint"] == 100_000


def test_planner_hint_applies_only_when_operator_left_it_blank() -> None:
    plan = _llm_plan()
    for blank in ({}, {"followers_min": ""}, {"followers_min": None}, None):
        resolved = intent.resolve_audience_scale(plan, blank)
        assert resolved["applied_followers_min"] == 100_000
        assert resolved["source"] == "planner_hint"
        assert resolved["hint_applied"] is True


def test_no_scale_words_means_no_extra_gate() -> None:
    resolved = intent.resolve_audience_scale(_llm_plan(), {})
    assert resolved["applied_followers_min"] == 100_000
    quiet = smart_query_planner._normalise_plan(
        "给 55mm evo 找人像摄影师",
        {"search_query": "portrait photographer", "product_focus": ["portrait photographer"]},
        {"provider": "google", "model": "m", "status": "success"},
        _LENS_PRODUCT,
    )
    assert quiet["audience_scale"] == ""
    assert quiet["min_followers_hint"] is None
    assert intent.resolve_audience_scale(quiet, {})["applied_followers_min"] is None
    assert intent.resolve_audience_scale(quiet, {})["source"] == "policy_default"


def test_hint_is_clamped_up_to_the_floor_never_below() -> None:
    # 红线:planner 不得借"小号/素人"把粉丝下限压到 smart-local 的 min_followers 以下。
    normalised = intent.normalise_audience_scale("micro", 50)
    assert normalised["min_followers_hint"] == intent.AUDIENCE_SCALE_FLOOR
    assert intent.resolve_audience_scale(
        {"min_followers_hint": 10}, {}
    )["applied_followers_min"] == intent.AUDIENCE_SCALE_FLOOR


# ── A2 · search_query 必须携带产品锚 ─────────────────────────────────────────


def test_every_search_query_carries_brand_and_model(lens_catalog: None) -> None:
    plan = _llm_plan()
    anchor = intent.product_anchor(_LENS_PRODUCT, query_text=AUDIT_QUERY)
    assert anchor["brand"].lower() == "viltrox"
    assert "55mm" in [token.lower() for token in anchor["model_tokens"]]
    for query in plan["search_queries"]:
        lowered = query.lower()
        assert "viltrox" in lowered, query
        assert "55mm" in lowered, query
    # 向后兼容的单条 search_query 同样带锚,并以锚开头。
    assert plan["search_query"].lower().startswith("viltrox 55mm")
    assert intent.query_has_product_anchor(plan["search_query"], anchor)


def test_product_anchor_prefix_is_brand_model_mount(lens_catalog: None) -> None:
    anchor = intent.product_anchor(_LENS_PRODUCT, query_text=AUDIT_QUERY)
    assert anchor["mount"] == "Sony E-mount"
    assert anchor["prefix"] == "Viltrox 55mm EVO Sony E-mount"


def test_anchor_is_never_fabricated_without_a_resolved_product() -> None:
    anchor = intent.product_anchor(None)
    assert anchor["core"] == []
    assert anchor["prefix"] == ""
    # 空锚时任何 query 都算"带锚"(没有可核对的产品身份,不能凭空要求品牌词)。
    assert intent.query_has_product_anchor("automotive videographer", anchor) is True


def test_provider_free_product_path_also_carries_the_anchor(lens_catalog: None) -> None:
    plan = smart_query_planner.plan_text_query_provider_free(
        "55mm f1.8 evo 索尼口 找人像摄影师", body={}
    )
    assert plan["status"] != "needs_clarification"
    assert plan["resolved_product"]["sku"] == "AF-55MM-F18-EVO-FE"
    assert plan["search_queries"]
    for query in plan["search_queries"]:
        assert "viltrox" in query.lower() and "55mm" in query.lower()


# 事故当天 prod planner 真实产出的无锚检索词(A2 的对照组,勿改)。
_UNANCHORED_REWRITE = (
    "Sony E-mount portrait photographer natural light photography full frame "
    "prime lens creator street photographer lifestyle photography"
)
_PRODUCT_EVIDENCE_TERMS = match_evidence.product_evidence_terms(
    {"marketing_name": "Viltrox AF 55mm F1.8 EVO Sony E-mount"}
)
_GATE_CORPUS: list[dict[str, str]] = [
    # 只有品牌 + 一个题材词:无锚时 intent 腿只有 1 个证据,带锚后 viltrox 补上第二个。
    {"handle": "a", "bio": "Viltrox shooter, portrait work"},
    # 品牌 + 型号,题材词一个都没有:两侧都靠锚词自己凑 intent 腿。
    {"handle": "b", "bio": "reviewing the Viltrox 55mm on my channel"},
    # 题材词充足但完全没提产品:两侧都过不了产品腿。
    {"handle": "c", "bio": "street and lifestyle portrait work, natural light"},
    # 题材词充足 + 卡口语境:无锚时就能过,带锚不得把它挤掉。
    {"handle": "d", "bio": "Sony E-mount street portrait, natural light, full frame"},
    # 完全无关。
    {"handle": "e", "bio": "baking sourdough at home"},
]


def _gate_passers(query: str) -> set[str]:
    return {
        row["handle"]
        for row in _GATE_CORPUS
        if match_evidence.build_match_evidence(
            row, {}, query, required_product_terms=_PRODUCT_EVIDENCE_TERMS
        )
    }


def test_anchoring_only_adds_candidates_never_drops_one(lens_catalog: None) -> None:
    """锚词进检索词后,过闸集合只能是超集——一个人都不许被挤掉。

    2026-08-25 裁决:锚词若同时充当产品腿与意图腿,AND-2 就有一个槽位近乎白送
    (prod 实测 78→333,其中 87% 由品牌词 viltrox 一个词买来,而池内 66.7% 的
    资料本就写着它)。这是变相放宽,已在 build_match_evidence 里堵死:被产品腿
    消费过的词不再计入意图腿。因此**锚词的价值不在过闸**,而在召回侧——检索词
    带产品能让平台搜索与向量召回捞到更对的候选。这里只钉「零人掉队」这条底线。
    """
    before = _gate_passers(_UNANCHORED_REWRITE)
    after = _gate_passers(_llm_plan()["search_query"])
    assert before <= after, sorted(before - after)


def test_product_proof_word_cannot_also_prove_intent(lens_catalog: None) -> None:
    """一词不得两用:证明了产品腿的词,不得再充当意图腿的举证。"""
    # 资料里只有品牌词 + 一个意图词:品牌被产品腿吃掉后,意图腿只剩 1 个 → 不过闸。
    row = {"profile_text": "viltrox portrait"}
    proofs = match_evidence.build_match_evidence(
        row, {}, "Viltrox 55mm EVO portrait street photography",
        required_product_terms=_PRODUCT_EVIDENCE_TERMS,
    )
    assert proofs == [], "品牌词既算产品腿又算意图腿 = 变相把 AND-2 降成 AND-1"

    # 补上第二个真意图词后应当过闸,证明收紧的是「双重计数」而不是整体门槛。
    row_ok = {"profile_text": "viltrox portrait street photography"}
    assert match_evidence.build_match_evidence(
        row_ok, {}, "Viltrox 55mm EVO portrait street photography",
        required_product_terms=_PRODUCT_EVIDENCE_TERMS,
    ), "两个独立意图词 + 产品证据仍须过闸"


# ── A3 · 多条短查询取代一条长句 ──────────────────────────────────────────────


def test_search_queries_are_bounded_short_and_anchored(lens_catalog: None) -> None:
    plan = _llm_plan()
    queries = plan["search_queries"]
    assert 2 <= len(queries) <= 4
    for query in queries:
        assert len(query.split()) <= 6, query
    assert len({query.lower() for query in queries}) == len(queries)


def test_query_angles_differ_instead_of_repeating_synonyms(lens_catalog: None) -> None:
    anchor = intent.product_anchor(_LENS_PRODUCT, query_text=AUDIT_QUERY)
    core = set(intent.anchor_required_terms(anchor))
    queries = _llm_plan()["search_queries"]
    tails = [
        " ".join(word for word in query.lower().split() if word not in core) for query in queries
    ]
    # 每条 query 去掉产品锚之后的角度部分各不相同(不是同义重复)。
    assert len(tails) == len(set(tails)), tails
    assert all(tail for tail in tails), tails
    # 第一条角度是「产品 + 卡口」。
    assert "e-mount" in tails[0]
    # angle_terms 把锚(含卡口)剥干净,供重新装桶时只看角度内容。
    assert intent.angle_terms([queries[0]], anchor) == []


def test_single_search_query_stays_for_backward_compatibility(lens_catalog: None) -> None:
    plan = _llm_plan()
    assert isinstance(plan["search_query"], str) and plan["search_query"]
    # 兼容串不丢广度:短 query 装不下的题材词仍在合并串里,供未改造的下游消费。
    for term in ("portrait", "street", "lifestyle", "natural"):
        assert term in plan["search_query"].lower()


def test_llm_supplied_queries_are_renormalised_not_trusted_blindly(lens_catalog: None) -> None:
    plan = _llm_plan(
        search_queries=[
            "portrait photographer with a very long unanchored sentence about lenses",
            "street photography creators",
        ]
    )
    for query in plan["search_queries"]:
        assert len(query.split()) <= 6, query
        assert "viltrox" in query.lower() and "55mm" in query.lower(), query


def test_build_search_queries_degrades_to_topics_without_a_product() -> None:
    queries = intent.build_search_queries(
        intent.product_anchor(None), ["automotive videographer", "food creator", "wedding filmmaker"]
    )
    assert queries
    for query in queries:
        assert len(query.split()) <= 6
        assert "viltrox" not in query.lower()


# ── A4 · 规则路径(provider_free)与 LLM 路径结构一致 ─────────────────────────


_CONTRACT_KEYS = (
    "search_query", "search_queries", "audience_scale", "min_followers_hint",
    "audience_scale_source", "product_focus", "target_persona", "platforms",
)


def test_rule_path_and_llm_path_expose_the_same_contract_keys(lens_catalog: None) -> None:
    rule_plan = smart_query_planner._fallback_plan("找一些赛车方向的创作者")
    llm_plan = _llm_plan()
    for key in _CONTRACT_KEYS:
        assert key in rule_plan, key
        assert key in llm_plan, key
    assert isinstance(rule_plan["search_queries"], list) and rule_plan["search_queries"]
    for query in rule_plan["search_queries"]:
        assert len(query.split()) <= 6, query


def test_rule_path_also_reads_audience_scale_as_a_threshold() -> None:
    plan = smart_query_planner._fallback_plan("找一些粉丝多的美食创作者")
    assert plan["audience_scale"] == "large"
    assert plan["min_followers_hint"] == 100_000
    assert "food creator" in plan["search_query"]


def test_bare_evo_is_a_lens_family_not_a_300w_light() -> None:
    # A4 坐实的口径冲突:「55evo」是 55mm F1.8 EVO 镜头,过去被规则表翻成
    # "300W EVO portable lighting"——把用户的话理解成了另一个品类。
    plan = smart_query_planner._fallback_plan(AUDIT_QUERY)
    assert "300w" not in plan["search_query"].lower()
    assert "portable lighting" not in plan["search_query"].lower()


def test_real_wattage_still_reaches_the_lighting_terms() -> None:
    plan = smart_query_planner._fallback_plan("找 300W 闪光灯的评测博主")
    assert "300W" in plan["search_query"]
    assert "portable lighting" in plan["search_query"]
    assert "flash" in plan["search_query"]


def test_clarification_plan_keeps_the_contract_keys_empty() -> None:
    plan = smart_query_planner._clarification_plan("x", {"reason": "r", "message": "m"})
    assert plan["search_queries"] == []
    assert plan["audience_scale"] == ""
    assert plan["min_followers_hint"] is None
