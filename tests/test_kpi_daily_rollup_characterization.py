"""Frozen fake-DB contracts for the employee KPI daily rollup split."""
from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.domains.staff import kpi_ledger
from scripts.vkpi_engineering_health_collect import collect_complexity


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class RowResult:
    def __init__(self, row: dict[str, Any]):
        self.row = row

    def fetchone(self) -> dict[str, Any]:
        return self.row


@dataclass
class RollupHarness:
    events: list[tuple[Any, ...]] = field(default_factory=list)
    queries: list[tuple[str, str, tuple[Any, ...]]] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    store: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    audit_error: BaseException | None = None
    fetch_error_label: str = ""
    commit_error: BaseException | None = None
    total_error: BaseException | None = None
    total_row: dict[str, Any] | None = None
    empty_sources: bool = False

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(kpi_ledger, "ensure_vkpi_schema", self.ensure_main)
        monkeypatch.setattr(
            kpi_ledger,
            "ensure_vkpi_product_industry_schema",
            self.ensure_product,
        )
        monkeypatch.setattr(kpi_ledger, "utcnow", lambda: "2026-08-30T03:04:05Z")
        monkeypatch.setattr(kpi_ledger, "get_conn", lambda: self)
        monkeypatch.setattr(kpi_ledger, "is_postgres_runtime", lambda: False)
        monkeypatch.setattr(kpi_ledger, "_fetchall", self.fetchall)
        monkeypatch.setattr(kpi_ledger, "_upsert_entry", self.upsert)
        monkeypatch.setattr(
            kpi_ledger,
            "_ledger_source_query",
            self.ledger_source_query,
        )
        monkeypatch.setattr(
            kpi_ledger.business_truth,
            "current_kpi_ledger_sql",
            lambda alias="": f"current_truth({alias or 'root'})=1",
        )
        monkeypatch.setattr(
            kpi_ledger.business_truth,
            "approved_actual_cost_sql",
            lambda: "approved_actual=1",
        )
        monkeypatch.setattr(
            kpi_ledger.business_truth,
            "verified_shopify_attribution_sql",
            lambda: "verified_attribution=1",
        )
        monkeypatch.setattr(kpi_ledger.audit, "log_business_event", self.audit)

    def ensure_main(self) -> None:
        self.events.append(("ensure", "main"))

    def ensure_product(self) -> None:
        self.events.append(("ensure", "product_industry"))

    @staticmethod
    def _normalized(sql: str) -> str:
        return " ".join(str(sql).split())

    def _query_label(self, normalized: str) -> str:
        if "FROM vkpi_kol_claims" in normalized:
            return "claims"
        if "FROM vkpi_project_stage_events" in normalized:
            return "stage_events"
        if "FROM vkpi_link_clicks" in normalized:
            return "clicks"
        if "FROM vkpi_links" in normalized:
            return "links"
        if "FROM vkpi_content_posts" in normalized:
            return "content"
        if "FROM vkpi_cost_ledger" in normalized:
            return "costs"
        if "FROM vkpi_sales_attributions" in normalized:
            return "attributions"
        if "o.attributed_clicks" in normalized:
            return "recommendation_metrics"
        if "AS event_at" in normalized:
            time_columns = {
                "shortlisted": "shortlisted_at",
                "rejected": "rejected_at",
                "claimed": "claimed_at",
                "project_created": "project_created_at",
                "outreach_sent": "outreach_sent_at",
                "reply_received": "reply_at",
                "agreement_reached": "agreement_at",
                "content_published": "content_published_at",
                "order_attributed": "first_order_at",
            }
            for label, column in time_columns.items():
                if f"o.{column} AS event_at" in normalized:
                    return f"recommendation_{label}"
            raise AssertionError(f"unrecognized recommendation event query: {normalized}")
        if "FROM vkpi_projects" in normalized:
            return "projects"
        raise AssertionError(f"unrecognized rollup query: {normalized}")

    def fetchall(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        normalized = self._normalized(sql)
        label = self._query_label(normalized)
        bound = tuple(params)
        self.queries.append((label, normalized, bound))
        self.events.append(("fetch", label, bound))
        if label == self.fetch_error_label:
            raise RuntimeError(f"fetch failed: {label}")
        return deepcopy(self._rows(label))

    def _recommendation_row(self) -> dict[str, Any]:
        return {
            "outcome_id": 801,
            "recommendation_id": 701,
            "launch_id": 601,
            "kol_pool_id": 501,
            "kol_id": 41,
            "platform": "youtube",
            "handle": "creator_1",
            "score": 92.5,
            "rank": 1,
            "staff_id": 7,
            "event_at": "2026-08-29T12:00:00Z",
        }

    def _rows(self, label: str) -> list[dict[str, Any]]:
        if self.empty_sources:
            return []
        if label == "claims":
            row = {
                "id": 11,
                "staff_id": 7,
                "kol_id": 41,
                "project_id": 21,
                "status": "active",
            }
            return [row, deepcopy(row)]
        if label == "projects":
            return [
                {
                    "id": 21,
                    "staff_id": 7,
                    "kol_id": 41,
                    "product_sku": "AF 16/1.8",
                    "project_name": "Launch",
                }
            ]
        if label == "stage_events":
            return [
                {
                    "id": 31,
                    "project_id": 21,
                    "to_stage": " Agreed ",
                    "event_type": "manual",
                    "staff_id": 7,
                    "kol_id": 41,
                    "product_sku": "AF 16/1.8",
                },
                {
                    "id": 32,
                    "project_id": 21,
                    "to_stage": "",
                    "staff_id": 7,
                    "kol_id": 41,
                },
            ]
        if label == "links":
            return [
                {
                    "id": 51,
                    "staff_id": 7,
                    "kol_id": 41,
                    "project_id": 21,
                    "product_sku": "AF 16/1.8",
                    "slug": "launch-link",
                }
            ]
        if label == "clicks":
            return [
                {
                    "link_id": 51,
                    "staff_id": 7,
                    "kol_id": 41,
                    "project_id": 21,
                    "valid_clicks": "3",
                    "bot_clicks": 2,
                },
                {
                    "link_id": 52,
                    "staff_id": 7,
                    "kol_id": 41,
                    "project_id": 21,
                    "valid_clicks": 0,
                    "bot_clicks": "bad",
                },
            ]
        if label == "content":
            return [
                {
                    "id": 61,
                    "project_id": 21,
                    "kol_id": 41,
                    "platform": "youtube",
                    "post_url": "https://youtube.test/1",
                    "views": "100",
                    "likes": 10,
                    "staff_id": 7,
                    "product_sku": "AF 16/1.8",
                },
                {
                    "id": 62,
                    "project_id": 21,
                    "kol_id": 41,
                    "platform": "instagram",
                    "post_url": "https://instagram.test/2",
                    "views": 0,
                    "likes": 0,
                    "staff_id": 7,
                    "product_sku": "AF 16/1.8",
                },
            ]
        if label == "costs":
            return [
                {
                    "id": 71,
                    "staff_id": 7,
                    "kol_id": 41,
                    "project_id": 21,
                    "cost_type": "creator_fee",
                    "amount_cents": "2500",
                    "status": "approved",
                    "source_ref": "invoice-1",
                }
            ]
        if label == "attributions":
            return [
                {
                    "id": 81,
                    "staff_id": 7,
                    "kol_id": 41,
                    "project_id": 21,
                    "link_id": 51,
                    "source_platform": "shopify",
                    "source_ref": "order-1",
                    "revenue_cents": "10000",
                    "confidence": "confirmed",
                },
                {
                    "id": 82,
                    "staff_id": 7,
                    "kol_id": 41,
                    "project_id": 21,
                    "link_id": 51,
                    "source_platform": "shopify",
                    "source_ref": "order-2",
                    "revenue_cents": 4000,
                    "confidence": "estimated",
                },
            ]
        if label.startswith("recommendation_") and label != "recommendation_metrics":
            return [self._recommendation_row()]
        if label == "recommendation_metrics":
            return [
                {
                    **self._recommendation_row(),
                    "attributed_clicks": "5",
                    "attributed_gmv_cents": 20000,
                    "attributed_cost_cents": 3000,
                    "computed_roi": "2.5",
                }
            ]
        raise AssertionError(label)

    def upsert(self, _conn: Any, **kwargs: Any) -> str:
        key = (
            str(kwargs["ledger_date"]),
            str(kwargs["metric_key"]),
            str(kwargs["source_ref"]),
        )
        status = "updated" if key in self.store else "inserted"
        item = {
            "ledger_date": kwargs["ledger_date"],
            "staff_id": kwargs.get("staff_id"),
            "kol_id": kwargs.get("kol_id"),
            "project_id": kwargs.get("project_id"),
            "metric_key": kwargs["metric_key"],
            "metric_value": float(kwargs["metric_value"]),
            "source_type": kwargs["source_type"],
            "source_ref": kwargs["source_ref"],
            "confidence": kwargs.get("confidence", "confirmed"),
            "metadata": deepcopy(kwargs.get("metadata") or {}),
            "created_at": kwargs.get("now"),
        }
        self.store[key] = item
        self.events.append(
            (
                "upsert",
                status,
                item["metric_key"],
                item["source_ref"],
                item["metric_value"],
                item["staff_id"],
                item["project_id"],
                item["confidence"],
            )
        )
        return status

    def ledger_source_query(
        self, day: str, staff_id: int | None
    ) -> list[dict[str, Any]]:
        self.events.append(("ledger_source", day, staff_id))
        rows = [
            deepcopy(row)
            for row in self.store.values()
            if row["ledger_date"] == day
            and (not staff_id or row.get("staff_id") == staff_id)
        ]
        rows.sort(
            key=lambda row: (
                int(row.get("staff_id") or 0),
                int(row.get("project_id") or 0),
                str(row.get("metric_key") or ""),
                str(row.get("source_ref") or ""),
            )
        )
        return rows

    def commit(self) -> None:
        self.events.append(("commit",))
        if self.commit_error is not None:
            raise self.commit_error

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> RowResult:
        normalized = self._normalized(sql)
        assert normalized.startswith(
            "SELECT COUNT(*) AS n FROM vkpi_kpi_ledger"
        )
        self.events.append(("total", tuple(params)))
        if self.total_error is not None:
            raise self.total_error
        if self.total_row is not None:
            return RowResult(deepcopy(self.total_row))
        day = str(params[0])
        staff_id = int(params[1]) if len(params) > 1 else None
        count = sum(
            1
            for row in self.store.values()
            if row["ledger_date"] == day
            and (staff_id is None or row.get("staff_id") == staff_id)
        )
        return RowResult({"n": count})

    def audit(self, **kwargs: Any) -> None:
        self.events.append(("audit", kwargs["staff_id"], kwargs["target_id"]))
        self.audit_events.append(deepcopy(kwargs))
        if self.audit_error is not None:
            raise self.audit_error


EXPECTED_QUERY_ORDER = [
    "claims",
    "projects",
    "stage_events",
    "links",
    "clicks",
    "content",
    "costs",
    "attributions",
    "recommendation_shortlisted",
    "recommendation_rejected",
    "recommendation_claimed",
    "recommendation_project_created",
    "recommendation_outreach_sent",
    "recommendation_reply_received",
    "recommendation_agreement_reached",
    "recommendation_content_published",
    "recommendation_order_attributed",
    "recommendation_metrics",
]


def test_daily_rollup_freezes_scope_date_metrics_money_and_return_order(
    monkeypatch,
) -> None:
    harness = RollupHarness()
    harness.install(monkeypatch)
    actor = {"id": 7, "role": "staff", "tenant_id": "tenant-a"}

    result = kpi_ledger.generate_daily_rollup(
        "2026-08-29T23:59:59-07:00",
        staff_id=99,
        actor_staff=actor,
    )

    assert _digest(result) == "d01497ccce590acc7447eda5b13777525cd5584e3dbc67b870368d966558920a"
    assert list(result) == [
        "ledger_date",
        "staff_id",
        "inserted",
        "updated",
        "total_entries",
        "metric_counts",
        "workload_weights",
    ]
    assert result["ledger_date"] == "2026-08-29"
    assert result["staff_id"] == 7
    assert [label for label, _sql, _params in harness.queries] == EXPECTED_QUERY_ORDER
    assert all(params == ("2026-08-29", 7) for _label, _sql, params in harness.queries)
    assert harness.events[-3:] == [
        ("commit",),
        ("total", ("2026-08-29", 7)),
        ("audit", 7, "2026-08-29"),
    ]
    assert harness.audit_events[0]["metadata"] == {
        "ledger_date": "2026-08-29",
        "staff_id": 7,
        "metrics": result["metric_counts"],
        "status_counts": {
            "inserted": result["inserted"],
            "updated": result["updated"],
        },
    }
    assert any(
        event[:5]
        == (
            "upsert",
            "inserted",
            "cost_cents",
            "cost:71",
            2500.0,
        )
        for event in harness.events
    )
    assert any(
        event[:5]
        == (
            "upsert",
            "inserted",
            "estimated_revenue_cents",
            "attribution:82",
            4000.0,
        )
        for event in harness.events
    )


def test_daily_rollup_rerun_updates_stable_sources_without_double_counting(
    monkeypatch,
) -> None:
    harness = RollupHarness()
    harness.install(monkeypatch)
    actor = {"id": 1, "role": "manager", "tenant_id": "tenant-a"}

    first = kpi_ledger.generate_daily_rollup(
        "2026-08-29",
        staff_id=7,
        actor_staff=actor,
    )
    unique_after_first = len(harness.store)
    second = kpi_ledger.generate_daily_rollup(
        "2026-08-29",
        staff_id=7,
        actor_staff=actor,
    )

    assert _digest(first) == "d01497ccce590acc7447eda5b13777525cd5584e3dbc67b870368d966558920a"
    assert _digest(second) == "0e3e11501e4372eeb9ecc6f3736d37cd4d826d838c8b6b127360686a875d5398"
    assert len(harness.store) == unique_after_first
    assert second["inserted"] == 0
    assert second["updated"] > 0
    claim_events = [
        event for event in harness.events
        if event[0] == "upsert" and event[3] == "claim:11"
    ]
    assert [event[1] for event in claim_events] == [
        "inserted",
        "updated",
        "updated",
        "updated",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "2026-08-30"),
        ("", "2026-08-30"),
        (" 2026-08-09T01:02:03+09:00 ", "2026-08-09"),
        ("short", "short"),
    ],
)
def test_daily_rollup_keeps_literal_utc_day_slicing(
    monkeypatch, value: str | None, expected: str
) -> None:
    harness = RollupHarness()
    harness.install(monkeypatch)

    result = kpi_ledger.generate_daily_rollup(value, staff_id=7)

    assert result["ledger_date"] == expected
    assert result["staff_id"] == 7
    assert harness.audit_events[0]["staff_id"] == 7


