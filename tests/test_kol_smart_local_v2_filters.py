from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.kol import profile_recall, profile_recall_qualification
from app.domains.kol.profile_recall_search_spec import (
    parse_operator_languages,
    parse_operator_profile_types,
)


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def _item(item_id: int, *, handle: str | None = None, channel_name: str = "") -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": handle or f"creator-{item_id}",
        "channel_name": channel_name or f"Creator {item_id}",
        "platform": "youtube",
        "bucket": "creator",
        "display_rank_score": 1.0,
        "recall_rank_score": 1.0,
        "match_evidence": [{"field": "bio", "term": "lens"}],
    }


def _row(
    item_id: int,
    *,
    country: str | None = "US",
    language: str | None = "en",
    profile_type: str | None = "creator",
    raw_platform_data: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "followers": 5_000,
        "country": country,
        "language": language,
        "profile_type": profile_type,
        "platform": "youtube",
        "bio": "Independent photographer testing camera lenses in the field.",
        "raw_platform_data": raw_platform_data or {},
        **extra,
    }


def _evidence(*, identity: bool = True, active: bool = True, age_days: int = 5) -> dict[str, Any]:
    latest: dict[str, Any] = {
        "posted_at": (NOW - timedelta(days=age_days)).isoformat(),
        "evidence_type": "video",
        "is_active": active,
        "source": "vkpi_kol_video_evidence.posted_at",
    }
    if identity:
        latest["content_url"] = "https://www.youtube.com/watch?v=auditable"
    return {"latest_real_video": latest}


