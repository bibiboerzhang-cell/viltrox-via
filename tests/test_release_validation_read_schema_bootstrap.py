from __future__ import annotations

import sqlite3
from typing import Any

from app.domains.media import cache_core
from app.domains.memory import feedback as memory_feedback
from app.domains.projects import launch_assembly
from app.domains.reports import weekly_generator


class _Cursor:
    def __init__(self, sql: str) -> None:
        self.sql = sql

    def fetchall(self) -> list[Any]:
        return []

    def fetchone(self) -> dict[str, int] | None:
        if "sqlite_master" in self.sql:
            return None
        if "COUNT(*) AS n" in self.sql:
            return {"n": 0}
        return None


class _ReadOnlyConnection:
    def execute(self, sql: str, _params: Any = None) -> _Cursor:
        normalized = " ".join(str(sql).split())
        if normalized.upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")):
            raise AssertionError(f"read surface attempted SQL mutation: {normalized[:24]}")
        return _Cursor(normalized)


def _ddl_forbidden() -> None:
    raise AssertionError("release-fenced read attempted schema bootstrap")


def test_fenced_weekly_reads_skip_compatibility_ddl(monkeypatch) -> None:
    monkeypatch.setattr(weekly_generator, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        weekly_generator,
        "ensure_vkpi_weekly_reports_schema",
        _ddl_forbidden,
    )
    monkeypatch.setattr(weekly_generator, "get_conn", _ReadOnlyConnection)
    staff = {"id": 1, "role": "admin"}

    assert weekly_generator.list_reports(staff=staff) == {"count": 0, "reports": []}
    assert weekly_generator.get_report(0, staff=staff)["status"] == "not_found"