def test_daily_rollup_audit_failure_is_swallowed_after_commit_and_count(
    monkeypatch,
) -> None:
    harness = RollupHarness(audit_error=RuntimeError("audit unavailable"))
    harness.install(monkeypatch)

    result = kpi_ledger.generate_daily_rollup("2026-08-29", staff_id=7)

    assert result["total_entries"] == len(harness.store)
    assert harness.events[-3:] == [
        ("commit",),
        ("total", ("2026-08-29", 7)),
        ("audit", 7, "2026-08-29"),
    ]


@pytest.mark.parametrize(
    ("actor", "expected_audit_staff"),
    [
        (None, 0),
        ({"id": 1, "role": "manager", "tenant_id": "tenant-a"}, 1),
    ],
)
def test_daily_rollup_empty_unscoped_run_keeps_all_staff_scope_and_zero_result(
    monkeypatch,
    actor: dict[str, Any] | None,
    expected_audit_staff: int,
) -> None:
    harness = RollupHarness(empty_sources=True)
    harness.install(monkeypatch)

    result = kpi_ledger.generate_daily_rollup(
        "2026-08-29",
        staff_id=None,
        actor_staff=actor,
    )

    assert result == {
        "ledger_date": "2026-08-29",
        "staff_id": None,
        "inserted": 0,
        "updated": 0,
        "total_entries": 0,
        "metric_counts": {},
        "workload_weights": kpi_ledger.WORKLOAD_WEIGHTS,
    }
    assert all(params == ("2026-08-29",) for _label, _sql, params in harness.queries)
    assert harness.events[-3:] == [
        ("commit",),
        ("total", ("2026-08-29",)),
        ("audit", expected_audit_staff, "2026-08-29"),
    ]


