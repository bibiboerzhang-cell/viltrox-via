"""Pure queue-request extraction keeps the existing facade binding and contract."""
from copy import deepcopy

import pytest

from app.domains.kol import profile_discovery_queue as queue


def test_nested_filters_win_and_top_level_allowlist_is_unchanged():
    body = {
        "filters": {"languages": ["fr"], "countries": ["GB"]},
        "languages": ["en"], "countries": ["US"], "platforms": ["youtube"],
        "followers_min": 1000, "followers_max": 10000, "verticals": ["portrait"],
        "gear_content": False, "query_cells": [{"primary_query": "not-a-filter"}],
        "search_mode": "fresh_network", "product_sku": "not-a-filter",
    }
    original = deepcopy(body)
    result = queue._smart_profile_recall_filters(body)
    assert result == {
        "languages": ["fr"], "countries": ["GB"], "platforms": ["youtube"],
        "followers_min": 1000, "followers_max": 10000, "verticals": ["portrait"],
        "gear_content": False,
    }
    result["languages"].append("en")
    result["platforms"].append("instagram")
    assert body == original


def test_nested_platform_filter_is_not_overwritten():
    assert queue._smart_profile_recall_filters({
        "filters": {"platforms": ["instagram"]}, "platforms": ["youtube"],
    }) == {"platforms": ["instagram"]}


def test_alias_normalization_still_precedes_projection():
    assert queue._smart_profile_recall_filters({
        "content_languages": ["English"], "follower_min": 3000,
    }) == {"languages": ["English"], "followers_min": 3000}


@pytest.mark.parametrize("filters", [[], "", "en", 0, False])
def test_malformed_filters_keep_existing_error(filters):
    with pytest.raises(ValueError, match="filters must be an object"):
        queue._smart_profile_recall_filters({"filters": filters})


def test_original_module_normalizer_binding_remains_patchable(monkeypatch):
    calls = []

    def normalize(body):
        calls.append(body)
        return {"filters": {"languages": ["fr"]}}

    monkeypatch.setattr(queue, "normalize_operator_body", normalize)
    body = {"languages": ["en"]}
    assert queue._smart_profile_recall_filters(body) == {"languages": ["fr"]}
    assert calls == [body]
