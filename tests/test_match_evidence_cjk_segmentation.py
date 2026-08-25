"""B7 · CJK segmentation for the lexical evidence gate.

Chinese writes no spaces, so the ASCII-shaped tokenizer captured a whole run as
one token and that token matched nothing in the pool: a Chinese query had zero
provable terms and `_require_evidence_anchor`'s "fall back to original_query"
rescue was empty by construction.  These tests pin the repair *and* pin that it
buys no leniency: the AND-2 intent contract survives, and the ASCII path is
untouched.
"""
from __future__ import annotations

import pytest

from app.domains.kol import profile_recall_match_evidence as me


AUDIT_QUERY = "55evo e 卡口找一些消费群体多的推广人"


def _row(bio: str) -> dict[str, str]:
    return {
        "handle": "creator", "display_name": "", "bio": bio, "primary_topic": "",
        "content_style": "", "secondary_topics_json": "", "profile_text": "", "type_reason": "",
    }


# ── the bug: a Chinese query could prove nothing ────────────────────────────


def test_cjk_run_is_segmented_into_words_instead_of_one_unmatchable_token() -> None:
    terms = me.query_evidence_terms(AUDIT_QUERY)
    # Before: ['55evo', '卡口消费群体多的推广人'] — the second term is a whole
    # sentence and is absent from every profile in the pool.
    assert "卡口消费群体多的推广人" not in terms
    assert {"卡口", "推广"}.issubset(terms)
    # 受众规模词(消费/群体/粉丝/流量…)是筛选条件,落 followers_min,不是题材证据,
    # 因此被归入泛词表——切得出来但不充当举证词(2026-08-25 一词不得两用同批裁决)。
    assert "消费" not in terms and "群体" not in terms


def test_two_character_chinese_word_survives_the_ascii_min_length_rule() -> None:
    # `len(term) < 3` is an English stub rule; 卡口 / 索尼 are complete words.
    assert "卡口" in me.query_evidence_terms("索尼 卡口 镜头")
    assert "索尼" in me.query_evidence_terms("索尼 卡口 镜头")


def test_chinese_query_can_now_prove_a_match_at_all() -> None:
    assert me.build_match_evidence(_row("卡口镜头推广"), {}, AUDIT_QUERY)
    assert not me.build_match_evidence(_row("只做美食探店"), {}, AUDIT_QUERY)


def test_generic_chinese_instruction_words_are_still_discarded() -> None:
    terms = me.query_evidence_terms(AUDIT_QUERY)
    assert "一些" not in terms and "找一" not in terms
    assert me.query_evidence_terms("寻找一些达人") == []


# ── the guard: segmentation must not weaken the AND-2 intent contract ───────


def test_overlapping_bigrams_of_one_word_count_as_one_proof() -> None:
    # 人像摄影师 needs two words (人像 + 摄影).  A bio saying only 摄影师
    # matches the bigrams 摄影 and 影师, but they overlap inside the query and
    # are therefore one word, not two — it must not satisfy AND-2.
    assert me.build_match_evidence(_row("摄影师"), {}, "人像摄影师") == []
    # Two genuinely distinct words in the same bio do satisfy it.
    assert me.build_match_evidence(_row("人像摄影"), {}, "人像摄影师")


def test_adjacent_chinese_words_still_count_separately() -> None:
    # 人像摄影 is 人像 + 摄影: two words, two disjoint spans, AND-2 satisfied.
    # (原例 消费群体 已归入受众规模泛词,不再充当举证词,故换成题材词验证同一机理。)
    evidence = me.build_match_evidence(_row("专注人像摄影创作"), {}, "人像摄影达人")
    assert {"人像", "摄影"}.issubset({item["term"] for item in evidence})


def test_single_word_chinese_query_is_not_made_unsatisfiable() -> None:
    # 摄影师 tiles into two bigrams but is still one word: demanding two proofs
    # from it would turn a legitimate one-word query into a dead end.
    assert me.build_match_evidence(_row("专注人像摄影"), {}, "摄影师")


def test_losing_bigram_is_never_shown_to_the_operator() -> None:
    # 费群 is a slice spanning two words; it proved nothing and must not appear
    # in the user-visible reason.
    evidence = me.build_match_evidence(_row("专注人像摄影创作"), {}, "人像摄影达人")
    assert "像摄" not in {item["term"] for item in evidence}
    assert "费群" not in me.why_fit_from_match_evidence(evidence)


# ── the contract: the ASCII path is byte-identical ──────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("camera creator", []),
        ("lens review creator", ["lens", "review"]),
        ("find street photographers", ["street"]),
        ("Sony E-mount portrait photographer natural light",
         ["sony", "e-mount", "portrait", "natural", "light"]),
    ],
)
def test_ascii_queries_tokenize_exactly_as_before(query: str, expected: list[str]) -> None:
    assert me.query_evidence_terms(query) == expected


def test_ascii_evidence_is_unchanged_by_the_cjk_repair() -> None:
    evidence = me.build_match_evidence(
        _row("Documentary street photographer"), {}, "find street photographers"
    )
    assert {item["term"] for item in evidence} == {"street"}


def test_intent_proof_count_is_plain_intersection_without_cjk() -> None:
    terms = ["sony", "portrait", "street"]
    assert me._intent_proof_terms(terms, {"sony", "street"}, "sony portrait street") == [
        "sony", "street",
    ]