def test_daily_rollup_fetch_failure_propagates_without_commit_or_audit(
    monkeypatch,
) -> None:
    harness = RollupHarness(fetch_error_label="content")
    harness.install(monkeypatch)

    with pytest.raises(RuntimeError, match="^fetch failed: content$"):
        kpi_ledger.generate_daily_rollup("2026-08-29", staff_id=7)

    assert not any(event[0] in {"commit", "total", "audit"} for event in harness.events)
    assert harness.store


def test_daily_rollup_commit_failure_stops_total_and_audit(monkeypatch) -> None:
    expected = RuntimeError("commit failed")
    harness = RollupHarness(commit_error=expected)
    harness.install(monkeypatch)

    with pytest.raises(RuntimeError) as captured:
        kpi_ledger.generate_daily_rollup("2026-08-29", staff_id=7)

    assert captured.value is expected
    assert harness.events[-1] == ("commit",)
    assert not any(event[0] in {"total", "audit"} for event in harness.events)


def test_daily_rollup_total_failure_propagates_after_commit_without_audit(
    monkeypatch,
) -> None:
    expected = RuntimeError("total failed")
    harness = RollupHarness(total_error=expected)
    harness.install(monkeypatch)

    with pytest.raises(RuntimeError) as captured:
        kpi_ledger.generate_daily_rollup("2026-08-29", staff_id=7)

    assert captured.value is expected
    assert harness.events[-2:] == [
        ("commit",),
        ("total", ("2026-08-29", 7)),
    ]
    assert not harness.audit_events


