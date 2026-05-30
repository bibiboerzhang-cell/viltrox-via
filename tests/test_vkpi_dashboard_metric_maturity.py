from __future__ import annotations

from app.domains.dashboard.metric_maturity import (
    dashboard_window_metrics_contract,
    maturity_from_days,
    maturity_payload_for_days,
    normalize_dashboard_scope,
)


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
