from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domains.dashboard import summary as dashboard_summary
from app.domains.dashboard import summary_company
from app.domains.dashboard.summary_company import (
    _build_company_metric_series_from_snapshots,
)


def _daily_snapshots_with_latest_gap() -> tuple[
    list[int], list[dict[str, object]], list[dict[str, object]]
]:
    latest = date(2026, 7, 10)
    rows: list[dict[str, object]] = []
    row_id = 1
    for offset in range(-60, 1):
        snapshot_date = latest + timedelta(days=offset)
        age = offset + 60
        for channel_id in (1, 2, 3):
            if channel_id == 3 and snapshot_date == latest:
                continue
            total_views = channel_id * 100_000 + age * 100
            rows.append(
                {
                    "id": row_id,
                    "channel_id": channel_id,
                    "snapshot_date": snapshot_date,
                    "captured_at": f"{snapshot_date.isoformat()}T08:00:00Z",
                    "posts_count": 10 + age // 10,
                    "views_delta_24h": 100,
                    "total_views": total_views,
                    "total_likes": total_views // 10,
                    "total_comments": total_views // 100,
                }
            )
            row_id += 1
    post_rows = [
        {
            "channel_id": channel_id,
            "snapshot_date": latest,
            "posted_at": latest - timedelta(days=5),
            "views_delta": 10,
            "likes_delta": 1,
            "comments_delta": 0,
        }
        for channel_id in (1, 2, 3)
    ]
    return [1, 2, 3], rows, post_rows


def test_company_series_carries_missing_account_without_exposure_cliff():
    channel_ids, rows, post_rows = _daily_snapshots_with_latest_gap()

    series = _build_company_metric_series_from_snapshots(
        channel_ids, rows, post_rows=post_rows
    )

    assert set(series) == {"kol-count", "active-30d", "exposure", "engagement"}
    for item in series.values():
        assert len(item["points"]) == 30
        assert [point["date"] for point in item["points"]] == sorted(
            point["date"] for point in item["points"]
        )
        assert item["window_days"] == 30
        assert item["source_dates"] == sorted(item["source_dates"])

    exposure = series["exposure"]
    previous, latest = exposure["points"][-2:]
    assert previous["value"] == 9_000
    assert latest["value"] == 8_900
    assert latest["value"] > previous["value"] * 0.9
    assert latest["covered_accounts"] == 3
    assert latest["direct_snapshot_accounts"] == 2
    assert latest["carried_forward_accounts"] == 0
    assert exposure["delta_pct"] == pytest.approx(-1.111111)

    coverage = exposure["coverage"]
    assert coverage["official_accounts"] == 3
    assert coverage["eligible_accounts_latest"] == 3
    assert coverage["eligible_ratio_latest"] == 1.0
    assert coverage["direct_snapshot_accounts_latest"] == 2
    assert coverage["carried_forward_accounts_latest"] == 0
    assert coverage["baseline_direct_accounts_latest"] is None
    assert coverage["baseline_carried_forward_accounts_latest"] is None
    assert series["kol-count"]["points"][-1]["value"] == 3
    assert series["active-30d"]["points"][-1]["value"] == 3
    assert series["engagement"]["points"][-1]["value"] == pytest.approx(11.0)


def test_company_series_without_snapshots_is_explicitly_empty():
    series = _build_company_metric_series_from_snapshots([1, 2], [])

    for item in series.values():
        assert item["points"] == []
        assert item["delta_pct"] is None
        assert item["source_dates"] == []
        assert item["coverage"]["official_accounts"] == 2
        assert item["coverage"]["eligible_accounts_latest"] == 0
        assert item["coverage"]["eligible_ratio_latest"] == 0.0


def test_company_series_does_not_invent_active_accounts_without_post_evidence():
    series = _build_company_metric_series_from_snapshots(
        [1],
        [
            {
                "channel_id": 1,
                "snapshot_date": "2026-07-10",
                "posts_count": 20,
                "total_views": 1_000,
                "total_likes": 100,
                "total_comments": 20,
            }
        ],
    )

    assert series["active-30d"]["points"] == []
    assert series["active-30d"]["delta_pct"] is None
    assert series["exposure"]["points"] == [
        {
            "date": "2026-07-10",
            "value": 0,
            "covered_accounts": 1,
            "coverage_pct": 1.0,
            "direct_snapshot_accounts": 1,
            "carried_forward_accounts": 0,
        }
    ]
    assert series["exposure"]["delta_pct"] is None
    assert series["kol-count"]["points"] == [
        {
            "date": "2026-07-10",
            "value": 1,
            "covered_accounts": 1,
            "coverage_pct": 1.0,
            "direct_snapshot_accounts": 1,
            "carried_forward_accounts": 0,
        }
    ]


