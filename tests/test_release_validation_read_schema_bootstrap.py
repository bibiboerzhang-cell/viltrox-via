from __future__ import annotations

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


def test_fenced_memory_readiness_skips_migration_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(memory_feedback, "release_validation_active", lambda: True)
    monkeypatch.setattr(memory_feedback, "ensure_memory_schema", _ddl_forbidden)
    monkeypatch.setattr(memory_feedback, "get_conn", _ReadOnlyConnection)
    monkeypatch.setattr(memory_feedback, "_market_signal_counts", lambda: {})
    monkeypatch.setattr(memory_feedback, "_table_exists", lambda _name: False)

    result = memory_feedback.readiness()

    assert result["status"] == "blocked"
    assert result["provider_calls_allowed"] is False


def test_live_memory_readiness_keeps_schema_compatibility_bootstrap(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(memory_feedback, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        memory_feedback,
        "ensure_memory_schema",
        lambda: calls.append("ensure"),
    )
    monkeypatch.setattr(memory_feedback, "get_conn", _ReadOnlyConnection)
    monkeypatch.setattr(memory_feedback, "_market_signal_counts", lambda: {})
    monkeypatch.setattr(memory_feedback, "_table_exists", lambda _name: False)

    memory_feedback.readiness()

    assert calls == ["ensure"]


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
