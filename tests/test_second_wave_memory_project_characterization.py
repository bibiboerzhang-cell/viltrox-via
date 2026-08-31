"""Behavior locks for the second-wave market, memory, media, and project splits."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.media import cache_migration
from app.domains.memory import legacy, legacy_build, market, market_legacy_build
from app.domains.projects import contracts_extract
from app.shared import vkpi_kpi_evidence


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.events: list[str] = []

    def execute(self, sql: str, _params: Any = None) -> _Cursor:
        self.events.append("execute:" + " ".join(sql.split())[:48])
        return _Cursor(self.rows)

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("fee_amount", 12, 12.0),
        ("deliverable_count", 0, 0),
        ("start_date", " 2026-08-31 ", "2026-08-31"),
        ("promised_publish_deadline", "2026-08-31T12:00:00Z", "2026-08-31T12:00:00Z"),
        ("fee_currency", " USD ", "USD"),
        ("platforms", [" YouTube ", ""], ["YouTube"]),
        ("deliverables", [{"platform": "youtube", "quantity": 1}], [{"platform": "youtube", "quantity": 1}]),
    ],
)
def test_contract_field_normalization_keeps_return_shapes(field: str, value: Any, expected: Any) -> None:
    assert contracts_extract._normalized_business_field(field, value) == expected


@pytest.mark.parametrize(
    ("field", "value", "error", "message"),
    [
        ("fee_amount", True, TypeError, "must be a number or null"),
        ("deliverable_count", -1, TypeError, "must be a non-negative integer or null"),
        ("start_date", "2026-02-31", ValueError, "must contain a valid ISO date"),
        ("promised_publish_deadline", "not-a-date", ValueError, "must be a valid ISO date/time"),
        ("platforms", [1], TypeError, "must be a list of strings"),
        ("deliverables", [{"quantity": True}], TypeError, "quantity must be a non-negative integer or null"),
        ("unknown", "x", KeyError, "'unknown'"),
    ],
)
def test_contract_field_normalization_keeps_errors(
    field: str,
    value: Any,
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message if error is not KeyError else "unknown"):
        contracts_extract._normalized_business_field(field, value)


def test_media_migration_dry_run_keeps_scan_and_sample_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "a" * 64
    missing = "b" * 64
    cache_file = tmp_path / digest
    cache_file.write_bytes(b"video")
    (tmp_path / f"{digest}.content-type").write_text("video/webm", encoding="utf-8")
    sidecars = {
        tmp_path / "one.json": {"platform": "youtube", "digest": digest, "video_id": "v1"},
        tmp_path / "two.json": {"platform": "youtube", "digest": missing, "video_id": "v2"},
        tmp_path / "three.json": {"platform": "tiktok", "digest": digest, "video_id": "v3"},
    }
    monkeypatch.setattr(cache_migration, "VIDEO_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        cache_migration,
        "_sidecar_entries",
        lambda: [
            {"sidecar_path": "invalid"},
            {"sidecar_path": tmp_path / "one.json", "size_bytes": 5},
            {"sidecar_path": tmp_path / "two.json", "size_bytes": 6},
            {"sidecar_path": tmp_path / "three.json", "size_bytes": 7},
        ],
    )
    monkeypatch.setattr(cache_migration, "_read_json_file", lambda path: sidecars[path])
    monkeypatch.setattr(
        cache_migration,
        "_legacy_bare_cache_entries",
        lambda: [{"cache_path": tmp_path / "legacy", "media_kind": "image", "digest": "c" * 64, "size_bytes": 8}],
    )
    monkeypatch.setattr(cache_migration, "_cached_asset_url_by_digest", lambda *_args: "")
    monkeypatch.setattr(cache_migration, "_media_cache_r2_enabled", lambda: True)

    result = cache_migration.migrate_local_video_cache_to_r2(limit=10)

    assert {
        key: result[key]
        for key in (
            "execute", "scanned", "eligible", "migrated", "skipped", "failed",
            "legacy_scanned", "legacy_eligible", "legacy_migrated", "limit", "platform",
        )
    } == {
        "execute": False,
        "scanned": 4,
        "eligible": 3,
        "migrated": 0,
        "skipped": 1,
        "failed": 0,
        "legacy_scanned": 1,
        "legacy_eligible": 1,
        "legacy_migrated": 0,
        "limit": 10,
        "platform": "all",
    }
    assert [item["status"] for item in result["sample"]] == [
        "would_migrate", "skipped", "would_migrate", "would_migrate_legacy",
    ]


def test_media_execute_checks_entries_before_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cache_migration, "_sidecar_entries", lambda: calls.append("entries") or [])
    monkeypatch.setattr(cache_migration, "_media_cache_r2_enabled", lambda: calls.append("configured") or False)

    result = cache_migration.migrate_local_video_cache_to_r2(execute=True)

    assert calls == ["entries", "configured"]
    assert result["status"] == "not_configured"
    assert set(result) == {"execute", "status", "message", "required_env"}


def test_kpi_evidence_keeps_query_and_entity_order(monkeypatch: pytest.MonkeyPatch) -> None:
    table_rows = {
        "vkpi_projects": {"id": 1, "project_name": "Project"},
        "FROM kols": {"id": 2, "channel_name": "Creator"},
        "vkpi_kol_claims": {"id": 3, "status": "active"},
        "vkpi_project_stage_events": {"id": 4, "to_stage": "contacted"},
        "vkpi_links": {"id": 5, "slug": "launch"},
        "vkpi_content_posts": {"id": 6, "title": "Video"},
        "vkpi_cost_ledger": {"id": 7, "cost_type": "sample"},
        "vkpi_sales_attributions": {"id": 8, "source_ref": "order", "shopify_order_snapshot_id": 9},
        "vkpi_shopify_order_snapshots": {"id": 9, "order_name": "#9"},
        "vkpi_kol_recommendations": {"id": 10, "recommendation_uid": "rec-10"},
        "vkpi_recommendation_outcomes": {"id": 11, "recommendation_id": 10},
        "vkpi_product_launches": {"id": 12, "name": "Launch"},
        "vkpi_kol_pool": {"id": 13, "handle": "creator"},
    }
    seen: list[str] = []

    def fake_row(_conn: Any, sql: str, _params: tuple[Any, ...]) -> dict[str, Any]:
        marker = next(key for key in table_rows if key in sql)
        seen.append(marker)
        return table_rows[marker]

    monkeypatch.setattr(vkpi_kpi_evidence, "_row", fake_row)
    source_row = {
        "project_id": 1,
        "kol_id": 2,
        "source_type": "content_post",
        "source_ref": "content:6:views",
        "metadata_json": json.dumps(
            {
                "claim_id": 3,
                "link_id": 5,
                "cost_id": 7,
                "attribution_id": 8,
                "recommendation_id": 10,
                "outcome_id": 11,
                "launch_id": 12,
                "kol_pool_id": 13,
                "formula": "gmv-cost",
                "components": ["gmv", "cost"],
            }
        ),
    }

    result = vkpi_kpi_evidence.enrich_kpi_source_row(None, source_row)

    assert seen == [
        "vkpi_projects", "FROM kols", "vkpi_kol_claims", "vkpi_links",
        "vkpi_content_posts", "vkpi_cost_ledger", "vkpi_sales_attributions",
        "vkpi_shopify_order_snapshots", "vkpi_kol_recommendations",
        "vkpi_recommendation_outcomes", "vkpi_product_launches", "vkpi_kol_pool",
    ]
    context = result["source_context"]
    assert [entity["type"] for entity in context["entities"]] == [
        "project", "kol", "claim", "link", "content_post", "cost", "attribution",
        "shopify_order", "recommendation", "recommendation_outcome", "launch", "kol_pool",
    ]
    assert context["formula"] == "gmv-cost"
    assert context["components"] == ["gmv", "cost"]
    assert context["entity_count"] == 12


def test_market_build_all_keeps_phase_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(market_legacy_build, "reset_scope", lambda *_args: calls.append("reset"))
    monkeypatch.setattr(market_legacy_build, "build_launch_plans", lambda *_args: calls.append("launch"))
    monkeypatch.setattr(market_legacy_build, "build_official_content", lambda *_args: calls.append("content"))
    monkeypatch.setattr(market_legacy_build, "build_official_materials", lambda *_args: calls.append("materials"))
    monkeypatch.setattr(market_legacy_build, "build_voc_alerts", lambda *_args: calls.append("voc"))

    market_legacy_build.build_all(None, 1, "scope", "batch", {}, {})

    assert calls == ["reset", "launch", "content", "materials", "voc"]


def test_market_builder_keeps_commit_and_rollback_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    success = _Connection()
    monkeypatch.setattr(market, "ensure_memory_schema", lambda: None)
    monkeypatch.setattr(market, "get_conn", lambda: success)
    monkeypatch.setattr(market, "_fetch_batch", lambda _uid: {"id": 7})
    monkeypatch.setattr(
        market_legacy_build,
        "build_all",
        lambda _conn, _batch_id, _scope, _uid, counters, _ops: counters.update({"launch_plan": 1}),
    )
    monkeypatch.setattr(market, "market_signal_summary", lambda **_kwargs: {"status": "ok"})

    result = market.build_market_memory_from_legacy_batch("b1")

    assert success.events == ["commit"]
    assert result == {"status": "ok", "batch_uid": "b1", "build_counts": {"launch_plan": 1}}

    failed = _Connection()
    monkeypatch.setattr(market, "get_conn", lambda: failed)
    monkeypatch.setattr(
        market_legacy_build,
        "build_all",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("phase failed")),
    )
    with pytest.raises(RuntimeError, match="phase failed"):
        market.build_market_memory_from_legacy_batch("b2")
    assert failed.events == ["rollback"]


def test_legacy_build_all_keeps_phase_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(legacy_build, "build_kol_entities", lambda *_args: calls.append("kol"))
    monkeypatch.setattr(legacy_build, "build_cooperations", lambda *_args: calls.append("cooperation"))
    monkeypatch.setattr(legacy_build, "build_risks", lambda *_args: calls.append("risk"))
    monkeypatch.setattr(legacy_build, "build_launches", lambda *_args: calls.append("launch"))
    monkeypatch.setattr(legacy_build, "build_product_costs", lambda *_args: calls.append("cost"))
    monkeypatch.setattr(legacy_build, "write_snapshot", lambda *_args: calls.append("snapshot") or "snap")

    result = legacy_build.build_all(None, [], "batch", 1, "scope", {}, {}, {})

    assert result == "snap"
    assert calls == ["kol", "cooperation", "risk", "launch", "cost", "snapshot"]


def test_legacy_builder_keeps_empty_and_transaction_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "ensure_memory_schema", lambda: None)
    monkeypatch.setattr(legacy, "_fetch_batch", lambda _uid: {"id": 3})
    empty = _Connection()
    monkeypatch.setattr(legacy, "get_conn", lambda: empty)
    with pytest.raises(RuntimeError, match="no active P2D committed refs"):
        legacy.build_memory_from_legacy_batch("empty")
    assert not any(event in {"commit", "rollback"} for event in empty.events)

    active = _Connection([{"legacy_entity_id": 1}])
    monkeypatch.setattr(legacy, "get_conn", lambda: active)
    monkeypatch.setattr(
        legacy_build,
        "build_all",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("phase failed")),
    )
    with pytest.raises(RuntimeError, match="phase failed"):
        legacy.build_memory_from_legacy_batch("active")
    assert active.events[-1] == "rollback"
    assert "commit" not in active.events
