from __future__ import annotations

import json
from datetime import datetime, timezone

from app.domains.channels import refill
from app.domains.costs import budget_guard
from app.domains.ops import health_sentinel


def _at(hour: int = 12) -> datetime:
    return datetime(2026, 7, 15, hour, 0, tzinfo=timezone.utc)


def test_daily_sync_uses_durable_official_receipts_not_kol_only_run(monkeypatch) -> None:
    monkeypatch.setattr(health_sentinel, "_utcnow", _at)
    monkeypatch.setattr(
        health_sentinel,
        "table_exists",
        lambda table: table in {"job_execution_ledger", "vkpi_employee_channels", "vkpi_sync_runs"},
    )
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: object())
    monkeypatch.setattr(health_sentinel, "_rows", lambda *_args, **_kwargs: [])

    def fake_row(_conn, sql, _params=()):
        if "COUNT(*) AS n FROM vkpi_employee_channels" in sql:
            return {"n": 18}
        if "MAX(COALESCE(finished_at,updated_at))" in sql:
            return {"at": "2026-05-18T16:53:54Z"}
        if "SELECT stage,status FROM vkpi_sync_runs" in sql:
            return {"stage": "kol_pool_light", "status": "completed"}
        raise AssertionError(sql)

    monkeypatch.setattr(health_sentinel, "_row", fake_row)
    result = health_sentinel._check_daily_sync()
    assert result["status"] == "fail"
    assert "0/18" in result["detail"]
    assert "kol_pool_light/completed" in result["detail"]
    assert "不作官号同步证据" in result["detail"]


def test_daily_sync_requires_unique_channel_coverage(monkeypatch) -> None:
    monkeypatch.setattr(health_sentinel, "_utcnow", _at)
    monkeypatch.setattr(
        health_sentinel,
        "table_exists",
        lambda table: table in {"job_execution_ledger", "vkpi_employee_channels"},
    )
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: object())
    monkeypatch.setattr(
        health_sentinel,
        "_row",
        lambda _conn, sql, _params=(): {"n": 2}
        if "COUNT(*) AS n FROM vkpi_employee_channels" in sql
        else {},
    )
    monkeypatch.setattr(
        health_sentinel,
        "_rows",
        lambda *_args, **_kwargs: [
            {"status": "done", "payload_json": json.dumps({"channel_id": 102})},
            {"status": "done", "payload_json": {"channel_id": 103}},
            # Duplicate receipt must not inflate coverage above the channel count.
            {"status": "done", "payload_json": json.dumps({"channel_id": 103})},
        ],
    )
    result = health_sentinel._check_daily_sync()
    assert result["status"] == "ok"
    assert "2/2" in result["detail"]


def test_official_metrics_requires_full_fresh_provenance_coverage(monkeypatch) -> None:
    monkeypatch.setattr(health_sentinel, "_utcnow", _at)
    monkeypatch.setattr(
        health_sentinel,
        "table_exists",
        lambda table: table in {"vkpi_channel_metrics", "vkpi_employee_channels"},
    )
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: object())
    monkeypatch.setattr(
        health_sentinel,
        "_rows",
        lambda *_args, **_kwargs: [
            {
                "channel_id": 102,
                "latest_captured": "2026-07-15T11:00:00Z",
                "fresh": 1,
                "fresh_with_provenance": 1,
            },
            {
                "channel_id": 103,
                "latest_captured": "2026-07-15T11:00:00Z",
                "fresh": 1,
                "fresh_with_provenance": 0,
            },
        ],
    )
    result = health_sentinel._check_official_metrics()
    assert result["status"] == "fail"
    assert "provider provenance 仅 1/2" in result["detail"]


def test_failed_pool_separates_historical_debt_from_recent_outage(monkeypatch) -> None:
    monkeypatch.setattr(health_sentinel, "table_exists", lambda table: table == "apify_jobs")
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: object())
    monkeypatch.setattr(
        health_sentinel,
        "_row",
        lambda *_args, **_kwargs: {
            "triage": 23,
            "failed": 398,
            "triage_24h": 1,
            "failed_24h": 0,
            "triage_7d": 2,
            "failed_7d": 0,
        },
    )
    result = health_sentinel._check_failed_pool()
    assert result["status"] == "warn"
    assert "历史债" in result["detail"]
    assert "不冒充当前 provider 故障" in result["detail"]


def test_failed_pool_recent_surge_remains_red(monkeypatch) -> None:
    monkeypatch.setattr(health_sentinel, "table_exists", lambda table: table == "apify_jobs")
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: object())
    monkeypatch.setattr(
        health_sentinel,
        "_row",
        lambda *_args, **_kwargs: {
            "triage": 12,
            "failed": 9,
            "triage_24h": 12,
            "failed_24h": 8,
            "triage_7d": 12,
            "failed_7d": 8,
        },
    )
    assert health_sentinel._check_failed_pool()["status"] == "fail"


def test_empty_hot_is_an_evidence_gap_not_a_fake_scheduler_diagnosis(monkeypatch) -> None:
    monkeypatch.setattr(
        health_sentinel,
        "table_exists",
        lambda table: table in {"vkpi_kol_refresh_tier", "vkpi_kol_pool"},
    )
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: object())

    def fake_row(_conn, sql, _params=()):
        if "FROM vkpi_kol_refresh_tier" in sql:
            return {"tier_rows": 32, "hot_total": 0, "warm_total": 32, "refreshed": 0}
        if "FROM vkpi_kol_pool" in sql:
            return {"n": 1375}
        raise AssertionError(sql)

    monkeypatch.setattr(health_sentinel, "_row", fake_row)
    result = health_sentinel._check_kol_hot_refresh()
    assert result["status"] == "warn"
    assert "tier 已物化 32/1375" in result["detail"]
    assert "未自动伪造 hot 标签" in result["detail"]


def test_provider_budget_label_and_spend_are_not_misreported_as_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        health_sentinel,
        "table_exists",
        lambda table: table == "vkpi_provider_budget_caps",
    )
    monkeypatch.setattr(
        budget_guard,
        "get_budget_status",
        lambda: {
            "budgets": [
                {
                    "scope": "provider:apify",
                    "current_spend": 54.2741,
                    "cap_usd": 40,
                    "hard_stopped": True,
                    "warning": True,
                }
            ]
        },
    )
    result = health_sentinel._check_llm_budget()
    assert result["status"] == "warn"
    assert result["label"] == "外部 Provider 预算闸状态"
    assert "provider:apify($54.27/$40.00)" in result["detail"]


def test_official_snapshot_day_uses_declared_business_timezone() -> None:
    # 16:30 UTC is still July 15 in New York, but already July 16 in the
    # declared V-KPI business timezone. Host timezone must not change the key.
    assert refill._today(datetime(2026, 7, 15, 16, 30, tzinfo=timezone.utc)) == "2026-07-16"
