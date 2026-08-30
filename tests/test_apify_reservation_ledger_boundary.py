from __future__ import annotations

from app.domains.costs import budget_guard
from app.platform import apify_budget
from app.platform import apify_budget_contracts
from app.shared import apify_reservation_ledger


def test_platform_and_cost_facades_share_one_reservation_settlement_implementation() -> None:
    assert apify_budget.settle_apify_reservation is apify_reservation_ledger.settle_apify_reservation
    assert budget_guard.settle_apify_reservation is apify_reservation_ledger.settle_apify_reservation
    assert apify_budget_contracts.APIFY_BUDGET_SCOPE == apify_reservation_ledger.APIFY_BUDGET_SCOPE


def test_empty_reservation_keeps_the_public_no_write_result() -> None:
    expected = {"settled": False, "reason": "no_reservation"}
    assert apify_reservation_ledger.settle_apify_reservation("", 1.25) == expected
    assert apify_budget.settle_apify_reservation("", 1.25) == expected