def test_daily_rollup_malformed_total_audits_before_return_conversion_error(
    monkeypatch,
) -> None:
    harness = RollupHarness(total_row={"n": "not-a-number"})
    harness.install(monkeypatch)

    with pytest.raises(ValueError):
        kpi_ledger.generate_daily_rollup("2026-08-29", staff_id=7)

    assert harness.events[-3:] == [
        ("commit",),
        ("total", ("2026-08-29", 7)),
        ("audit", 7, "2026-08-29"),
    ]


def test_daily_rollup_family_complexity_size_and_dependency_are_bounded() -> None:
    runtime = getattr(kpi_ledger, "_rollup_runtime", None)
    assert runtime is not None
    modules = (kpi_ledger, runtime)
    rows = []
    for module in modules:
        path = Path(module.__file__)
        source = path.read_text(encoding="utf-8")
        rows.extend(collect_complexity({str(path): ast.parse(source)}))
        assert len(source.splitlines()) < 800
    public = next(
        row for row in rows
        if row.path == str(Path(kpi_ledger.__file__))
        and row.qualified_name == "generate_daily_rollup"
    )

    assert public.cc <= 10
    assert max(row.cc for row in rows) <= 30
    runtime_source = Path(runtime.__file__).read_text(encoding="utf-8")
    assert "kpi_ledger import" not in runtime_source
