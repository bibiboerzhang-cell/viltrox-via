#!/usr/bin/env python3
"""P4.3 gate for Daily Top100 source health.

This smoke is intentionally read-only. It does not call Apify, YouTube, LLMs, or
generate new digest rows. It only verifies that the current local environment
still has a real monitored-product source for Daily Top100.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("ENVIRONMENT", "local")

from app.db.connection import close_db_runtime  # noqa: E402
from audit_vkpi_daily_top100_source import audit_state  # noqa: E402


def _count_for_product(snapshot: dict, product_sku: str) -> int:
    total = 0
    for row in snapshot.get("assigned_by_product") or []:
        if str(row.get("product_sku") or "") == product_sku:
            total += int(row.get("assigned_count") or 0)
    return total


def main() -> None:
    try:
        state = audit_state(limit=20)
        blockers = state.get("blockers") or []
        assert state.get("status") == "ok", state
        assert not blockers, state
        assert int(state.get("enabled_monitored_products_count") or 0) >= 1, state

        real_skus = [str(item) for item in state.get("real_suggestion_skus") or [] if str(item)]
        assert real_skus, state

        candidates = state.get("product_candidates") or []
        candidate_skus = {str(item.get("product_sku") or "") for item in candidates}
        missing = [sku for sku in real_skus if sku not in candidate_skus]
        assert not missing, {"missing_candidate_skus": missing, "state": state}

        snapshot = state.get("digest_snapshot") or {}
        assigned = {sku: _count_for_product(snapshot, sku) for sku in real_skus}
        assert any(count > 0 for count in assigned.values()), {
            "message": "real product suggestions exist but no Daily Top100 digest items have been assigned",
            "assigned": assigned,
            "state": state,
        }

        stdout_out(
            json.dumps(
                {
                    "ok": True,
                    "real_suggestion_skus": real_skus,
                    "assigned_by_real_product": assigned,
                    "monitored_products_count": state.get("monitored_products_count"),
                    "enabled_monitored_products_count": state.get("enabled_monitored_products_count"),
                    "marker": "VKPI_P4_3_DAILY_TOP100_SOURCE_GATE_SMOKE_OK",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        close_db_runtime()


if __name__ == "__main__":
    main()
