"""在线意图腿:build_match_evidence 的 min_intent_terms 参数契约。

红线:本地车道口径一个字节不变(默认 2),只有在线车道传 1。
理由与实测见 profile_recall_match_evidence._intent_terms_required 的 docstring。
"""
from __future__ import annotations

import inspect
from pathlib import Path

from app.domains.kol import profile_recall_match_evidence as me
from app.domains.kol import profile_online_qualification as online


ROOT = Path(__file__).resolve().parents[1]

# 8 个可举证字段全有内容 —— 本地池行的形状。
LOCAL_ROW = {
    "handle": "streetshooter",
    "display_name": "Street Shooter",
    "bio": "I shoot street photography every week.",
    "primary_topic": "street photography",
    "content_style": "documentary",
    "secondary_topics_json": ["travel"],
    "profile_text": "street and travel work",
    "type_reason": "street creator",
}
# 在线 provider 行:_candidate_row 里那四个字段取自 provider 从不下发的键,恒为空。
ONLINE_ROW = {
    "handle": "streetshooter",
    "display_name": "Street Shooter",
    "bio": "I shoot street photography every week.",
    "primary_topic": "",
    "content_style": "",
    "secondary_topics_json": [],
    "profile_text": "",
    "type_reason": "",
}
# 两个可举证意图词(street / portrait),候选只证得出 street —— AND-2 判死,AND-1 放行。
QUERY_TWO_INTENT_WORDS = "street photography portrait retouching"


def _old_required_terms(provable_words: list[str]) -> int:
    """改动前那一行的原文:required_terms = 1 if len(provable_words) <= 1 else 2"""
    return 1 if len(provable_words) <= 1 else 2


def test_default_keeps_the_existing_required_terms_arithmetic_byte_for_byte():
    for count in range(0, 40):
        words = ["w%d" % i for i in range(count)]
        assert me._intent_terms_required(words, me.INTENT_TERMS_DEFAULT) == _old_required_terms(words)


def test_default_signature_stays_two_so_untouched_callers_do_not_shift():
    assert me.INTENT_TERMS_DEFAULT == 2
    assert inspect.signature(me.build_match_evidence).parameters["min_intent_terms"].default == 2


def test_local_lane_still_demands_two_intent_words():
    # 本地行字段丰富,但只证得出一个查询意图词 → 仍然判无证据。
    assert me.build_match_evidence(LOCAL_ROW, {}, QUERY_TWO_INTENT_WORDS) == []
    # 不传参 == 显式传 2,两条路径逐条一致。
    assert me.build_match_evidence(LOCAL_ROW, {}, QUERY_TWO_INTENT_WORDS, min_intent_terms=2) == []


def test_online_lane_at_one_admits_the_single_provable_intent_word():
    assert me.build_match_evidence(ONLINE_ROW, {}, QUERY_TWO_INTENT_WORDS) == []
    proofs = me.build_match_evidence(ONLINE_ROW, {}, QUERY_TWO_INTENT_WORDS, min_intent_terms=1)
    assert proofs
    assert {item["term"] for item in proofs} == {"street"}


def test_one_is_a_floor_not_a_free_pass():
    # 降到 1 仍要**真的**举证一个意图词;一个都证不出照样判死。
    stranger = {
        **ONLINE_ROW,
        "handle": "sourdoughdaily",
        "display_name": "Sourdough Daily",
        "bio": "I bake sourdough bread on weekends.",
    }
    assert me.build_match_evidence(stranger, {}, QUERY_TWO_INTENT_WORDS, min_intent_terms=1) == []


def test_product_leg_is_untouched_by_the_intent_threshold():
    # 传了产品锚而候选证不出产品身份/语境 → 无论意图门槛是 1 还是 2 都不放行。
    for threshold in (1, 2):
        assert me.build_match_evidence(
            LOCAL_ROW, {}, QUERY_TWO_INTENT_WORDS,
            required_product_terms=["viltrox af 135mm f1.8 lab"],
            min_intent_terms=threshold,
        ) == []


def test_out_of_range_values_are_clamped_and_never_loosen_below_one():
    words = ["alpha", "beta", "gamma"]
    for bogus in (0, -1, -99, False):
        assert me._intent_terms_required(words, bogus) == me.INTENT_TERMS_FLOOR
    for bogus in (3, 99, 2.9):
        assert me._intent_terms_required(words, bogus) == me.INTENT_TERMS_DEFAULT
    for junk in (None, "two", object(), [2]):
        assert me._intent_terms_required(words, junk) == me.INTENT_TERMS_DEFAULT
    # 越界值走到 build_match_evidence 也不得改变结论。
    assert me.build_match_evidence(LOCAL_ROW, {}, QUERY_TWO_INTENT_WORDS, min_intent_terms=99) == []
    assert me.build_match_evidence(LOCAL_ROW, {}, QUERY_TWO_INTENT_WORDS, min_intent_terms=None) == []
    assert me.build_match_evidence(ONLINE_ROW, {}, QUERY_TWO_INTENT_WORDS, min_intent_terms=0)


def test_single_provable_word_query_is_unaffected_by_the_knob():
    # 只能举证一个词的查询本来就走 1,两个车道读数必须一致。
    for threshold in (1, 2):
        assert me.build_match_evidence(LOCAL_ROW, {}, "摄影师", min_intent_terms=threshold) == \
            me.build_match_evidence(LOCAL_ROW, {}, "摄影师")


def test_only_the_online_lane_passes_the_knob_in_the_whole_backend():
    mentions, passers = [], []
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        if "min_intent_terms" in text:
            mentions.append(rel)
        if "min_intent_terms=" in text:
            passers.append(rel)
    assert sorted(mentions) == [
        "backend/app/domains/kol/profile_online_qualification.py",
        "backend/app/domains/kol/profile_recall_match_evidence.py",
    ]
    # 全后端只有在线腿真的把这个参数传出去。
    assert sorted(passers) == ["backend/app/domains/kol/profile_online_qualification.py"]
    online_src = (ROOT / "backend/app/domains/kol/profile_online_qualification.py").read_text(encoding="utf-8")
    assert "build_match_evidence(row, evidence, query_text, min_intent_terms=1)" in online_src
    # 本地车道两处调用都不得出现这个参数。
    local_src = (ROOT / "backend/app/domains/kol/profile_recall.py").read_text(encoding="utf-8")
    assert "min_intent_terms" not in local_src
    assert "build_match_evidence(row, evidence, resolved_text" in local_src


def test_online_lane_still_withholds_the_product_anchor_this_wave():
    # 产品腿在线侧仍不传 required_product_terms(另一个缺口,本波不动)——钉住现状,
    # 免得后来者以为已经修过。
    src = (ROOT / "backend/app/domains/kol/profile_online_qualification.py").read_text(encoding="utf-8")
    assert "required_product_terms=" not in src
