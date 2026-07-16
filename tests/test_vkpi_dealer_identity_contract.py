from __future__ import annotations

import pytest

from app.domains.commerce.dealer_identity import (
    build_alias_contract,
    normalize_identity_text,
    normalize_official_domain,
    propose_stable_location_key,
    propose_stable_org_key,
)


def test_identity_normalization_is_deterministic_without_fuzzy_claims():
    assert normalize_identity_text("  Samy’s Camera · Pasadena ") == "samy s camera pasadena"
    assert normalize_identity_text("B&H PHOTO") == "b and h photo"
    assert normalize_official_domain("https://www.Example.COM/stores") == "example.com"


def test_stable_org_and_location_key_are_repeatable_and_location_specific():
    org_a = propose_stable_org_key("Samy's Camera", country_code="US", official_domain="samys.com")
    org_b = propose_stable_org_key("Samy's Camera", country_code="US", official_domain="https://www.samys.com/")
    assert org_a == org_b

    los_angeles = propose_stable_location_key(
        org_a,
        country_code="US",
        address="431 S Fairfax Ave",
        postal_code="90036",
    )
    pasadena = propose_stable_location_key(
        org_a,
        country_code="US",
        address="1759 E Colorado Blvd",
        postal_code="91106",
    )
    assert los_angeles.startswith("dealer_loc_")
    assert los_angeles != pasadena


def test_alias_contract_preserves_evidence_without_business_inference():
    contract = build_alias_contract(
        alias_type="event_host",
        alias_value="Samy's Camera · Pasadena",
        country_code="US",
        source_url="https://samysphotoschool.com/events/",
    )

    assert contract == {
        "alias_type": "event_host",
        "alias_value": "Samy's Camera · Pasadena",
        "alias_normalized": "samy s camera pasadena",
        "country_code": "US",
        "source_url": "https://samysphotoschool.com/events/",
    }
    assert "authorization" not in contract
    assert "stock" not in contract


@pytest.mark.parametrize(
    "kwargs",
    [
        {"alias_type": "unknown", "alias_value": "Dealer"},
        {"alias_type": "official_name", "alias_value": ""},
        {"alias_type": "official_name", "alias_value": "Dealer", "country_code": "USA"},
        {"alias_type": "official_name", "alias_value": "Dealer", "source_url": "http://example.com"},
    ],
)
def test_alias_contract_fails_closed_on_unreviewable_identity(kwargs):
    with pytest.raises(ValueError):
        build_alias_contract(**kwargs)
