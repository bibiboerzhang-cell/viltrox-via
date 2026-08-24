from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.api.routers import vkpi_budgets
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


def test_memory_readiness_never_runs_migrations(
    monkeypatch,
) -> None:
    """Memory readiness 在任意运行态都只 SELECT、不跑 schema bootstrap。"""
    connection = _ReadOnlyConnection()
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


def test_budget_status_readonly_treats_missing_legacy_sqlite_table_as_not_configured(
    monkeypatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    schema_before = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    result = budget_readonly.get_budget_status_readonly(
        "cron:p4_recommendations_daily",
        estimated_cost=0.0,
    )

    schema_after = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    assert result == {
        "scope": "cron:p4_recommendations_daily",
        "status": "not_configured",
        "reason": "budget_registry_not_migrated",
        "configured": False,
        "allowed": True,
        "provider_calls_allowed": False,
        "estimated_cost_usd": 0.0,
        "read_only": True,
        "projected": False,
        "projection_status": "not_configured",
        "projection_warnings": [],
        "window_roll_pending": False,
    }
    assert schema_after == schema_before == []
    assert connection.total_changes == 0


def test_budget_status_readonly_missing_table_blocks_positive_cost(
    monkeypatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    result = budget_readonly.get_budget_status_readonly(
        "feature:recommendation",
        estimated_cost=0.01,
    )

    assert result["status"] == "not_configured"
    assert result["configured"] is False
    assert result["allowed"] is False
    assert result["provider_calls_allowed"] is False
    assert connection.total_changes == 0


def test_budget_status_readonly_does_not_relax_configured_hard_stop(
    monkeypatch,
) -> None:
    connection = _ReadOnlyBudgetConnection(
        {
            "scope": "feature:recommendation",
            "cap_usd": 4.0,
            "current_spend": 4.0,
            "warning_at": 0.8,
            "hard_stop_at": 1.0,
            "reset_at": "2999-01-01T00:00:00Z",
            "fallback_action": "fallback_to_rule_v0",
            "metadata_json": "{}",
        }
    )
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    result = budget_readonly.get_budget_status_readonly(
        "feature:recommendation",
        estimated_cost=0.0,
    )

    assert result["configured"] is True
    assert result["allowed"] is False
    assert result["hard_stopped"] is True
    assert result["current_spend"] == 4.0
    assert result["window_roll_pending"] is False


def test_new_launch_zero_cost_preview_crosses_missing_legacy_budget_table(
    monkeypatch,
) -> None:
    from app.domains.kol import discovery_filters as _discovery_filters  # noqa: F401
    from app.domains.recommendations import new_launch_match

    class _ReachedRecommendationRead(RuntimeError):
        pass

    connection = sqlite3.connect(":memory:")
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)
    monkeypatch.setattr(
        new_launch_match.memory,
        "readiness",
        lambda: {
            "status": "ready_for_p4_dry_run",
            "provider_calls_allowed": False,
        },
    )
    monkeypatch.setattr(
        new_launch_match,
        "_select_target_family",
        lambda _query: (_ for _ in ()).throw(_ReachedRecommendationRead()),
    )

    with pytest.raises(_ReachedRecommendationRead):
        new_launch_match.build_new_launch_match_preview(
            product_query="AF 35mm",
            with_llm_reasons=False,
        )

    assert connection.total_changes == 0


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("no such table: unrelated_table"),
        RuntimeError('relation "vkpi_provider_budget_caps" does not exist'),
    ],
)
def test_budget_status_readonly_does_not_mask_other_database_errors(
    monkeypatch,
    error: BaseException,
) -> None:
    class _FailingConnection:
        def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> Any:
            raise error

    monkeypatch.setattr(
        budget_readonly,
        "get_conn",
        lambda: _FailingConnection(),
    )

    with pytest.raises(type(error), match=str(error)):
        budget_readonly.get_budget_status_readonly(
            "cron:p4_recommendations_daily",
            estimated_cost=0.0,
        )


