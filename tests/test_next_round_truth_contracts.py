from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _InventoryConn:
    def __init__(self, *, fail_old_schema: bool = False):
        self.fail_old_schema = fail_old_schema
        self.sql = ""

    def execute(self, sql: str, params=()):
        self.sql = sql
        if self.fail_old_schema:
            raise RuntimeError("column quantity_status does not exist")
        return _Rows([
            {
                "id": "verified-1",
                "sku": "REAL-1",
                "name": "Confirmed stock",
                "category": "lens",
                "qty": 1,
                "quantity_status": "manual_confirmed",
                "quantity_source": "manual_adjustment",
            }
        ])


def test_inventory_low_only_reads_confirmed_quantities() -> None:
    from app.domains.actions.producers import produce_inventory_low

    conn = _InventoryConn()
    result = produce_inventory_low(conn, threshold=5)

    assert "quantity_status IN ('manual_confirmed', 'source_confirmed')" in conn.sql
    assert len(result) == 1
    assert result[0]["payload"]["quantity_status"] == "manual_confirmed"


def test_inventory_low_fails_closed_before_truth_migration() -> None:
    from app.domains.actions.producers import produce_inventory_low

    assert produce_inventory_low(_InventoryConn(fail_old_schema=True)) == []


def test_dealer_candidates_are_source_backed_and_not_claimed_authorized() -> None:
    from app.domains.commerce.dealer_scrape import _fetch_candidates

    candidates = _fetch_candidates("reviewed_public_retailers_20260713", 20)

    assert len(candidates) == 5
    assert all(row["brand_listing_url"].startswith("https://") for row in candidates)
    assert all(row["location_source_url"].startswith("https://") for row in candidates)
    assert all(row.get("authorization_status") is None for row in candidates)


def test_youtube_audience_contract_separates_live_sample_from_durable_bridge() -> None:
    from app.domains.kol.audience_stats import _audience_source_contract

    contract = _audience_source_contract(
        "youtube",
        {"comments_scanned": 400},
        {
            "sample_size": 329,
            "comment_intel": {"sample_size": 400, "source": "youtube_api_sample"},
            "overlap": {"self_commenters": 0, "items": []},
        },
    )

    assert contract["profile_sample"] == {
        "source": "youtube_data_api_live_sample",
        "durable": False,
        "commenters": 329,
        "comments_scanned": 400,
    }
    assert contract["overlap"]["source"] == "vkpi_comments_pool_evidence"
    assert contract["overlap"]["durable"] is True
    assert contract["overlap"]["commenters"] == 0