def test_live_weekly_read_keeps_schema_compatibility_bootstrap(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(weekly_generator, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        weekly_generator,
        "ensure_vkpi_weekly_reports_schema",
        lambda: calls.append("ensure"),
    )
    monkeypatch.setattr(weekly_generator, "get_conn", _ReadOnlyConnection)

    staff = {"id": 1, "role": "admin"}
    weekly_generator.get_report(0, staff=staff)
    weekly_generator.list_reports(staff=staff)

    assert calls == ["ensure", "ensure"]


def test_memory_readiness_always_skips_migration_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(memory_feedback, "ensure_memory_schema", _ddl_forbidden)
    monkeypatch.setattr(memory_feedback, "get_conn", _ReadOnlyConnection)
    monkeypatch.setattr(memory_feedback, "_market_signal_counts", lambda: {})
    monkeypatch.setattr(memory_feedback, "_table_exists", lambda _name: False)

    result = memory_feedback.readiness()

    assert result["status"] == "blocked"
    assert result["provider_calls_allowed"] is False


def test_live_memory_readiness_is_also_pure_read(monkeypatch) -> None:
    monkeypatch.setattr(memory_feedback, "ensure_memory_schema", _ddl_forbidden)
    monkeypatch.setattr(memory_feedback, "get_conn", _ReadOnlyConnection)
    monkeypatch.setattr(memory_feedback, "_market_signal_counts", lambda: {})
    monkeypatch.setattr(memory_feedback, "_table_exists", lambda _name: False)

    result = memory_feedback.readiness()

    assert result["status"] == "blocked"
    assert result["provider_calls_allowed"] is False


def test_memory_readiness_blocks_cleanly_when_schema_is_absent(monkeypatch) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(memory_feedback, "get_conn", lambda: connection)
    monkeypatch.setattr(memory_feedback, "_table_exists", lambda _name: False)
    monkeypatch.setattr(
        memory_feedback,
        "_market_signal_counts",
        lambda: (_ for _ in ()).throw(AssertionError("missing schema must short-circuit")),
    )

    result = memory_feedback.readiness()

    assert result["status"] == "blocked"
    assert result["blockers"][0]["key"] == "memory_schema"
    assert len(result["blockers"][0]["missing_tables"]) == 4


def test_launch_candidate_readiness_has_no_ddl_provider_or_persistence(monkeypatch) -> None:
    from app.domains.recommendations import new_launch_match
    from app.domains.recommendations import new_launch_match_helpers

    connection = _ReadOnlyConnection()
    monkeypatch.setattr(memory_feedback, "ensure_memory_schema", _ddl_forbidden)
    monkeypatch.setattr(memory_feedback, "get_conn", lambda: connection)
    monkeypatch.setattr(memory_feedback, "_market_signal_counts", lambda: {})
    monkeypatch.setattr(memory_feedback, "_table_exists", lambda _name: False)
    monkeypatch.setattr(
        new_launch_match_helpers,
        "resolve_target_family",
        lambda _query: (
            {"entity_uid": "family_test", "display_name": "Test Family"},
            "Test Family",
            "",
        ),
    )

    def _unexpected_provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("launch candidate dry-run attempted an LLM provider call")

    def _unexpected_persist(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("launch candidate dry-run attempted persistence")

    monkeypatch.setattr(new_launch_match.llm_production, "generate_json", _unexpected_provider)
    monkeypatch.setattr(new_launch_match, "_persist_preview_run", _unexpected_persist)
    real_build = new_launch_match.build_new_launch_match_preview
    observed: list[dict[str, Any]] = []

    def _observed_build(**kwargs: Any) -> dict[str, Any]:
        observed.append(dict(kwargs))
        return real_build(**kwargs)

    monkeypatch.setattr(new_launch_match, "build_new_launch_match_preview", _observed_build)

    result = launch_assembly._candidate_pool("AF 35/1.8", 12)

    assert result["status"] == "unavailable"
    assert "memory readiness blocked" in result["reason"]
    assert observed == [
        {
            "product_query": "Test Family",
            "limit": 12,
            "with_llm_reasons": False,
            "persist_run": False,
        }
    ]


def test_fenced_media_cache_reads_skip_compatibility_ddl(monkeypatch) -> None:
    monkeypatch.setattr(cache_core, "release_validation_active", lambda: True)
    monkeypatch.setattr(cache_core, "ensure_vkpi_media_cache_schema", _ddl_forbidden)
    monkeypatch.setattr(cache_core, "get_conn", _ReadOnlyConnection)

    assert cache_core._cached_asset_url_by_digest("video", "a" * 64) == ""
    assert cache_core._cached_asset_url_for_item("instagram", "DX8prCJOe6V") == ""


def test_live_media_cache_reads_keep_schema_compatibility_bootstrap(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cache_core, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        cache_core,
        "ensure_vkpi_media_cache_schema",
        lambda: calls.append("ensure"),
    )
    monkeypatch.setattr(cache_core, "get_conn", _ReadOnlyConnection)

    assert cache_core._cached_asset_url_by_digest("video", "a" * 64) == ""
    assert cache_core._cached_asset_url_for_item("instagram", "DX8prCJOe6V") == ""
    assert calls == ["ensure", "ensure"]


def test_launch_assembly_forecast_is_read_only(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def _forecast_for_kol(kol_pool_id: int, **kwargs: Any) -> dict[str, Any]:
        calls.append({"kol_pool_id": kol_pool_id, **kwargs})
        return {"status": "ready", "expected_views_p50": 1234}

    from app.domains.kol import performance_forecast

    monkeypatch.setattr(performance_forecast, "forecast_for_kol", _forecast_for_kol)

    result = launch_assembly._forecast_block(
        [{"kol_pool_id": 42, "handle": "creator", "display_name": "Creator"}],
        "AF 26/1.8",
    )

    assert result["status"] == "ready"
    assert calls == [
        {
            "kol_pool_id": 42,
            "sku": "AF 26/1.8",
            "context": "launchpad",
            "dry_run": True,
        }
    ]