def _create_budget_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE vkpi_provider_budget_caps (
            scope TEXT PRIMARY KEY,
            cap_usd,
            current_spend,
            warning_at,
            hard_stop_at,
            reset_at TEXT,
            fallback_action TEXT,
            metadata_json TEXT
        )
        """
    )


def test_budget_list_and_api_project_expired_windows_without_writing(
    monkeypatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    _create_budget_table(connection)
    connection.executemany(
        """
        INSERT INTO vkpi_provider_budget_caps (
            scope, cap_usd, current_spend, warning_at, hard_stop_at,
            reset_at, fallback_action, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "cron:expired_probe",
                2.0,
                2.0,
                0.8,
                1.0,
                "2000-01-01T00:00:00Z",
                "fallback_to_cache",
                "{}",
            ),
            (
                "provider:future_probe",
                10.0,
                3.0,
                0.8,
                1.0,
                "2999-01-01T00:00:00Z",
                "deny",
                "{}",
            ),
        ],
    )
    connection.commit()
    changes_before = connection.total_changes
    source_before = [
        tuple(row)
        for row in connection.execute(
            "SELECT scope, current_spend, reset_at "
            "FROM vkpi_provider_budget_caps ORDER BY scope"
        ).fetchall()
    ]
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    single = budget_readonly.get_budget_status_readonly("cron:expired_probe")
    listed = budget_readonly.list_budget_status_readonly()
    api_payload = vkpi_budgets.budgets(staff={"id": 1, "role": "admin"})

    by_scope = {row["scope"]: row for row in listed["budgets"]}
    assert by_scope["cron:expired_probe"] == single
    assert single["current_spend"] == 0.0
    assert single["hard_stopped"] is False
    assert single["projected"] is True
    assert single["read_only"] is True
    assert single["window_roll_pending"] is True
    assert single["reset_at"] != "2000-01-01T00:00:00Z"
    assert by_scope["provider:future_probe"]["current_spend"] == 3.0
    assert by_scope["provider:future_probe"]["projected"] is False
    assert listed["summary"]["current_spend_usd"] == 3.0
    assert listed["summary"]["projected_windows"] == 1
    assert api_payload == listed

    connection.set_trace_callback(None)
    source_after = [
        tuple(row)
        for row in connection.execute(
            "SELECT scope, current_spend, reset_at "
            "FROM vkpi_provider_budget_caps ORDER BY scope"
        ).fetchall()
    ]
    assert source_after == source_before
    assert connection.total_changes == changes_before
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT ") for statement in statements)


def test_budget_list_missing_table_is_honest_and_read_only(monkeypatch) -> None:
    connection = sqlite3.connect(":memory:")
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    result = budget_readonly.list_budget_status_readonly()

    assert result["status"] == "not_configured"
    assert result["reason"] == "budget_registry_not_migrated"
    assert result["configured"] is False
    assert result["budgets"] == []
    assert result["read_only"] is True
    assert connection.total_changes == 0


def test_budget_list_does_not_mask_non_schema_database_errors(monkeypatch) -> None:
    class _LockedConnection:
        def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> Any:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(budget_readonly, "get_conn", lambda: _LockedConnection())

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        budget_readonly.list_budget_status_readonly()


def test_advisor_scope_preflight_projects_expired_window_without_any_write(
    monkeypatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    _create_budget_table(connection)
    connection.execute(
        """
        INSERT INTO vkpi_provider_budget_caps (
            scope, cap_usd, current_spend, warning_at, hard_stop_at,
            reset_at, fallback_action, metadata_json
        ) VALUES ('cron:marketing_advisor', 2, 2, 0.8, 1,
                  '2000-01-01T00:00:00Z', 'fallback_to_evidence_only', '{}')
        """
    )
    connection.commit()
    changes_before = connection.total_changes
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)
    monkeypatch.setattr(
        budget_guard, "ensure_budget_schema", _unexpected_schema_bootstrap
    )

    plan = budget_guard.check_budget_scopes(
        ["cron:marketing_advisor"], 0.01, require_configured=True
    )

    check = plan["checks"][0]
    assert plan["allowed"] is True
    assert check["read_only"] is True
    assert check["projected"] is True
    assert check["current_spend"] == 0.0
    assert connection.total_changes == changes_before
    assert statements and all(
        statement.lstrip().upper().startswith("SELECT ") for statement in statements
    )


