"""在线意图腿:build_match_evidence 的 min_intent_terms 参数契约。

红线:本地车道口径一个字节不变(默认 2),只有在线车道传 1。
理由与实测见 profile_recall_match_evidence._intent_terms_required 的 docstring。
"""
from __future__ import annotations

import inspect

from app.domains.kol import profile_recall_match_evidence as me
from app.domains.kol import profile_online_qualification as online
from app.domains.kol import profile_query_cell_evidence as query_cell_evidence

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


def test_only_the_online_entrypoint_lowers_the_shared_builder_threshold(
    monkeypatch,
):
    """重构可移动源码；契约只关心入口实际传给共享证据构建器的阈值。"""
    calls = []

    def capture(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return []

    monkeypatch.setattr(online, "build_query_cell_match_evidence", capture)
    online._cell_match_evidence(
        ONLINE_ROW,
        {},
        query_text=QUERY_TWO_INTENT_WORDS,
        query_cell={},
    )

    assert len(calls) == 1
    assert calls[0]["kwargs"]["min_intent_terms"] == 1
    assert inspect.signature(
        query_cell_evidence.build_query_cell_match_evidence
    ).parameters["min_intent_terms"].default == me.INTENT_TERMS_DEFAULT


def test_online_lane_still_withholds_the_product_anchor_this_wave(monkeypatch):
    # 在线入口不会凭客户端产品词制造证据；共享构建器只接收服务端锁定 QueryCell。
    observed = []

    def capture(*args, **kwargs):
        observed.append(kwargs)
        return []

    monkeypatch.setattr(online, "build_query_cell_match_evidence", capture)
    online._cell_match_evidence(
        ONLINE_ROW,
        {},
        query_text=QUERY_TWO_INTENT_WORDS,
        query_cell={},
    )

    assert "required_product_terms" not in observed[0]
