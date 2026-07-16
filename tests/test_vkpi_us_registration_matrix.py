from __future__ import annotations

from app.shared.us_jurisdiction_coverage import registered_us_jurisdiction_matrix


def test_us_registration_matrix_is_bounded_and_never_claims_market_coverage():
    result = registered_us_jurisdiction_matrix(["ca", "NY", "DC", "CA", "PR", ""])

    assert result["covered_states"] == ["CA", "DC", "NY"]
    assert result["covered_count"] == 3
    assert result["jurisdiction_count"] == 51
    assert "PR" not in result["covered_states"]
    assert result["authoritative_market_denominator"] is None
    assert result["coverage_rate"] is None
    assert result["claim_status"] == "descriptive_only"
    assert len(result["missing_states"]) == 48