def test_company_series_uses_daily_deltas_instead_of_bootstrap_total_jump():
    first = date(2026, 5, 16)
    rows = [
        {
            "channel_id": 1,
            "snapshot_date": first,
            "posts_count": 0,
            "views_delta_24h": 0,
            "total_views": 0,
            "total_likes": 0,
            "total_comments": 0,
        },
        {
            "channel_id": 1,
            "snapshot_date": first + timedelta(days=1),
            "posts_count": 100,
            "views_delta_24h": 1_000,
            "total_views": 10_000_000,
            "total_likes": 100_000,
            "total_comments": 10_000,
        },
        {
            "channel_id": 1,
            "snapshot_date": first + timedelta(days=30),
            "posts_count": 110,
            "views_delta_24h": 2_000,
            "total_views": 10_100_000,
            "total_likes": 101_000,
            "total_comments": 10_100,
        },
        {
            "channel_id": 1,
            "snapshot_date": first + timedelta(days=31),
            "posts_count": 111,
            "views_delta_24h": 3_000,
            "total_views": 10_110_000,
            "total_likes": 101_100,
            "total_comments": 10_110,
        },
    ]

    series = _build_company_metric_series_from_snapshots([1], rows)

    exposure = series["exposure"]
    assert len(exposure["points"]) == 30
    assert exposure["points"][-1]["date"] == "2026-06-16"
    assert exposure["points"][-1]["value"] == 5_000
    assert max(point["value"] for point in exposure["points"]) == 5_000
    assert exposure["delta_pct"] == pytest.approx(400.0)
    assert series["active-30d"]["points"] == []


def test_company_series_query_is_read_only_and_uses_metric_source_tables(monkeypatch):
    sql_calls: list[str] = []

    def fake_fetch(sql: str):
        sql_calls.append(sql)
        return [{"channel_id": 1}] if len(sql_calls) == 1 else []

    monkeypatch.setattr(summary_company, "_fetch_dicts", fake_fetch)

    series = summary_company._build_company_metric_series()

    assert len(sql_calls) == 3
    assert "vkpi_employee_channels" in sql_calls[0]
    assert "vkpi_employee_channels" in sql_calls[1]
    assert "vkpi_channel_metrics" in sql_calls[1]
    assert "vkpi_channel_post_metrics" in sql_calls[2]
    combined_sql = " ".join(sql_calls).upper()
    assert all(keyword not in combined_sql for keyword in (" INSERT ", " UPDATE ", " DELETE "))
    assert all(item["points"] == [] for item in series.values())


def test_dashboard_summary_adds_company_and_owned_series_without_overwriting_other_scopes(monkeypatch):
    company_series = {
        metric: {
            "points": [{"date": "2026-07-10", "value": index}],
            "delta_pct": None,
            "window_days": 30,
            "basis": {"definition": metric},
            "coverage": {"official_accounts": 18},
            "source_dates": ["2026-07-10"],
        }
        for index, metric in enumerate(("kol-count", "active-30d", "exposure", "engagement"), 1)
    }
    maturity = {
        "scopes": {
            scope: {
                "scope_label": scope,
                "snapshot_days": 0,
                "required_days": 30,
                "maturity_label": "accumulating",
            }
            for scope in ("all", "kol", "owned")
        }
    }
    monkeypatch.setattr(dashboard_summary.scope, "effective_staff_id", lambda staff, staff_id: None)
    monkeypatch.setattr(dashboard_summary, "resolve_staff_id", lambda staff: 7)
    monkeypatch.setattr(
        dashboard_summary.decision_engine,
        "dashboard",
        lambda window_days: {
            "summary": {"metric_series_by_scope": {"all": {"existing": True}}}
        },
    )
    monkeypatch.setattr(
        dashboard_summary.metric_lineage,
        "dashboard_metrics",
        lambda **kwargs: {"run": {}, "metrics": []},
    )
    monkeypatch.setattr(dashboard_summary, "dashboard_metric_maturity_contract", lambda: maturity)
    monkeypatch.setattr(
        dashboard_summary,
        "dashboard_window_metrics_contract",
        lambda contract: {
            "exposure_30d_by_scope": {"owned": None, "kol": None, "all": None},
            "engagement_rate_by_scope": {"owned": None, "kol": None, "all": None},
            "active_30d_by_scope": {"owned": None, "kol": None, "all": None},
        },
    )
    monkeypatch.setattr(
        dashboard_summary,
        "build_dashboard_active_roster_counts",
        lambda **kwargs: {"all": 0, "kol": 0, "media": 0, "company": 0},
    )
    monkeypatch.setattr(dashboard_summary, "_cached_summary_block", lambda *args, **kwargs: {})
    monkeypatch.setattr(dashboard_summary, "_build_company_window_metrics", lambda: {})
    monkeypatch.setattr(dashboard_summary, "_build_company_metric_series", lambda **kwargs: company_series)
    monkeypatch.setattr(dashboard_summary, "_dashboard_official_matrix_summary", lambda limit: {})

    payload = dashboard_summary.build_dashboard_summary(window_days=14, staff_id=None, staff={"id": 7})
    by_scope = payload["summary"]["metric_series_by_scope"]

    assert by_scope["all"] == {"existing": True}
    assert by_scope["company"] == company_series
    assert by_scope["owned"] == company_series
