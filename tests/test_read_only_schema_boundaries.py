from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domains.costs import budget_guard
from app.domains.costs import budget_readonly
from app.domains.data_quality import checks as data_quality_checks
from app.domains.data_quality import common as data_quality_common
from app.domains.memory import feedback as memory_feedback
from app.domains.reports import weekly_generator


ROOT = Path(__file__).resolve().parents[1]


def _unexpected_connection():
    raise AssertionError("PostgreSQL runtime schema helpers must not open a connection")


def _unexpected_schema_bootstrap() -> None:
    raise AssertionError("read path must not bootstrap schema")


class _EmptyCursor:
    def fetchall(self) -> list[Any]:
        return []

    def fetchone(self) -> None:
        return None


class _ReadOnlyConnection:
    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _EmptyCursor:
        normalized = " ".join(sql.split()).upper()
        assert normalized.startswith(("SELECT ", "WITH "))
        return _EmptyCursor()


class _BudgetCursor:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any]:
        return self._row


class _ReadOnlyBudgetConnection:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row
        self.statements: list[str] = []

    def execute(
        self,
        sql: str,
        _params: tuple[Any, ...] = (),
    ) -> _BudgetCursor:
        normalized = " ".join(sql.split()).upper()
        assert normalized.startswith("SELECT ")
        self.statements.append(normalized)
        return _BudgetCursor(self._row)

    def commit(self) -> None:
        raise AssertionError("read-only budget status must not commit")


def test_data_quality_schema_helper_is_noop_for_postgres(monkeypatch) -> None:
    monkeypatch.setattr(data_quality_common, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(data_quality_common, "get_conn", _unexpected_connection)

    data_quality_common.ensure_data_quality_schema()


def test_weekly_get_and_list_never_bootstrap_schema_under_release_fence(
    monkeypatch,
) -> None:
    """B 线口径:发布验证围栏激活时,周报读路径不得触发兼容 DDL。"""
    connection = _ReadOnlyConnection()
    monkeypatch.setattr(weekly_generator, "release_validation_active", lambda: True)
    monkeypatch.setattr(weekly_generator, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(weekly_generator, "get_conn", lambda: connection)
    monkeypatch.setattr(
        weekly_generator,
        "ensure_vkpi_weekly_reports_schema",
        _unexpected_schema_bootstrap,
    )

    assert weekly_generator.get_report(
        0, staff={"id": 1, "role": "admin"}
    )["status"] == "not_found"
    assert weekly_generator.list_reports(
        staff={"id": 1, "role": "admin"}
    ) == {"count": 0, "reports": []}


def test_data_quality_list_never_bootstraps_action_schema(monkeypatch) -> None:
    connection = _ReadOnlyConnection()
    monkeypatch.setattr(data_quality_checks, "get_conn", lambda: connection)
    monkeypatch.setattr(data_quality_checks, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(
        data_quality_checks, "ensure_vkpi_lineage_schema", lambda: None
    )
    monkeypatch.setattr(
        data_quality_checks, "ensure_vkpi_reconciliation_schema", lambda: None
    )
    # list_issues 不再引用 action 表的 bootstrap helper(迁移 274 接管 PG 建表)。
    assert not hasattr(data_quality_checks, "ensure_data_quality_schema")

    result = data_quality_checks.list_issues(
        limit=1, staff={"id": 1, "role": "admin"}
    )

    assert isinstance(result["issues"], list)


def test_memory_readiness_never_runs_migrations_under_release_fence(
    monkeypatch,
) -> None:
    """B 线口径:发布验证围栏激活时,Memory readiness 只 SELECT、不跑 bootstrap。"""
    connection = _ReadOnlyConnection()
    monkeypatch.setattr(memory_feedback, "release_validation_active", lambda: True)
    monkeypatch.setattr(memory_feedback, "get_conn", lambda: connection)
    monkeypatch.setattr(
        memory_feedback, "ensure_memory_schema", _unexpected_schema_bootstrap
    )
    monkeypatch.setattr(memory_feedback, "_market_signal_counts", lambda: {})
    monkeypatch.setattr(memory_feedback, "_table_exists", lambda _name: False)

    result = memory_feedback.readiness()

    assert result["status"] == "blocked"
    assert result["provider_calls_allowed"] is False


def test_budget_status_readonly_projects_expired_window_without_writing(
    monkeypatch,
) -> None:
    connection = _ReadOnlyBudgetConnection(
        {
            "scope": "cron:p4_recommendations_daily",
            "cap_usd": 4.0,
            "current_spend": 4.0,
            "warning_at": 0.8,
            "hard_stop_at": 1.0,
            "reset_at": "2000-01-01T00:00:00Z",
            "fallback_action": "fallback_to_rule_v0",
            "metadata_json": "{}",
        }
    )
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    result = budget_readonly.get_budget_status_readonly(
        "cron:p4_recommendations_daily",
        estimated_cost=0.0,
    )

    assert result["allowed"] is True
    assert result["current_spend"] == 0.0
    assert result["read_only"] is True
    assert result["window_roll_pending"] is True
    assert len(connection.statements) == 1


def test_data_quality_postgres_schema_is_migration_owned() -> None:
    migration = (
        ROOT / "migrations" / "274_vkpi_data_quality_actions.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS vkpi_data_quality_actions" in migration
    assert "idx_vkpi_data_quality_actions_issue" in migration