def _qualify(
    items: list[dict[str, Any]],
    rows: dict[int, dict[str, Any]],
    evidence: dict[int, dict[str, Any]],
    *,
    market: str = "US",
    languages: Any = None,
    profile_types: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, _, contract = profile_recall_qualification.qualify_local_candidates(
        buckets={"creator": items, "reviewer": []},
        rows_by_id=rows,
        evidence_by_id=evidence,
        policy=profile_recall_qualification.smart_local_policy(
            market=market,
            platforms=["youtube"],
            languages=languages,
            profile_types=profile_types,
        ),
        creator_quota=30,
        reviewer_quota=0,
        as_of=NOW,
    )
    return selected, contract


def test_operator_filter_parser_is_bounded_and_retains_invalid_values() -> None:
    languages = parse_operator_languages(["English", "ja-JP", "Klingon"])
    assert languages == {
        "requested": True,
        "values": ["en", "ja"],
        "invalid": ["klingon"],
        "maximum": 8,
    }
    profile_types = parse_operator_profile_types(["content creator", "评测者", "shop"])
    assert profile_types == {
        "requested": True,
        "values": ["creator", "reviewer"],
        "invalid": ["shop"],
        "maximum": 3,
    }


def test_explicit_language_and_profile_type_are_hard_gates() -> None:
    items = [_item(item_id) for item_id in range(1, 6)]
    rows = {
        1: _row(1),
        2: _row(2, language="ja"),
        3: _row(3, profile_type="reviewer"),
        4: _row(4, language=None),
        5: _row(5, profile_type=None),
    }
    selected, contract = _qualify(
        items,
        rows,
        {item_id: _evidence() for item_id in rows},
        languages=["English"],
        profile_types=["creator"],
    )
    assert [item["kol_pool_id"] for item in selected] == [1]
    assert contract["rejected_by_reason"] == {
        "language_mismatch": 1,
        "language_unknown": 1,
        "profile_type_mismatch": 1,
        "profile_type_unknown": 1,
    }
    stages = [
        contract["funnel"][key]
        for key in (
            "canonical_unique",
            "account_quality_pass",
            "followers_pass",
            "fresh_video_pass",
            "market_pass",
            "language_pass",
            "profile_type_pass",
            "platform_pass",
            "qualified",
            "returned",
        )
    ]
    assert stages == sorted(stages, reverse=True)


def test_invalid_explicit_filter_fails_closed_instead_of_widening() -> None:
    item = _item(1)
    selected, contract = _qualify(
        [item],
        {1: _row(1)},
        {1: _evidence()},
        languages=["klingon"],
    )
    assert selected == []
    assert contract["policy"]["languages"] == []
    assert contract["policy"]["operator_filters"]["languages"]["invalid"] == ["klingon"]
    assert contract["rejected_by_reason"] == {"language_filter_invalid": 1}


def test_market_gate_rejects_profile_annotation_and_llm_but_accepts_audience_or_declaration() -> None:
    items = [_item(item_id) for item_id in range(1, 5)]
    rows = {
        1: _row(1, country=None, raw_platform_data={
            "market_inference": {
                "value": "US", "confidence": 0.99, "strength": "strong", "source": "profile_annotation",
            }
        }),
        2: _row(2, country=None, raw_platform_data={
            "market_inference": {
                "value": "US", "confidence": 0.8, "source": "audience_profile_distribution",
            }
        }),
        3: _row(3, country="US", country_source="llm_inference"),
        4: _row(4, country="US", country_source="declared_profile"),
    }
    selected, contract = _qualify(
        items,
        rows,
        {item_id: _evidence() for item_id in rows},
    )
    assert [item["kol_pool_id"] for item in selected] == [2, 4]
    assert contract["rejected_by_reason"] == {"market_untrusted_source": 2}
    market = selected[0]["qualification_evidence"]["market"]
    assert market["method"] == "strong_audience_inference"
    assert market["confidence"] == 0.8


@pytest.mark.parametrize(
    "source",
    ["llm_audience_inference", "model_audience_geo", "profile_annotation_audience"],
)
def test_market_gate_rejects_model_sources_even_when_they_contain_audience(source: str) -> None:
    rows = {1: _row(1, country=None, raw_platform_data={
        "audience_market_inference": {
            "value": "US",
            "confidence": 0.95,
            "source": source,
        }
    })}
    selected, contract = _qualify(
        [_item(1)], rows, {1: _evidence()}
    )
    assert selected == []
    assert contract["rejected_by_reason"] == {"market_untrusted_source": 1}


def test_market_gate_accepts_only_allowlisted_high_confidence_audience_source() -> None:
    rows = {1: _row(1, country=None, raw_platform_data={
        "audience_market_inference": {
            "value": "US",
            "confidence": 0.9,
            "source": "audience_multi_signal_v1",
        }
    })}
    selected, contract = _qualify(
        [_item(1)], rows, {1: _evidence()}
    )
    assert [item["kol_pool_id"] for item in selected] == [1]
    market = contract["gate_evidence"][0]["market"]
    assert market["method"] == "strong_audience_inference"
    assert market["source"] == "audience_multi_signal_v1"
    assert market["confidence"] == 0.9


def test_pool_country_does_not_override_recorded_untrusted_inference_source() -> None:
    rows = {
        1: _row(1, raw_platform_data={
            "market_annotation": {"value": "US", "confidence": 0.99, "source": "profile_annotation"},
        }),
        2: _row(2, raw_platform_data={
            "market_inference": {"value": "US", "confidence": 0.99, "source": "llm_inference"},
        }),
        3: _row(3, raw_platform_data={
            "qualification_annotations": {
                "country": {"value": "US", "confidence": 0.99, "source": "model_inference"},
            },
        }),
        4: _row(4),
    }
    selected, contract = _qualify(
        [_item(item_id) for item_id in rows],
        rows,
        {item_id: _evidence() for item_id in rows},
    )
    assert [item["kol_pool_id"] for item in selected] == [4]
    assert contract["rejected_by_reason"] == {"market_untrusted_source": 3}


def test_unknown_country_passes_when_operator_did_not_request_market() -> None:
    item = _item(1)
    selected, _ = _qualify(
        [item],
        {1: _row(1, country=None)},
        {1: _evidence()},
        market="",
    )
    assert [candidate["kol_pool_id"] for candidate in selected] == [1]
    assert item["qualification_evidence"]["market"]["value"] is None
    assert item["qualification_evidence"]["market"]["passed"] is True


def test_existing_discovery_classifiers_exclude_own_brand_official_and_retailer_accounts() -> None:
    items = [
        _item(1, handle="viltrox.official", channel_name="Viltrox Official"),
        _item(2, handle="sony.official", channel_name="Sony"),
        _item(3, handle="focuscenter", channel_name="Focus Camera Store"),
        _item(4, handle="field-lens-notes", channel_name="Field Lens Notes"),
        _item(5),
    ]
    items[4]["handle"] = ""
    items[4]["channel_name"] = "Unknown Creator"
    rows = {
        item_id: _row(item_id, channel_name=items[item_id - 1]["channel_name"])
        for item_id in range(1, 6)
    }
    selected, contract = _qualify(
        items,
        rows,
        {item_id: _evidence() for item_id in rows},
    )
    assert [item["kol_pool_id"] for item in selected] == [4]
    assert contract["rejected_by_reason"] == {
        "account_own_brand": 1,
        "account_brand_official": 1,
        "account_retailer": 1,
        "account_garbage": 1,
    }


def test_recent_video_requires_active_type_and_auditable_identity() -> None:
    items = [_item(item_id) for item_id in range(1, 4)]
    rows = {item_id: _row(item_id) for item_id in range(1, 4)}
    evidence = {
        1: _evidence(identity=False),
        2: _evidence(active=False),
        3: _evidence(),
    }
    selected, contract = _qualify(items, rows, evidence)
    assert [item["kol_pool_id"] for item in selected] == [3]
    assert contract["rejected_by_reason"] == {
        "latest_video_identity_missing": 1,
        "latest_video_not_active_video": 1,
    }
    activity = selected[0]["qualification_evidence"]["activity"]
    assert activity["identity_kind"] == "content_url"
    assert activity["identity"].endswith("v=auditable")


def test_recent_video_title_without_stable_url_or_video_id_is_not_auditable() -> None:
    title_only = _evidence(identity=False)
    title_only["latest_real_video"]["title"] = "A recent-looking title is not a video identity"
    selected, contract = _qualify([_item(1)], {1: _row(1)}, {1: title_only})
    assert selected == []
    assert contract["rejected_by_reason"] == {"latest_video_identity_missing": 1}


def test_canonical_handle_is_unicode_safe_and_preserves_platform_punctuation() -> None:
    items = [
        _item(1, handle="未来写真"),
        _item(2, handle="未来写真"),
        _item(3, handle="lens-pro"),
        _item(4, handle="lenspro"),
    ]
    selected, contract = _qualify(
        items,
        {item_id: _row(item_id) for item_id in range(1, 5)},
        {item_id: _evidence() for item_id in range(1, 5)},
    )
    assert {item["kol_pool_id"] for item in selected} == {1, 3, 4}
    assert contract["rejected_by_reason"] == {"duplicate_canonical_identity": 1}


def test_missing_field_level_relevance_evidence_never_counts_as_qualified() -> None:
    item = _item(1)
    item["match_evidence"] = []
    selected, contract = _qualify([item], {1: _row(1)}, {1: _evidence()})
    assert selected == []
    assert contract["qualified_count"] == 0
    assert contract["funnel"]["evidence_relevant"] == 0
    assert contract["rejected_by_reason"] == {"low_relevance": 1}
    proof = contract["rejected_evidence_sample"][0]
    assert proof["passed"] is False
    assert proof["relevance"]["passed"] is False


def test_evidence_summary_carries_latest_video_identity(monkeypatch) -> None:
    class _Result:
        def fetchall(self) -> list[dict[str, Any]]:
            return [{
                "kol_pool_id": 7,
                "title": "Auditable field review",
                "content_url": "https://www.youtube.com/watch?v=proof",
                "thumbnail_url": "",
                "view_count": 10,
                "like_count": 1,
                "posted_at": NOW.isoformat(),
                "evidence_type": "video",
                "content_summary": "",
                "product_presence": "",
                "brand_exposure": "",
            }]

    class _Conn:
        def execute(self, _sql: str, _params: tuple[int]) -> _Result:
            return _Result()

    monkeypatch.setattr(profile_recall, "get_conn", lambda: _Conn())
    latest = profile_recall._evidence_summaries([7])[7]["latest_real_video"]
    assert latest["content_url"] == "https://www.youtube.com/watch?v=proof"
    assert latest["title"] == "Auditable field review"
