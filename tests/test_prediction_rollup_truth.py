"""Pure claim gates for registered prediction rollups."""
from __future__ import annotations

import json

from app.domains.market_brain import gtm_prediction_producer, prediction_ledger


def test_binary_brier_is_task_aware_distinct_and_requires_fifty() -> None:
    coverage = {"claimable": True, "registered_due": 50, "verified_actual": 50}
    perfect = [
        {
            "run_id": f"r{i}", "outcome_id": i + 1,
            "task_type": "kol_outreach_reply_probability",
            "p50": 0.0, "actual_value": 0.0, "error_abs": 0.0,
            "verified_actual": True,
        }
        for i in range(50)
    ]
    good = prediction_ledger.weekly_rollup(perfect, outreach_coverage=coverage)
    assert good["wape"] is None and good["brier_score"] == 0.0
    assert good["binary_probability"]["claimable"] is True

    bad = prediction_ledger.weekly_rollup(
        [{**row, "p50": 1.0} for row in perfect], outreach_coverage=coverage,
    )
    assert bad["brier_score"] == 1.0
    only_five = prediction_ledger.weekly_rollup(perfect[:5], outreach_coverage=coverage)
    assert only_five["brier_score"] == 0.0
    assert only_five["binary_probability"]["claimable"] is False
    duplicate = prediction_ledger.weekly_rollup(
        [*perfect, dict(perfect[0])], outreach_coverage=coverage,
    )
    assert duplicate["brier_n"] == 49
    assert duplicate["binary_probability"]["invalid_n"] == 2
    assert duplicate["binary_probability"]["claimable"] is False

    one_outcome = prediction_ledger.weekly_rollup(
        [{**row, "outcome_id": 1} for row in perfect], outreach_coverage=coverage,
    )
    assert one_outcome["brier_n"] == 0
    assert one_outcome["binary_probability"]["claimable"] is False


def test_probability_bounds_reject_bool_nan_and_out_of_range() -> None:
    base = {
        "sku": "AF-26", "country": "US",
        "bet": {
            "action_type": "kol_outreach",
            "prediction_seed": {
                "schema": gtm_prediction_producer.PRODUCER_SCHEMA,
                "registry_key": gtm_prediction_producer.REGISTRY_KEY,
                "p10": 0.05, "p50": 0.1, "p90": 0.2,
                "confidence": "low", "channel": "youtube", "kol_pool_id": 17,
            },
        },
    }
    cases = (("p10", True), ("p50", float("nan")), ("p90", 1.1), ("p10", -0.1))
    for field, value in cases:
        payload = json.loads(json.dumps(base))
        payload["bet"]["prediction_seed"][field] = value
        seed, reason = gtm_prediction_producer._validated_seed(payload)
        assert seed is None and reason == "prediction_seed_interval_invalid"
