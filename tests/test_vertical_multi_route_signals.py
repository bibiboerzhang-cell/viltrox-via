"""垂类多路取证的契约(车道 3,2026-08-25)。

钉死四件事(逐条对应用户裁令):多维度、不放宽合格标准、判不出要标未知、可解释。
另外钉住「历史词组零回归」——旧的单词表能判出来的人,新引擎必须照样判得出来。
"""
from __future__ import annotations

import json

import pytest

from app.domains.kol import profile_vertical_signals as vs
from app.domains.kol.profile_recall_projection import _candidate_filter_verdict


def _topics(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


# ── 多路取证:每一路单独都能把人判出来 ──────────────────────────────────────


def test_channel_keywords_route_alone_finds_lifestyle() -> None:
    """频道关键词路:prod 里 615 人有这一路,历史一次都没读过。"""
    reading = vs.classify_verticals(
        {"topic_details_json": _topics(keywords=["travel photography", "coffee"])}, {}
    )
    assert "lifestyle" in reading.verticals
    routes = {item["route"] for item in reading.evidence_for("lifestyle")}
    assert routes == {vs.ROUTE_CHANNEL_KEYWORDS}


def test_profile_category_route_alone_finds_video_creation() -> None:
    reading = vs.classify_verticals(
        {"topic_details_json": _topics(business_category="Cinematographer")}, {}
    )
    assert "video_creation" in reading.verticals
    assert reading.evidence_for("video_creation")[0]["route"] == vs.ROUTE_PROFILE_CATEGORY


def test_video_title_route_alone_finds_lens_review() -> None:
    reading = vs.classify_verticals(
        {}, {"evidence_titles": ["The Most Underrated 50mm Lens - honest review"]}
    )
    assert "lens_review" in reading.verticals
    assert reading.evidence_for("lens_review")[0]["route"] == vs.ROUTE_VIDEO_TITLES


def test_tagged_brand_route_alone_finds_camera_system_and_video_creation() -> None:
    reading = vs.classify_verticals(
        {
            "tagged_brands_json": json.dumps(
                [
                    {"handle": "viltrox.official", "name": "VILTROX"},
                    {"handle": "djiglobal", "name": "DJI"},
                ]
            )
        },
        {},
    )
    assert set(reading.verticals) == {"video_creation", "camera_system"}
    assert reading.evidence_for("camera_system")[0]["note"].startswith("作品里标记过镜头品牌")


def test_used_lens_route_alone_finds_camera_system() -> None:
    reading = vs.classify_verticals({}, {"used_lenses": ["Viltrox AF 27mm F1.2"]})
    assert reading.verticals == ("camera_system",)
    assert "Viltrox AF 27mm F1.2" in reading.evidence_for("camera_system")[0]["note"]


def test_video_category_route_needs_more_than_one_sample() -> None:
    """prod 直方图 772 行里 728 行只有 1 条样本 —— 单条不许给整个频道定性。"""
    one_sample = vs.classify_verticals({"topic_details_json": _topics(video_category_ids={"22": 1})}, {})
    assert one_sample.verticals == ()
    assert one_sample.is_unknown is True

    enough = vs.classify_verticals({"topic_details_json": _topics(video_category_ids={"22": 12})}, {})
    assert enough.verticals == ("vlog",)
    assert "12/12 条" in enough.evidence_for("vlog")[0]["note"]

    diluted = vs.classify_verticals(
        {"topic_details_json": _topics(video_category_ids={"22": 3, "24": 30})}, {}
    )
    assert diluted.verticals == ()


def test_brand_route_never_claims_review_or_comparison_by_itself() -> None:
    """口径:品牌标记只回答「他做器材内容」,不回答「他做评测/对比」。"""
    reading = vs.classify_verticals(
        {"tagged_brands_json": json.dumps([{"handle": "sigmaphoto", "name": "Sigma"}])}, {}
    )
    assert "lens_review" not in reading.verticals
    assert "gear_comparison" not in reading.verticals


# ── 多归属 ──────────────────────────────────────────────────────────────────


def test_one_person_can_belong_to_several_verticals_with_separate_evidence() -> None:
    reading = vs.classify_verticals(
        {
            "bio": "Portrait photographer sharing lens reviews",
            "topic_details_json": _topics(keywords=["travel", "camera gear"]),
        },
        {"evidence_titles": ["Sony vs Canon camera comparison"]},
    )
    assert {"portrait", "lens_review", "lifestyle", "camera_system"} <= set(reading.verticals)
    for vertical in reading.verticals:
        assert reading.evidence_for(vertical), f"{vertical} 没有引证 = 黑盒布尔,禁止"


def test_evidence_is_capped_but_never_empty_for_a_claimed_vertical() -> None:
    reading = vs.classify_verticals(
        {
            "bio": "travel",
            "topic_details_json": _topics(keywords=["food"], business_category="Personal blog"),
        },
        {"evidence_titles": ["my daily life in Lisbon"], "reason_labels": ["travel"]},
    )
    for vertical in reading.verticals:
        assert 1 <= len(reading.evidence_for(vertical)) <= vs.MAX_EVIDENCE_PER_VERTICAL


# ── 判不出 = 未知,绝不默认归类 ────────────────────────────────────────────


def test_no_signal_at_all_is_unknown_not_a_default_vertical() -> None:
    reading = vs.classify_verticals({}, {})
    assert reading.verticals == ()
    assert reading.is_unknown is True
    assert reading.has_signal is False


def test_signal_present_but_unrecognisable_is_still_unknown() -> None:
    """有话说但说不出垂类的人,照样标未知 —— 不许硬塞进某一类。"""
    reading = vs.classify_verticals({"bio": "just a guy from Lisbon"}, {})
    assert reading.verticals == ()
    assert reading.is_unknown is True
    assert reading.has_signal is True


def test_self_declared_photographer_is_deliberately_not_mapped() -> None:
    """prod 最大的单一身份标注(121 人)。9 个垂类里没有「摄影」本身,就诚实留空。"""
    reading = vs.classify_verticals({"topic_details_json": _topics(business_category="Photographer")}, {})
    assert reading.verticals == ()
    assert reading.is_unknown is True


def test_unknown_is_rejected_under_the_default_require_mode() -> None:
    outcome, reading, hits = vs.vertical_filter_outcome({}, {}, ["lifestyle"])
    assert (outcome, hits) == (vs.OUTCOME_UNKNOWN, [])
    assert reading.is_unknown is True


def test_known_other_vertical_is_a_mismatch_not_an_unknown() -> None:
    outcome, _reading, hits = vs.vertical_filter_outcome(
        {"bio": "Daily travel stories"}, {}, ["technology"]
    )
    assert (outcome, hits) == (vs.OUTCOME_MISMATCH, [])


def test_requested_vertical_that_matches_passes_with_its_evidence() -> None:
    outcome, _reading, hits = vs.vertical_filter_outcome(
        {"bio": "Daily travel stories"}, {}, ["lifestyle"]
    )
    assert outcome == vs.OUTCOME_PASS
    assert hits and hits[0]["vertical"] == "lifestyle"


def test_empty_request_always_passes() -> None:
    assert vs.vertical_filter_outcome({}, {}, [])[0] == vs.OUTCOME_PASS
    assert vs.vertical_filter_outcome({"bio": "travel"}, {}, None)[0] == vs.OUTCOME_PASS


# ── 可解释:字段契约 ───────────────────────────────────────────────────────


def test_evidence_item_field_contract_is_stable() -> None:
    reading = vs.classify_verticals({"bio": "Daily travel stories"}, {})
    item = reading.evidence_for("lifestyle")[0]
    assert set(item) == {
        "vertical", "vertical_label", "route", "route_label", "matched", "snippet", "note",
    }
    assert item["vertical_label"] == "生活方式"
    assert item["route_label"] == vs.ROUTE_LABELS_ZH[item["route"]]
    assert isinstance(item["matched"], list) and item["matched"]


def test_explanations_contract_and_plain_chinese_facade() -> None:
    reading = vs.classify_verticals({"bio": "Daily travel stories"}, {})
    explanations = vs.vertical_explanations(reading)
    assert explanations
    entry = explanations[0]
    assert set(entry) == {"vertical", "label", "reasons", "routes"}
    assert entry["label"] in vs.VERTICAL_LABELS_ZH.values()
    assert entry["reasons"] and all(isinstance(text, str) for text in entry["reasons"])
    banned = ("llm", "blob", "regex", "token", "embedding", "vector")
    for text in entry["reasons"]:
        assert not any(word in text.lower() for word in banned), text


def test_unknown_person_has_no_explanation_rather_than_a_made_up_one() -> None:
    assert vs.vertical_explanations(vs.classify_verticals({}, {})) == []


def test_snippet_never_carries_contact_values() -> None:
    """引文只截命中词附近一小段,并过联系方式清洗 —— 简介里的邮箱/电话不许上卡面。"""
    reading = vs.classify_verticals(
        {"bio": "travel creator  reach me at hello@example.test or +1-202-555-0199"}, {}
    )
    dumped = json.dumps(vs.vertical_explanations(reading), ensure_ascii=False) + json.dumps(
        list(reading.evidence), ensure_ascii=False
    )
    assert "hello@example.test" not in dumped
    assert "202-555-0199" not in dumped


# ── 零回归:历史词组照旧命中;其他闸一个都没动 ─────────────────────────────


@pytest.mark.parametrize(
    "vertical,bio",
    [
        ("lens_review", "lens review channel"),
        ("photography_tutorial", "photography tutorial"),
        ("gear_comparison", "camera gear comparison"),
        ("portrait", "portrait work"),
        ("video_creation", "filmmaking"),
        ("camera_system", "camera system talk"),
        ("vlog", "vlogger"),
        ("lifestyle", "lifestyle"),
        ("technology", "technology"),
    ],
)
def test_legacy_word_groups_still_classify(vertical: str, bio: str) -> None:
    assert vertical in vs.classify_verticals({"bio": bio}, {}).verticals


def test_word_boundary_stops_the_classic_substring_misfires() -> None:
    """裸子串会让 "tech" 命中 "technique"(摄影技巧被判成科技),词首边界堵住它。"""
    assert vs.term_hit("tech", "photography technique") is False
    assert vs.term_hit("tech", "tech reviews") is True
    # 但必须保留历史上真正需要的前缀命中
    assert vs.term_hit("photo", "photography") is True
    assert vs.term_hit("lenses", "lenses") is True
    # 名字里带 camera 不算器材内容(@spatialcamera 不该被判成相机系统)
    assert vs.term_hit("camera", "@spatialcamera") is False


def test_word_boundary_does_not_swallow_english_terms_inside_cjk_text() -> None:
    """边界不能用 ``\\b``:CJK 也算 \\w,「をcinematic」会被整条判空。

    prod 2034 人快照实测:用 ``\\b`` 时这类日语/中文简介丢 5 人,丢的全是本来判得出
    「视频创作」的创作者。
    """
    assert vs.term_hit("cinematic", "モノやサービスをcinematicに表現しよう") is True
    assert vs.term_hit("photo", "写真photoの撮り方") is True
    reading = vs.classify_verticals(
        {"bio": "「熱い想い、モノやサービスをcinematicに表現しよう。」をコンセプトとした映像制作会社"}, {}
    )
    assert "video_creation" in reading.verticals


def test_plural_forms_that_the_legacy_substring_covered_are_kept() -> None:
    """历史裸子串顺带吃下的复数形,封边界后必须在词表里显式补回,否则主流写法搜不到人。"""
    assert "vlog" in vs.classify_verticals({"bio": "tech + camera's + vlogs"}, {}).verticals
    assert "gear_comparison" in vs.classify_verticals(
        {"bio": "in-depth reviews and tests of anamorphic lenses and adapters"}, {}
    ).verticals


def test_gear_content_gate_is_untouched_by_the_wider_vertical_corpus() -> None:
    """红线:垂类语料变宽,不许顺带把器材证据要求放宽。"""
    row = {
        "handle": "a", "platform": "youtube", "followers": 100_000,
        "country": "US", "language": "en",
        # 频道关键词里全是器材词,但它**不在** gear_content 的语料里
        "topic_details_json": _topics(keywords=["camera gear", "lens reviews"]),
    }
    verdict = _candidate_filter_verdict(row, {}, {"gear_content": "yes"})
    assert verdict.passes is False
    assert "gear_content" in verdict.rejected


def test_country_language_tri_state_is_untouched() -> None:
    row = {"handle": "a", "platform": "youtube", "followers": 100_000, "bio": "travel"}
    verdict = _candidate_filter_verdict(row, {}, {"countries": ["United States"]})
    assert verdict.passes is False
    assert verdict.rejected_unknown == ["countries"]
    passing = _candidate_filter_verdict(
        row, {}, {"countries": ["United States"], "countries_mode": "include_unknown"}
    )
    assert passing.passes is True


def test_vertical_unknown_is_reported_honestly_even_without_a_vertical_filter() -> None:
    row = {"handle": "a", "platform": "youtube", "followers": 100_000, "country": "US", "language": "en"}
    verdict = _candidate_filter_verdict(row, {}, {})
    assert verdict.passes is True
    assert "verticals" in verdict.unknown


def test_free_text_vertical_term_keeps_the_legacy_substring_fallback() -> None:
    row = {"handle": "a", "platform": "youtube", "followers": 100_000, "primary_topic": "camera lens review"}
    assert _candidate_filter_verdict(row, {}, {"verticals": ["camera"]}).passes is True
    assert _candidate_filter_verdict(row, {}, {"verticals": ["knitting"]}).passes is False


# ── 端到端:垂类读数确实随候选一起返回(判定与卡面同源)────────────────────


def test_recall_item_carries_vertical_tags_and_explanations(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import profile_recall

    row = {
        "kol_pool_id": 1, "handle": "creator-1", "display_name": "C1", "platform": "youtube",
        "profile_type": "creator", "creator_type_score": 80, "reviewer_type_score": 80,
        "followers": 120_000, "country": "US", "language": "en",
        "primary_topic": "camera lens review", "bio": "Travel filmmaker and lens reviewer",
        "topic_details_json": _topics(
            keywords=["travel photography", "camera reviews"], video_category_ids={"22": 12}
        ),
        "tagged_brands_json": json.dumps([{"handle": "viltrox.official", "name": "VILTROX"}]),
    }
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(profile_recall, "resolve_query_text", lambda **_k: ("lens review", {"query_profile": ""}))
    monkeypatch.setattr(profile_recall, "_embed_query", lambda _t: ([0.1], {}))
    monkeypatch.setattr(
        profile_recall, "_search_qdrant",
        lambda _v, _l: [profile_recall.RecallHit(kol_pool_id=1, vector_score=0.9, qdrant_point_id="q-1")],
    )
    monkeypatch.setattr(profile_recall, "_entry_rows", lambda ids: {1: dict(row)} if 1 in ids else {})
    monkeypatch.setattr(
        profile_recall, "_evidence_summaries",
        lambda _ids: {1: {"evidence_titles": ["Best travel lens review"],
                          "representative_evidence": [{"title": "Best travel lens review"}]}},
    )
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})

    result = profile_recall.recall_kol_profiles(
        query_text="lens review", candidate_limit=1, limit=1,
        creator_quota=1, reviewer_quota=0, allow_backfill=False,
    )
    item = result["items"][0]
    assert "lifestyle" in item["vertical_tags"] and "camera_system" in item["vertical_tags"]
    labels = {entry["label"] for entry in item["vertical_evidence"]}
    assert labels == {vs.VERTICAL_LABELS_ZH[key] for key in item["vertical_tags"]}
    assert all(entry["reasons"] for entry in item["vertical_evidence"])
