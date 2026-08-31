"""CC 战役四文件 characterization:降复杂度前锁行为,重构后逐键相等。

覆盖名下四文件(lens_evidence / metric_truth / profile_recall_projection /
events.service)里被整文件清剿波触碰的函数。golden 由重构前 HEAD(91c4ad45)
的真实输出捕获(tests/fixtures/cc_quad_characterization_golden.json),
重构只许搬家/提 helper,任何输出漂移都在这里红。

重生成 golden(仅当行为变更经用户批准):
``.venv/bin/python tests/test_cc_quad_characterization.py --regenerate``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (str(BACKEND), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.domains.events import service as ev  # noqa: E402
from app.domains.kol import lens_evidence as le  # noqa: E402
from app.domains.kol import metric_truth as mt  # noqa: E402
from app.domains.kol import profile_recall_projection as prj  # noqa: E402
from app.domains.kol.profile_recall_contract import RecallHit  # noqa: E402
from tests.test_lens_evidence_resolver import CATALOG  # noqa: E402

GOLDEN_PATH = ROOT / "tests" / "fixtures" / "cc_quad_characterization_golden.json"

LENS_RESULT = {
    "layer1_visual_content": {
        "product_presence": (
            "The Viltrox AF 85mm F1.4 Pro FE lens appears in close-ups throughout. "
            "字幕标注了 Viltrox 135mm F1.8 LAB。"
        ),
        "brand_exposure": "Viltrox Pro 系列 logo visible; also mentions Viltrox 13mm/23mm/27mm/75mm Pro briefly.",
        "content_summary": "Reviewer talks about the Viltrox DC-A1 7英寸 monitor and the Viltrox EF-E II adapter.",
        "scene_timeline": [{"what": "Unboxing the Viltrox 85mm f/1.4 for Sony E-mount"}],
    },
    "layer4_attribution": {
        "product_contribution": "口播提到 Viltrox 50mm F2.0 Air 是性价比之选",
        "attribution_breakdown": [{"evidence": "subtitle shows Viltrox K60 light stick"}],
    },
    "layer6_flags_and_scores": {
        "scores": {"product_proof_score": {"evidence": "Viltrox 85 1.4 mentioned by voice"}},
        "key_hook": "推荐 Viltrox EVO 系列",
        "final_verdict": "Great fit for Viltrox Pro series",
    },
    "raw_content_topic": "unused",
}
CLIP_SAMPLES = (
    "AF 85mm F1.4 Pro FE lens appears",
    "Pro 系列 logo visible",
    "13mm/23mm/27mm/75mm Pro briefly.",
    "DC-A1 7英寸 monitor",
    "85 1.4 mentioned",
    "the new lens for Sony E-mount is great",
    "Pro 75mm F1.2 shots",
    "Z-mount 85 test",
)
MOUNT_SAMPLES = (
    "for sony e-mount", "for nikon z", "for fuji x", "for leica l",
    "for canon rf", "for canon ef", "for m43 panasonic", "for pentax",
)
FAMILY_SAMPLES = (
    "Viltrox AF 85mm F1.4 Pro Full-Frame Lens for Sony E-Mount",
    "Viltrox Vintage Z1 Pro TTL Retro On-Camera Flash",
    "Viltrox DC-A1 2800 Nits 7-Inch Camera Monitor",
    "AF 75mm F1.8 EVO FE", "Vintage Z2-S", "K60 RGB Light Stick",
)
SPEC_SAMPLES = ("AF 85mm F1.4 Pro FE", "EPIC 50mm T2.0 PL", "16-35mm f2.8 Z", "35 1.8", "DC-A1")
SLASH_SAMPLES = ("13mm/23mm/27mm/75mm Pro", "DC-X2/X3", "85mm F1.4")
V_REL_ROWS = (
    {"resolution": "sku", "lens_key": "af85mmf14pro", "modalities": ["visual"], "source_fields": ["product_presence"]},
    {"resolution": "family", "lens_key": "series:pro", "modalities": ["visual"], "source_fields": ["brand_exposure"]},
    {"resolution": "unresolved", "lens_key": "", "modalities": ["unspecified"], "source_fields": ["content_summary"]},
    {"resolution": "sku", "lens_key": "x", "modalities": ["unspecified"], "source_fields": ["final_verdict"]},
    {"resolution": "family", "lens_key": "y", "modalities": ["unspecified"], "source_fields": ["product_presence"]},
)

POOL_RAW = {
    "source": "apify_youtube_profile_crawl",
    "sync_status": "ok",
    "metrics_scraped_at": "2026-08-01T00:00:00Z",
    "followersCount": 12000,
    "engagement_rate": 4.5,
    "items": [
        {"type": "video", "id": "v1", "title": "t1", "viewCount": 1000, "likeCount": 100, "commentCount": 10},
        {"type": "video", "id": "v2", "title": "t2", "viewCount": 3000, "likeCount": 300, "commentCount": 30},
    ],
}
POOL_ITEM = {
    "kol_pool_id": 7, "followers": 12000, "avg_views": 2000, "avg_likes": 200, "avg_comments": 20,
    "engagement_rate": 4.5, "source_type": "apify", "source_ref": "https://youtube.com/@x?token=abc",
    "last_seen_at": "2026-08-02T00:00:00Z",
    "real_er": 3.2, "real_er_sample_n": 8, "real_er_computed_at": "2026-08-01", "real_er_method": "views_v1",
    "audience_estimated_json": json.dumps(
        {"method": "ensemble_v1", "sample_size": 40, "confidence": 0.7, "geo": {"US": 0.5}}
    ),
    "brand_collaborations_json": json.dumps([
        {"brand": "Sony", "status": "published", "evidence_url": "https://x.com/v"},
        {"brand": "Nikon", "status": "planned"},
        {"brand": "Sigma"},
        "Tamron",
    ]),
    "raw_platform_data": json.dumps(POOL_RAW),
}
POOL_ZERO_ITEM = {
    "kol_pool_id": 1, "followers": 0, "avg_views": None, "engagement_rate": "unknown",
    "source_type": "manual", "source_ref": "", "raw_platform_data": "{}",
}
POOL_DECLARED_ITEM = {
    "kol_pool_id": 2, "followers": 500, "source_type": "csv_import", "source_ref": "roster.csv",
    "raw_platform_data": None,
}
EVIDENCE_ITEMS = (
    {"id": 1, "view_count": 10, "like_count": 0, "comment_count": None, "share_count": "n/a",
     "metrics_source": "apify", "metrics_scraped_at": "2026-08-01", "scrape_status": "ok", "content_url": "https://a"},
    {"id": 2, "view_count": 0, "like_count": 5, "comment_count": 2, "share_count": 1,
     "metrics_source": "", "metrics_scraped_at": "", "scrape_status": "", "content_url": "https://b"},
    {"id": None, "view_count": 7, "like_count": 7, "comment_count": 7, "share_count": 7,
     "metrics_source": "", "metrics_scraped_at": "", "scrape_status": "", "content_url": ""},
)
SOURCE_REF_SAMPLES = (
    "https://user:pw@youtube.com:8443/watch?v=1", "https://youtube.com/path/token_abc",
    "/home/x/secret_token.json", "roster.csv", "unknown", "",
)

RECALL_ROW = {
    "kol_pool_id": 42, "handle": "lensguy", "display_name": "Lens Guy", "platform": "youtube",
    "profile_url": "https://youtube.com/@lensguy", "avatar_url": "", "followers": 52000,
    "avg_views": 8000, "avg_likes": 400, "avg_comments": 40, "engagement_rate": 5.1,
    "real_er": 2.4, "real_er_sample_n": 12, "real_er_computed_at": "2026-08-01", "real_er_method": "views_v1",
    "country": "US", "language": "en", "primary_topic": "camera gear reviews",
    "bio": "Photographer reviewing lenses and cameras", "profile_type": "reviewer",
    "creator_type_score": 40.0, "reviewer_type_score": 88.0, "type_reason": "review-heavy uploads",
    "type_method": "profile_index_v2", "sufficiency": "rich", "content_style": "review",
    "secondary_topics_json": '["lens","travel"]',
    "profile_text": "gear reviewer portraits", "last_seen_at": "2026-08-20", "updated_at": "2026-08-21",
    "source_type": "apify", "source_ref": "https://youtube.com/@lensguy",
    "raw_platform_data": '{"source": "apify_youtube_profile_crawl", "sync_status": "ok", "followersCount": 52000}',
    "brand_collaborations_json": '[{"brand": "Sony", "status": "published", "evidence_url": "https://v"}]',
}
RECALL_EVIDENCE = {
    "representative_evidence": [
        {"title": "Viltrox AF 85mm F1.4 Pro review", "content_url": "https://youtu.be/1", "thumbnail_url": "",
         "view_count": 120000, "like_count": 8000, "comment_count": 500, "share_count": None, "data_truth": {"x": 1}},
    ],
    "used_lenses": ["Viltrox AF 85mm F1.4 Pro"],
    "reason_labels": ["镜头评测"],
    "video_evidence_count": 9, "with_view_count": 7, "deep_analysis_count": 3,
    "view_count_coverage_ratio": 0.7778, "coverage_note": "证据覆盖计数，不代表分析准确率或合作结果。",
    "evidence_titles": ["Viltrox AF 85mm F1.4 Pro review"],
}
RECALL_HIT = RecallHit(
    kol_pool_id=42, vector_score=0.61, qdrant_point_id="pt-1", lexical_score=0.5,
    retrieval_score=0.58, retrieval_method="hybrid_v1", retrieval_tier="strict",
    hybrid_rrf_score=0.9, retrieval_meta={"factual_anchor_terms": ["85mm"]},
)
NATURAL_LANE_ITEMS = (
    {"match_tier": "backfill"},
    {"match_tier": "strict", "source_fields": {"retrieval_meta": {"factual_anchor_terms": ["a"]}}},
    {"match_tier": "strict", "source_fields": {}, "primary_topic": "camera lens reviews", "bio": "",
     "used_lenses": [], "representative_evidence": []},
    {"match_tier": "relaxed"},
    {"match_tier": "strict", "source_fields": "notadict", "primary_topic": "cooking", "bio": ""},
)
FILTER_SAMPLES = (
    None,
    {"platforms": ["YouTube", "all"], "countries": {"values": ["US"], "mode": "include_unknown"},
     "languages": {"values": ["english"], "mode": "bogus"}, "followers_min": "1000.5", "follower_max": "abc",
     "gear_content": "yes", "unknown_key": 1},
    {"gear_content": "no", "verticals": ["photo"]},
)
VERDICT_SAMPLES = (
    ({"platform": "youtube", "country": "US", "language": "en", "followers": 100},
     {"platforms": ["tiktok"], "followers_min": 500}),
    ({"platform": "", "country": "", "language": "", "followers": None},
     {"platforms": ["youtube"], "countries": ["US"], "followers_max": 10}),
    ({"platform": "youtube", "country": "United States", "language": "English", "followers": 100,
      "bio": "camera lens gear"},
     {"countries": ["us"], "languages": ["en"], "gear_content": "yes"}),
)
DUE_DATE_SAMPLES = ("2026-01-05", "06/21", "2026/1/5", "01/05/2026", "2026.1.5", "TBD", "待定", "", "13/45", "1-5")
BAD_NUMERIC_PAYLOADS = (
    {"health_score": 101}, {"budget_total": -1}, {"roi": 1e9},
    {"location_lat": 95.0}, {"leads": "abc"}, {"roi": "x"},
)


def _norm(value):
    if isinstance(value, dict):
        return {str(key): _norm(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_norm(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_norm(item) for item in value)
    return value


def _build_lens_actual() -> dict:
    index = le.CatalogIndex(CATALOG, aliases=None)
    return {
        "lens_rows": le.extract_resolved(LENS_RESULT, index),
        "lens_explain_anchors": le.explain(LENS_RESULT, index)["anchors"],
        "clip": {sample: le._clip(sample) for sample in CLIP_SAMPLES},
        "mount_hint": {sample: le._mount_hint(sample) for sample in MOUNT_SAMPLES},
        "family_name": {sample: le.family_name(sample) for sample in FAMILY_SAMPLES},
        "parse_spec": {sample: le.parse_spec(sample) for sample in SPEC_SAMPLES},
        "split_slash": {sample: le.split_slash_list(sample) for sample in SLASH_SAMPLES},
        "v_rel": [le.v_relevance_for(dict(row)) for row in V_REL_ROWS],
    }


def _build_metric_actual() -> dict:
    return {
        "pool_truth": mt.project_pool_item_truth(dict(POOL_ITEM)),
        "pool_zero": mt.project_pool_item_truth(dict(POOL_ZERO_ITEM)),
        "pool_declared": mt.project_pool_item_truth(dict(POOL_DECLARED_ITEM)),
        "evidence_truth": [mt.project_evidence_item_truth(dict(item)) for item in EVIDENCE_ITEMS],
        "source_ref": {sample: mt._public_source_ref(sample) for sample in SOURCE_REF_SAMPLES},
        "source_state": mt._raw_source_state(POOL_RAW),
        "raw_match": [
            mt._raw_metric_match(POOL_RAW, "followers", 12000),
            mt._raw_metric_match(POOL_RAW, "engagement_rate", 4.5),
            mt._raw_metric_match(POOL_RAW, "avg_views", 2000),
            mt._raw_metric_match(POOL_RAW, "avg_likes", 999),
        ],
    }


def _build_recall_actual() -> dict:
    unknown_row = {**RECALL_ROW, "profile_type": "", "creator_type_score": None, "reviewer_type_score": None}
    verdicts = []
    for row, filters in VERDICT_SAMPLES:
        verdict = prj._candidate_filter_verdict(dict(row), {}, dict(filters))
        verdicts.append((tuple(verdict), verdict.rejected_known_mismatch, verdict.rejected_unknown))
    return {
        "format_item": prj._format_item(
            RECALL_HIT, dict(RECALL_ROW), "reviewer",
            vector_weight=0.7, type_weight=0.3, type_boost_enabled=True,
            evidence=dict(RECALL_EVIDENCE), persona_text="portrait photographers",
            product_label="AF 85mm F1.4 Pro", video_leaning=True,
        ),
        "format_item_unknown_bucket": prj._format_item(
            RecallHit(kol_pool_id=42, vector_score=None, qdrant_point_id=""), unknown_row, "unknown",
            vector_weight=0.7, type_weight=0.3, type_boost_enabled=False,
            evidence={}, persona_text="", product_label="", video_leaning=False,
        ),
        "why_fit": prj._why_fit(RECALL_ROW, RECALL_EVIDENCE, "portrait photographers", "AF 85mm F1.4 Pro"),
        "persona_text": prj._persona_text_for_query(
            {"query_profile": "", "query_text_provided": True, "query_text": "  Portrait  LENS "},
            ["85mm", "portrait"], "wedding shooters",
        ),
        "natural_lane": [prj._natural_business_lane(dict(item)) for item in NATURAL_LANE_ITEMS],
        "normalize_filters": [prj._normalize_recall_filters(sample) for sample in FILTER_SAMPLES],
        "filter_verdict": verdicts,
        "adoption_boost": prj._adoption_boost_for(
            {"platform": "youtube", "bio": "lens tester", "why_fit": ""},
            {"n": 10, "platforms": {"youtube": 5}, "top_words": {"lens"}},
        ),
    }


def _events_validate_errors() -> list:
    errors = []
    for payload in BAD_NUMERIC_PAYLOADS:
        try:
            ev._validate_event_numeric(dict(payload))
            errors.append(None)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def _build_events_actual() -> dict:
    ok_payload = {"health_score": 55, "budget_total": 100, "roi": 3.5, "location_lat": 45.0, "amount": 5, "qty": None}
    ev._validate_event_numeric(ok_payload)  # 合法 payload 必须静默通过
    return {
        "due_date": {sample: ev._normalize_due_date(sample) for sample in DUE_DATE_SAMPLES},
        "validate_errors": _events_validate_errors(),
        "event_row": ev._event_row({
            "id": "e1", "budget_json": '{"a":1}', "team_ids": "[1,2]",
            "related_project_ids": None, "invited_kols_json": "notjson", "title": "t",
        }),
        "task_row": ev._task_row({"id": "t1", "collaborators": "[3]", "checklist": None, "details": '{"k":"v"}'}),
    }


_BUILDERS = {
    "lens_evidence": _build_lens_actual,
    "metric_truth": _build_metric_actual,
    "profile_recall_projection": _build_recall_actual,
    "events_service": _build_events_actual,
}


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _assert_section(section: str) -> None:
    golden = _golden()[section]
    actual = _norm(_BUILDERS[section]())
    for key in golden:
        assert key in actual, f"{section}.{key} 在重构后消失了"
        assert actual[key] == golden[key], (
            f"{section}.{key} 行为漂移:\n改后={actual[key]!r}\n黄金={golden[key]!r}"
        )
    assert sorted(actual) == sorted(golden), f"{section} 键集合漂移: {sorted(actual)} != {sorted(golden)}"


def test_lens_evidence_characterization() -> None:
    _assert_section("lens_evidence")


def test_metric_truth_characterization() -> None:
    _assert_section("metric_truth")


def test_profile_recall_projection_characterization() -> None:
    """召回链投影(_format_item 等)逐键相等断言。"""
    _assert_section("profile_recall_projection")


def test_events_service_characterization() -> None:
    _assert_section("events_service")


def test_event_visibility_sql_both_dialects(monkeypatch) -> None:
    """两方言谓词逐字节锁定(binds 三个位置不变:owner/team/share)。"""
    monkeypatch.setattr(ev, "is_postgres_runtime", lambda: True)
    assert ev._event_visibility_sql() == (
        "(owner_id = ? OR team_ids @> to_jsonb(?::bigint) "
        "OR id IN (SELECT event_id FROM vkpi_event_members WHERE staff_id = ?) "
        "OR COALESCE(is_public, FALSE) = TRUE)"
    )
    monkeypatch.setattr(ev, "is_postgres_runtime", lambda: False)
    assert ev._event_visibility_sql() == (
        "(owner_id = ? OR EXISTS ("
        "SELECT 1 FROM json_each(CASE WHEN json_valid(team_ids) THEN team_ids ELSE '[]' END) AS event_team "
        "WHERE CAST(event_team.value AS INTEGER) = ?"
        ") OR id IN (SELECT event_id FROM vkpi_event_members WHERE staff_id = ?) "
        "OR COALESCE(is_public, 0) = 1)"
    )


def _regenerate() -> None:
    payload = {name: _norm(builder()) for name, builder in _BUILDERS.items()}
    GOLDEN_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"regenerated {GOLDEN_PATH.relative_to(ROOT)}")


if __name__ == "__main__":  # pragma: no cover - 维护入口(须用户批准)
    if "--regenerate" in sys.argv:
        _regenerate()
    else:
        print("usage: python tests/test_cc_quad_characterization.py --regenerate  # 须用户批准")
