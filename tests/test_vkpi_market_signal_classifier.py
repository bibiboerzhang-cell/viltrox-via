from __future__ import annotations

from app.domains.market.signal_classifier import _classify_row


def test_classifier_keeps_viltrox_mentions_out_of_competitor_candidates() -> None:
    item = _classify_row(
        {
            "id": 7,
            "platform": "reddit",
            "handle": "unit",
            "mention_text": "XM5 with Viltrox 28mm f4.5 pancake lens",
            "product_sku": "viltrox",
            "competitor_product": "",
            "score": 0.36,
            "metadata_json": {
                "source_url": "https://example.test/viltrox",
                "keyword_groups": {"viltrox_products": ["viltrox"]},
                "keyword_hits": ["viltrox", "lens"],
            },
        }
    )

    assert item["category"] == "viltrox_mention"
    assert item["signal_type"] == "viltrox_product_mention"
    assert item["proposed_signals"] == []


def test_classifier_promotes_competitor_mention_as_review_candidate() -> None:
    item = _classify_row(
        {
            "id": 8,
            "platform": "reddit",
            "handle": "unit",
            "mention_text": "Mirrorless equivalent of D7500 with Sigma lens",
            "product_sku": "",
            "competitor_product": "sigma",
            "score": 0.35,
            "metadata_json": {
                "source_url": "https://example.test/sigma",
                "keyword_groups": {"tier1_lens_competitors": ["sigma"]},
                "keyword_hits": ["sigma", "lens"],
            },
        }
    )

    assert item["category"] == "competitor_signal"
    assert item["signal_type"] == "product_comparison"
    assert len(item["proposed_signals"]) == 1
    assert item["proposed_signals"][0]["brand"] == "sigma"
    assert item["proposed_signals"][0]["source_table"] == "vkpi_market_mentions"
    assert item["proposed_signals"][0]["review_status"] == "pending_review"


def test_classifier_problem_text_becomes_voc_issue() -> None:
    item = _classify_row(
        {
            "id": 9,
            "platform": "reddit",
            "handle": "unit",
            "mention_text": "24-70 f2.8 Samyang Faulty Aperture",
            "product_sku": "",
            "competitor_product": "samyang",
            "score": 0.314,
            "metadata_json": {
                "source_url": "https://example.test/samyang",
                "keyword_groups": {"tier1_lens_competitors": ["samyang"]},
                "keyword_hits": ["samyang", "lens"],
            },
        }
    )

    assert item["category"] == "competitor_signal"
    assert item["signal_type"] == "voc_issue"
    assert item["severity"] == "medium"


def test_classifier_normalizes_competitor_aliases() -> None:
    item = _classify_row(
        {
            "id": 11,
            "platform": "reddit",
            "handle": "unit",
            "mention_text": "Recipes to achieve this look?",
            "product_sku": "",
            "competitor_product": "tt artisan, ttartisan",
            "score": 0.314,
            "metadata_json": {
                "source_url": "https://example.test/ttartisan",
                "keyword_groups": {"tier1_lens_competitors": ["tt artisan", "ttartisan"]},
                "keyword_hits": ["tt artisan", "ttartisan"],
            },
        }
    )

    assert item["competitor_products"] == ["ttartisan"]
    assert len(item["proposed_signals"]) == 1


def test_classifier_generic_market_text_stays_noise() -> None:
    item = _classify_row(
        {
            "id": 10,
            "platform": "reddit",
            "handle": "unit",
            "mention_text": "A generic wide angle lens question",
            "product_sku": "",
            "competitor_product": "",
            "score": 0.2,
            "metadata_json": {
                "source_url": "https://example.test/generic",
                "keyword_groups": {"generic_imaging_terms": ["wide angle", "lens"]},
                "keyword_hits": ["wide angle", "lens"],
            },
        }
    )

    assert item["category"] == "noise"
    assert item["proposed_signals"] == []
