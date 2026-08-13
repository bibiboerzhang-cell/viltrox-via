from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import product_resolver
from app.domains.kol import profile_recall_match_evidence as evidence


def _catalog_product(sku: str = "DC-550") -> dict[str, Any]:
    return {
        "sku": sku,
        "model_name": "DC-550 Pro",
        "marketing_name": "DC-550 5.5-inch Camera Monitor",
        "category_main": "Monitor",
        "category_detail": "Field Monitor",
        "series": "DC",
        "price_usd": 199,
        "description": "On-camera monitor",
    }


def test_exact_normalized_sku_resolver_returns_public_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _catalog_product()
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [product]},
    )

    resolved = product_resolver.resolve_product_sku("dc550")

    assert resolved is not None
    assert resolved["sku"] == "DC-550"
    assert resolved["model_name"] == "DC-550 Pro"
    assert resolved["specs_line"]


def test_exact_normalized_sku_resolver_rejects_ambiguous_catalog_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def catalog(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if kwargs.get("query"):
            return {"products": [_catalog_product("DC-550")]}
        return {"products": [_catalog_product("DC-550"), _catalog_product("DC_550")]}

    monkeypatch.setattr(product_resolver, "list_product_catalog", catalog)

    assert product_resolver.resolve_product_sku("DC-550") is None
    assert calls == [{"limit": 500}]
    assert product_resolver.resolve_product("dc550") is None


@pytest.mark.parametrize("typed_sku", ["AF-35", "AF35", "DC-X3"])
def test_free_text_unique_exact_sku_bypasses_generic_hit_threshold(
    monkeypatch: pytest.MonkeyPatch,
    typed_sku: str,
) -> None:
    product = {
        **_catalog_product(typed_sku if typed_sku != "AF35" else "AF-35"),
        "model_name": typed_sku,
    }
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [product]},
    )

    resolved = product_resolver.resolve_product(typed_sku)

    assert resolved is not None
    assert product_resolver._normkey(resolved["sku"]) == product_resolver._normkey(typed_sku)


def test_long_query_keeps_product_identity_in_returned_evidence() -> None:
    query = (
        "wedding portrait documentary travel automotive culinary sports "
        "realestate music commercial fashion editorial studio lighting"
    )
    result = evidence.build_match_evidence(
        {"bio": query},
        {"representative_evidence": [{"title": "Hands-on Viltrox DC-550 review"}]},
        query,
        required_product_terms=["DC-550"],
    )

    assert len(result) <= 12
    assert result[0] == {
        "field": "representative_evidence.title",
        "term": "dc-550",
        "source": "server_profile_evidence",
    }
    assert "dc-550" in evidence.why_fit_from_match_evidence(result)


@pytest.mark.parametrize(
    ("raw_country", "expected"),
    [
        ("美国", "us"),
        ("德国", "de"),
        ("日本", "jp"),
        ("印度尼西亚", "id"),
        ("中國", "cn"),
    ],
)
def test_candidate_country_facets_normalize_chinese_market_names(
    raw_country: str,
    expected: str,
) -> None:
    facets = evidence.candidate_facets({"country": raw_country}, {})

    assert facets["country"] == expected
