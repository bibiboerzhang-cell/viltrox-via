from __future__ import annotations

from typing import Any

from app.services.vkpi import refresh_tier


def _kol(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": 101,
        "pool_uid": "pool:unit",
        "platform": "youtube",
        "handle": "unit_creator",
        "linked_main_kol_id": None,
    }
    base.update(overrides)
    return base


def _install_compute_harness(monkeypatch, kol: dict[str, Any], existing: dict[str, Any] | None = None) -> None:
    monkeypatch.setattr(refresh_tier, "_load_kol_row", lambda _kol_pool_id: dict(kol))
    monkeypatch.setattr(refresh_tier, "_existing_tier", lambda _kol_pool_id: dict(existing or {}))
    monkeypatch.setattr(refresh_tier, "_active_campaign_count", lambda _kol: 0)
    monkeypatch.setattr(refresh_tier, "_recent_project_count", lambda _kol, days=180: 0)
    monkeypatch.setattr(refresh_tier, "_shipping_count", lambda _kol, days=90: 0)
    monkeypatch.setattr(
        refresh_tier,
        "_brand_signal_counts",
        lambda _kol, days=60: {"self_total": 0, "viltrox_mentions": 0, "sku_mentions": 0},
    )


def test_compute_tier_manual_hot_wins(monkeypatch) -> None:
    _install_compute_harness(monkeypatch, _kol(), {"manual_hot_flag": True})

    result = refresh_tier.compute_kol_tier(101)

    assert result["tier"] == "hot"
    assert result["tier_reason"] == "manual_hot_flag"


def test_compute_tier_recent_sku_signal_is_hot(monkeypatch) -> None:
    _install_compute_harness(monkeypatch, _kol())
    monkeypatch.setattr(
        refresh_tier,
        "_brand_signal_counts",
        lambda _kol, days=60: {"self_total": 2, "viltrox_mentions": 0, "sku_mentions": 2},
    )

    result = refresh_tier.compute_kol_tier(101)

    assert result["tier"] == "hot"
    assert result["tier_reason"] == "sku_mention_60d"


def test_compute_tier_recent_search_is_warm(monkeypatch) -> None:
    _install_compute_harness(monkeypatch, _kol(), {"search_count_30d": 1})

    result = refresh_tier.compute_kol_tier(101)

    assert result["tier"] == "warm"
    assert result["tier_reason"] == "search_30d"


def test_qualified_refresh_rows_do_not_fallback_without_tier_table(monkeypatch) -> None:
    monkeypatch.setattr(refresh_tier, "_table_exists", lambda table: False)

    rows = refresh_tier.qualified_refresh_rows(limit=50)
    counts = refresh_tier.qualified_source_counts()

    assert rows == []
    assert counts["selector_ready"] is False
    assert counts["source_total"] == 0