def test_advisor_scope_preflight_missing_registry_does_not_bootstrap_sqlite(
    monkeypatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)
    monkeypatch.setattr(
        budget_guard, "ensure_budget_schema", _unexpected_schema_bootstrap
    )

    plan = budget_guard.check_budget_scopes(
        ["cron:marketing_advisor"], 0.01, require_configured=True
    )

    assert plan["allowed"] is False
    assert plan["checks"][0]["status"] == "not_configured"
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall() == []
    assert connection.total_changes == 0


@pytest.mark.parametrize(
    ("metadata_json", "warning_at", "hard_stop_at", "expected_warning"),
    [
        ("{malformed-json", 0.8, 1.0, "invalid_metadata_json"),
        ("[]", 0.8, 1.0, "invalid_metadata_json"),
        ("{}", 0.95, 0.80, "invalid_threshold_order"),
    ],
)
def test_admin_budget_status_marks_bad_metadata_and_inverted_thresholds_invalid(
    monkeypatch,
    metadata_json: str,
    warning_at: float,
    hard_stop_at: float,
    expected_warning: str,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    _create_budget_table(connection)
    connection.execute(
        """
        INSERT INTO vkpi_provider_budget_caps (
            scope, cap_usd, current_spend, warning_at, hard_stop_at,
            reset_at, fallback_action, metadata_json
        ) VALUES ('cron:invalid_admin_probe', 2, 0, ?, ?,
                  '2999-01-01T00:00:00Z', 'deny', ?)
        """,
        (warning_at, hard_stop_at, metadata_json),
    )
    connection.commit()
    changes_before = connection.total_changes
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    result = vkpi_budgets.budgets(staff={"id": 1, "role": "admin"})
    plan = budget_guard.check_budget_scopes(
        ["cron:invalid_admin_probe"], 0.01, require_configured=True
    )

    row = result["budgets"][0]
    assert result["status"] == "degraded"
    assert result["summary"]["invalid_rows"] == 1
    assert row["status"] == "invalid_data"
    assert row["reason"] == "budget_row_invalid"
    assert row["allowed"] is False
    assert row["provider_calls_allowed"] is False
    assert expected_warning in row["projection_warnings"]
    assert plan["allowed"] is False
    assert plan["checks"][0]["status"] == "invalid_data"
    assert connection.total_changes == changes_before


def test_budget_list_marks_malformed_rows_and_stays_fail_closed(
    monkeypatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    _create_budget_table(connection)
    connection.execute(
        """
        INSERT INTO vkpi_provider_budget_caps (
            scope, cap_usd, current_spend, warning_at, hard_stop_at,
            reset_at, fallback_action, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cron:malformed_probe",
            "not-a-cap",
            "not-a-spend",
            0.8,
            1.0,
            "not-a-timestamp",
            "deny",
            "{}",
        ),
    )
    connection.commit()
    changes_before = connection.total_changes
    monkeypatch.setattr(budget_readonly, "get_conn", lambda: connection)

    result = budget_readonly.list_budget_status_readonly()

    row = result["budgets"][0]
    assert result["status"] == "degraded"
    assert result["summary"]["invalid_rows"] == 1
    assert row["status"] == "invalid_data"
    assert row["reason"] == "budget_row_invalid"
    assert row["allowed"] is False
    assert row["projection_status"] == "invalid_source"
    assert set(row["projection_warnings"]) == {
        "invalid_cap_usd",
        "invalid_current_spend",
        "invalid_reset_at",
    }
    assert connection.total_changes == changes_before


def test_data_quality_postgres_schema_is_migration_owned() -> None:
    migration = (
        ROOT / "migrations" / "274_vkpi_data_quality_actions.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS vkpi_data_quality_actions" in migration
    assert "idx_vkpi_data_quality_actions_issue" in migration
