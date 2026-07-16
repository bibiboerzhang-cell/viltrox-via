from __future__ import annotations

from app.domains.dashboard.metric_maturity import (
    dashboard_window_metrics_contract,
    maturity_from_days,
    maturity_payload_for_days,
    normalize_dashboard_scope,
    snapshot_days_by_scope,
)
from app.domains.dashboard import metric_maturity


def test_maturity_label_starts_accumulating() -> None:
    payload = maturity_from_days(0, "owned")

    assert payload["snapshot_days"] == 0
    assert payload["required_days"] == 30
    assert payload["is_ready"] is False
    assert payload["maturity_label"] == "累积中 0/30"


def test_maturity_label_becomes_ready_after_30_days() -> None:
    payload = maturity_from_days(30, "kol")

    assert payload["snapshot_days"] == 30
    assert payload["is_ready"] is True
    assert payload["maturity_label"] == "真实 · 30d ready"


def test_all_scope_requires_both_owned_and_kol_maturity() -> None:
    contract = maturity_payload_for_days({"owned": 30, "kol": 0})

    assert contract["scopes"]["owned"]["maturity_label"] == "真实 · 30d ready"
    assert contract["scopes"]["kol"]["maturity_label"] == "累积中 0/30"
    assert contract["scopes"]["all"]["maturity_label"] == "累积中 0/30"


def test_company_scope_aliases_to_owned() -> None:
    assert normalize_dashboard_scope("company") == "owned"
    assert normalize_dashboard_scope("owned_matrix") == "owned"
    assert normalize_dashboard_scope("unexpected") == "all"


def test_window_metrics_remain_null_until_snapshot_mature() -> None:
    metrics = dashboard_window_metrics_contract(maturity_payload_for_days({"owned": 0, "kol": 0}))

    assert metrics["active_30d_by_scope"] == {"owned": None, "kol": None, "all": None}
    assert metrics["exposure_30d_by_scope"] == {"owned": None, "kol": None, "all": None}
    assert metrics["engagement_rate_by_scope"] == {"owned": None, "kol": None, "all": None}


def test_missing_kol_snapshot_table_is_not_probed(monkeypatch) -> None:
    queried_tables: list[str] = []

    def count_days(table_name: str) -> int:
        queried_tables.append(table_name)
        return 12

    monkeypatch.setattr(metric_maturity, "_count_distinct_snapshot_dates", count_days)

    assert snapshot_days_by_scope() == {"owned": 12, "kol": 0, "all": 0}
    assert queried_tables == ["vkpi_channel_post_metrics"]
