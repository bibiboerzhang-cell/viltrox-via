from __future__ import annotations

from typing import Any

from app.domains.data_quality import checks
from app.domains.data_quality import common


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, action_rows: list[dict[str, Any]], events: list[Any]) -> None:
        self.action_rows = action_rows
        self.events = events

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        self.events.append(("action_sql", sql, params))
        return _Rows(self.action_rows)


def test_list_issues_characterizes_query_order_scope_actions_and_projection(monkeypatch) -> None:
    events: list[Any] = []
    query_calls: list[tuple[str, tuple[Any, ...]]] = []
    action_rows = [
        {
            "issue_id": "amazon_missing_asin:sales_attribution:61",
            "action": "reopen",
        },
        {
            "issue_id": "amazon_missing_asin:sales_attribution:61",
            "action": "resolve",
        },
        {
            "issue_id": "amazon_missing_campaign:sales_attribution:61",
            "action": "resolve",
        },
    ]
    connection = _Connection(action_rows, events)

    for name in (
        "ensure_vkpi_schema",
        "ensure_vkpi_lineage_schema",
        "ensure_vkpi_reconciliation_schema",
    ):
        monkeypatch.setattr(checks, name, lambda name=name: events.append(("schema", name)))
    monkeypatch.setattr(
        checks,
        "get_conn",
        lambda: events.append(("get_conn",)) or connection,
    )
    monkeypatch.setattr(checks, "_utcnow", lambda: "2026-08-29T00:00:00Z")
    monkeypatch.setattr(common, "_utcnow", lambda: "2026-08-29T00:00:00Z")

    def fake_safe_rows(_conn, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        query_calls.append((sql, params))
        normalized = " ".join(sql.split())
        if "LOWER(source_platform)='amazon'" in normalized:
            return [
                {
                    "id": 61,
                    "source_platform": "amazon",
                    "source_ref": "report-61",
                    "project_id": 9,
                    "kol_id": 8,
                    "staff_id": 7,
                    "amazon_campaign_id": None,
                    "occurred_at": "2020-01-01",
                    "imported_at": "2020-01-02",
                    "evidence_json": "{}",
                }
            ]
        if "FROM kols" in normalized:
            return [
                {
                    "id": 12,
                    "platform": "youtube",
                    "channel_name": "first",
                    "channel_url": "https://youtube.test/shared/",
                    "contact_email": "",
                    "assigned_staff_id": 7,
                    "created_by_staff_id": 2,
                },
                {
                    "id": 11,
                    "platform": "youtube",
                    "channel_name": "second",
                    "channel_url": "https://youtube.test/shared",
                    "contact_email": "",
                    "assigned_staff_id": 7,
                    "created_by_staff_id": 3,
                },
            ]
        return []

    monkeypatch.setattr(checks, "_safe_rows", fake_safe_rows)

    def append_operational_quality_issues(*, conn, issues, staff, max_items) -> None:
        events.append(("operational", conn, staff, max_items))
        issues.append(
            {
                "id": "operational:critical:1",
                "issue_type": "operational",
                "severity": "critical",
                "created_at": "2026-08-29T00:00:00Z",
            }
        )

    monkeypatch.setattr(
        checks,
        "append_operational_quality_issues",
        append_operational_quality_issues,
    )

    result = checks.list_issues(limit=3, staff={"id": 7, "role": "member"})

    assert [event[:2] for event in events[:4]] == [
        ("schema", "ensure_vkpi_schema"),
        ("schema", "ensure_vkpi_lineage_schema"),
        ("schema", "ensure_vkpi_reconciliation_schema"),
        ("get_conn",),
    ]
    assert len(query_calls) == 14
    normalized_queries = [" ".join(sql.split()) for sql, _params in query_calls]
    expected_markers = [
        "vkpi_reconciliation_queue",
        "project_id IS NULL OR kol_id IS NULL OR staff_id IS NULL",
        "shopify_order_snapshot_id IS NULL",
        "GROUP BY sa.shopify_order_snapshot_id",
        "LOWER(COALESCE(os.refund_status, ''))",
        "LOWER(source_platform)='amazon'",
        "COALESCE(health_status,'unknown')",
        "TRIM(COALESCE(utm_source,''))",
        "p.stage IN ('published','measured','closed')",
        "p.stage IN ('shipped','received','published','measured','closed')",
        "LOWER(source_platform) IN ('manual','custom')",
        "p.stage_status='deleted'",
        "COALESCE(c.status,'actual') != 'void'",
        "FROM kols",
    ]
    assert all(marker in query for marker, query in zip(expected_markers, normalized_queries))
    assert [params for _sql, params in query_calls] == [
        (7, 3),
        (7, 3),
        (7, 3),
        (7, 3),
        (7, 3),
        (7, 3),
        (7, 3),
        (7, 3),
        (7, 7, 3),
        (7, 7, 3),
        (7, 3),
        (7, 3),
        (7, 3),
        (7, 7),
    ]
    assert events[-2][0] == "operational"
    assert events[-1][0] == "action_sql"
    assert "FROM vkpi_data_quality_actions" in events[-1][1]
    assert events[-1][2] == ()
    assert result == {
        "status": "ok",
        "generated_at": "2026-08-29T00:00:00Z",
        "count": 3,
        "total_count": 4,
        "issues": [
            {
                "id": "operational:critical:1",
                "issue_type": "operational",
                "severity": "critical",
                "created_at": "2026-08-29T00:00:00Z",
            },
            {
                "id": "amazon_missing_asin:sales_attribution:61",
                "issue_type": "amazon_missing_asin",
                "severity": "medium",
                "title": "Amazon 归因缺少 ASIN，无法稳定绑定产品",
                "entity_type": "sales_attribution",
                "entity_id": 61,
                "staff_id": 7,
                "project_id": 9,
                "kol_id": 8,
                "detail": "report-61",
                "evidence": {
                    "id": 61,
                    "source_platform": "amazon",
                    "source_ref": "report-61",
                    "project_id": 9,
                    "kol_id": 8,
                    "staff_id": 7,
                    "amazon_campaign_id": None,
                    "occurred_at": "2020-01-01",
                    "imported_at": "2020-01-02",
                    "evidence_json": "{}",
                    "normalized": {},
                },
                "created_at": "2026-08-29T00:00:00Z",
            },
            {
                "id": "duplicate_kol_candidate:kol:e28b2f19007b5d60",
                "issue_type": "duplicate_kol_candidate",
                "severity": "medium",
                "title": "疑似重复红人档案",
                "entity_type": "kol",
                "entity_id": "e28b2f19007b5d60",
                "staff_id": 7,
                "project_id": None,
                "kol_id": 11,
                "detail": "url · KOL IDs 11, 12",
                "evidence": {
                    "dedup_key": "url:youtube:https://youtube.test/shared",
                    "kol_ids": [11, 12],
                    "rows": [
                        {
                            "id": 12,
                            "platform": "youtube",
                            "channel_name": "first",
                            "channel_url": "https://youtube.test/shared/",
                            "contact_email": "",
                            "assigned_staff_id": 7,
                            "created_by_staff_id": 2,
                        },
                        {
                            "id": 11,
                            "platform": "youtube",
                            "channel_name": "second",
                            "channel_url": "https://youtube.test/shared",
                            "contact_email": "",
                            "assigned_staff_id": 7,
                            "created_by_staff_id": 3,
                        },
                    ],
                },
                "created_at": "2026-08-29T00:00:00Z",
            },
        ],
        "summary": {"critical": 1, "high": 0, "medium": 2, "low": 1, "info": 0},
    }
