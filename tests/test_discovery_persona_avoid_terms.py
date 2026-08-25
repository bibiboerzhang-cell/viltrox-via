"""persona avoid_types 误杀契约测试(A1,2026-08-25)。

病根(prod a05e48dd3 只读回放 vkpi_kol_pool 2034 人真池坐实):avoid_types 是 LLM 写的
人话短语(如 "cinematographer shooting cinema lenses"),旧口径按分隔符**拆成单词**再用
**裸子串**匹配、命中即丢弃 —— cinema 连坐 cinematic/cinematography(154 人)、lenses 连坐
lens 系(59 人)、"still-photography-only photographer" 拆出的 only 连坐 38 人,合计
409/2034(20.1%)被静默误杀;规则兜底那组更狠,"camera store unboxing channel" 拆出的
channel 一词独杀 318 人(388/2034)。

修后三条口径,本文件逐条钉死:
① 负词匹配单位 = LLM 原本写的**整短语**(_persona_phrase_list,只切真列表分隔符);
② 匹配走**词边界**(_term_hit,口径同 profile_recall_match_evidence._contains_term);
③ persona 负词**不再丢弃**,降级为 _persona_relevance 排序扣分 + persona_avoid_hits 透出;
   真丢弃只留给 _HARD_AVOID_TERMS 静态高精词表,且该词表同样改词边界匹配。
red line:纯候选层 FILTER/展示信号,零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import pytest

from app.domains.kol.discovery_filters import (
    PERSONA_AVOID_DROP_ENV,
    _is_hard_avoid,
    _persona_avoid_terms,
    _persona_positive_terms,
    _persona_relevance,
    _persona_term_list,
    _term_hit,
)


# 组A:prod 真实 session 的 avoid_types(误杀 409 人那组)。
AVOID_REAL = [
    "cinematographer shooting cinema lenses",
    "drone-only creator",
    "smartphone-only creator",
    "studio lighting specialist",
    "sports/action broadcast videographer",
]
# 组B:smart_query_planner._avoid_types_for_product 的 cine 分支规则兜底(误杀 388 人那组)。
AVOID_RULE = [
    "generic gear reviewer",
    "still-photography-only photographer",
    "phone vlogger",
    "camera store unboxing channel",
]


def _item(**kw):
    base = {"sample_title": "", "channel_name": "", "handle": "", "bio": ""}
    base.update(kw)
    return base


# ── ① 整短语:负词不再被拆成单词 ────────────────────────────────────────────────


def test_avoid_terms_keep_whole_phrase() -> None:
    """LLM 写的短语整条留着当匹配单位,绝不再拆出 cinema / lenses / only 这类连坐词。"""
    terms = _persona_avoid_terms(AVOID_REAL)
    assert "cinematographer shooting cinema lenses" in terms
    assert "studio lighting specialist" in terms
    for killer in ("cinema", "lenses", "only", "studio", "lighting", "action", "sports"):
        assert killer not in terms, f"{killer} 又被拆出来了,连坐误杀会复发"


def test_avoid_terms_split_only_on_real_list_separators() -> None:
    """真列表分隔符(逗号/顿号/分号/竖线/换行)才切;空格与连字符是短语内部结构,绝不切。"""
    assert _persona_avoid_terms("drone-only creator, phone vlogger") == [
        "drone-only creator",
        "phone vlogger",
    ]
    assert _persona_avoid_terms(["aa; bb|cc\ndd"]) == ["aa", "bb", "cc", "dd"]
    # JSON 串形态与 dict 形态照旧兜底(归一逻辑与正词共用)。
    assert _persona_avoid_terms('["studio lighting specialist"]') == ["studio lighting specialist"]


def test_positive_terms_still_tokenised() -> None:
    """正词口径**故意不动**:命中是加分不是杀人,失败方向本就安全。"""
    pos = _persona_positive_terms(["portrait photographer"], None, None, "street photographer")
    assert "portrait" in pos and "street" in pos
    assert _persona_term_list(["aa-bb cc"]) == ["aa", "bb", "cc"]


# ── ② 词边界:cinema ≠ cinematic,lenses ≠ lens ──────────────────────────────


def test_term_hit_word_boundary_separates_cinema_from_cinematic() -> None:
    assert _term_hit("shot on cinema camera", "cinema") is True
    assert _term_hit("cinematic travel films", "cinema") is False
    assert _term_hit("cinematography reel", "cinema") is False
    assert _term_hit("cinematographer based in berlin", "cinema") is False


def test_term_hit_word_boundary_separates_lens_family() -> None:
    assert _term_hit("i review lenses", "lenses") is True
    assert _term_hit("prime lens reviews", "lenses") is False
    assert _term_hit("lensbaby fan", "lens") is False


def test_term_hit_only_does_not_swallow_compounds() -> None:
    assert _term_hit("my only official channel", "only") is True
    assert _term_hit("onlyfans link in bio", "only") is False


def test_term_hit_multiword_phrase_tolerates_whitespace_and_cjk() -> None:
    assert _term_hit("real  estate\nmedia specialist", "real estate") is True
    assert _term_hit("unrealestate", "real estate") is False
    # CJK 无词边界 → 直接子串(与 _contains_term 同款分支)。
    assert _term_hit("我是一名摄影师", "摄影") is True
    assert _term_hit("", "cinema") is False
    assert _term_hit("cinema", "") is False


# ── ③ 降级:persona 负词只扣分不丢弃,真丢弃只留给静态高精词表 ──────────────────


@pytest.mark.parametrize(
    "handle, name, bio",
    [
        # 全部取自 prod 2034 人池里**被旧口径真丢掉**的人(见任务报告名单)。
        ("juliatrotti", "Julia Trotti", "portrait photographer based in Sydney, photoshoot behind the scenes, camera + lens reviews"),
        ("brandonli", "Brandon Li", "I'm a nomadic filmmaker on an endless world tour."),
        ("theartofphotography", "The Art of Photography", "Ted Forbes, I make videos about photography"),
        ("mathphotographer", "mathphotographer", "camera reviews, lens reviews, smartphone photography, technology & gear"),
        ("UCB9lqgmd6feEFpNahvu7RqQ", "Jay Soundo", "photographer based in the UK, portraits, commercial campaigns, football photography"),
        ("mikita_yo", "Mikita Yo", "Travel Filmmaking Cinematic"),
        ("slrlounge", "SLR Lounge | Photography Tutorials", "where working photographers learn to turn real skill into a business"),
        ("davidmanningvlog", "David Manning", "Camera, drone, action cam, and mobile filmmaking reviews for creators"),
    ],
)
def test_real_creators_no_longer_hard_avoided(handle, name, bio) -> None:
    """被旧口径误杀的真摄影/影视创作者,修后一律不再丢弃(两组 avoid_types 都试)。"""
    item = _item(handle=handle, channel_name=name, bio=bio)
    for avoid in (AVOID_REAL, AVOID_RULE):
        assert _is_hard_avoid(item, _persona_avoid_terms(avoid)) is False


def test_rule_fallback_channel_token_no_longer_kills_everyone() -> None:
    """规则兜底组里 'camera store unboxing channel' 曾拆出 channel 独杀 318 人。"""
    item = _item(channel_name="Photo Tutorials", bio="Welcome to my channel, photography tutorials")
    assert _is_hard_avoid(item, _persona_avoid_terms(AVOID_RULE)) is False


def test_persona_avoid_is_penalty_not_drop() -> None:
    """负词就算整短语真命中,也只扣分 + 透出,绝不丢弃。"""
    neg = _persona_avoid_terms(AVOID_REAL)
    item = _item(channel_name="Studio Lighting Specialist", bio="studio lighting specialist for beauty brands")
    assert _is_hard_avoid(item, neg) is False
    scored = _persona_relevance(item, pos_terms=["portrait"], neg_terms=neg)
    assert scored["persona_avoid_hits"] == ["studio lighting specialist"]
    clean = _persona_relevance(
        _item(channel_name="Portrait Studio", bio="portrait photographer"),
        pos_terms=["portrait"], neg_terms=neg,
    )
    assert clean["persona_avoid_hits"] == []
    assert scored["relevance_score"] < clean["relevance_score"]


def test_persona_relevance_negative_uses_word_boundary() -> None:
    """扣分侧同样走词边界:cinematic 创作者不再被 cinema 系负词扣成低分沉底。"""
    neg = _persona_avoid_terms(AVOID_REAL)
    cinematic = _persona_relevance(
        _item(channel_name="Mikita Yo", bio="travel filmmaking cinematic"),
        pos_terms=["travel"], neg_terms=neg,
    )
    assert cinematic["persona_avoid_hits"] == []
    assert cinematic["relevance_score"] > 0.3
    empty = _persona_relevance(_item(bio="anything"), pos_terms=[], neg_terms=[])
    assert empty["persona_avoid_hits"] == []


def test_persona_avoid_drop_env_restores_old_drop_behaviour() -> None:
    """应急回滚闸:置 1 才恢复丢弃,且丢弃口径也是整短语+词边界(不是旧的拆词裸子串)。"""
    neg = _persona_avoid_terms(AVOID_REAL)
    hit = _item(channel_name="Studio Lighting Specialist", bio="studio lighting specialist")
    innocent = _item(channel_name="Julia Trotti", bio="portrait photographer, cinematic lens reviews")
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv(PERSONA_AVOID_DROP_ENV, "1")
        assert _is_hard_avoid(hit, neg) is True
        assert _is_hard_avoid(innocent, neg) is False
    finally:
        monkey.undo()
    assert _is_hard_avoid(hit, neg) is False  # env 复位 → 回到默认「只扣分」


# ── 真该避开的人仍被避开:静态高精词表照旧丢弃 ────────────────────────────────


@pytest.mark.parametrize(
    "bio",
    [
        "Scentsy independent consultant, fragrance and candle lover",
        "Craft beer and brewery tours every week",
        "Real estate media specialist serving realtors",
        "Crypto and forex signals, NFT drops daily",
        "Weight loss supplement coach, diet plans",
        "Luxury jewelry brand ambassador",
    ],
)
def test_static_hard_avoid_still_drops(bio) -> None:
    assert _is_hard_avoid(_item(channel_name="Some Creator", bio=bio), []) is True


def test_static_hard_avoid_word_boundary_fixes_latent_substring_traps() -> None:
    """静态词表改词边界后,scent/political/wine 不再连坐 crescent/apolitical/winery。

    prod 2034 人池实测:裸子串与词边界两口径丢弃集**完全一致**(各 9 人),
    即本改动在真数据上零行为差,只拆掉后续新增词表项时的子串地雷。"""
    assert _is_hard_avoid(_item(bio="crescent moon night photography"), []) is False
    assert _is_hard_avoid(_item(bio="descent into the canyon, landscape work"), []) is False
    assert _is_hard_avoid(_item(bio="an apolitical documentary photographer"), []) is False
    assert _is_hard_avoid(_item(bio="scent of a new lens"), []) is True  # 真 scent 一词照旧命中
