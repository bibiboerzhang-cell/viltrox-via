"""Read-path side-effect regressions for the release-validation fence."""
from __future__ import annotations

import pytest

from app.api.routers import vkpi_attribution_metrics
from app.domains.audit import service as audit_service
from app.domains.costs import ledger as costs_ledger
from app.domains.reports import export_jobs


def test_product_cost_capture_read_never_bootstraps_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReadOnlyConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str, _params: object = ()):
            normalized = " ".join(statement.split()).upper()
            self.statements.append(normalized)
            if not normalized.startswith("SELECT "):
                raise AssertionError(f"write-shaped SQL ran in captured GET: {normalized}")
            return self

        @staticmethod
        def fetchall() -> list[object]:
            return []

    conn = ReadOnlyConnection()
    monkeypatch.setattr(costs_ledger, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(costs_ledger, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(costs_ledger, "get_conn", lambda: conn)

    result = costs_ledger.list_product_costs(limit=30, include_inactive=False)

    assert result == {"product_costs": []}
    assert len(conn.statements) == 1
    assert conn.statements[0].startswith("SELECT ")


def test_kpi_ledger_capture_read_skips_audit_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"entries": [{"id": 7}]}
    monkeypatch.setattr(
        vkpi_attribution_metrics.kpi_ledger,
        "list_entries",
        lambda **_kwargs: expected,
    )
    monkeypatch.setattr(audit_service, "_release_validation_fenced", lambda: True)

    def fail_db_access(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("audit touched the database in captured GET")

    monkeypatch.setattr(audit_service, "ensure_vkpi_audit_schema", fail_db_access)
    monkeypatch.setattr(audit_service, "get_conn", fail_db_access)

    result = vkpi_attribution_metrics.list_kpi_ledger(
        staff_id=None,
        limit=30,
        staff={"id": 5},
    )

    assert result is expected


def test_all_audit_writers_are_inert_during_release_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_service, "_release_validation_fenced", lambda: True)

    def fail_db_access(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fenced audit writer touched the database")

    monkeypatch.setattr(audit_service, "ensure_vkpi_audit_schema", fail_db_access)
    monkeypatch.setattr(audit_service, "get_conn", fail_db_access)

    class ForbiddenConnection:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            fail_db_access()

        def commit(self) -> None:
            fail_db_access()

    results = [
        audit_service.log_sensitive_access(
            staff_id=5,
            action_type="view",
            resource_type="test",
        ),
        audit_service.log_export(
            staff_id=5,
            export_kind="test",
            export_target="test",
        ),
        audit_service.log_settings_change(
            staff_id=5,
            change_type="test",
            setting_key="test",
        ),
        audit_service.log_business_event(
            staff_id=5,
            action_type="test",
            target_type="test",
        ),
    ]

    assert results == [
        {"skipped": True, "reason": "release_validation_fenced"}
    ] * 4

    with pytest.raises(AssertionError, match="fenced audit writer touched"):
        audit_service.log_business_event(
            staff_id=5,
            action_type="transactional_test",
            target_type="test",
            conn=ForbiddenConnection(),
            commit=False,
            ensure_schema=False,
        )


def test_fenced_export_reports_skipped_audit_truthfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class QueryResult:
        rowcount = 1

        @staticmethod
        def fetchone() -> dict[str, int]:
            return {"id": 17}

    class ExportConnection:
        def execute(self, *_args: object, **_kwargs: object) -> QueryResult:
            return QueryResult()

        @staticmethod
        def commit() -> None:
            return None

        @staticmethod
        def rollback() -> None:
            return None

    monkeypatch.setattr(export_jobs, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(export_jobs, "_assert_export_create_access", lambda *_args: None)
    monkeypatch.setattr(export_jobs, "get_conn", lambda: ExportConnection())
    monkeypatch.setattr(
        export_jobs.scope,
        "assert_legacy_default_organization",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(export_jobs, "_rows", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(export_jobs, "_uid", lambda _prefix: "export-fenced-test")
    monkeypatch.setattr(
        export_jobs,
        "store_bytes",
        lambda *_args, **_kwargs: {
            "file_path": "/tmp/export-fenced-test.csv",
            "file_size_bytes": 12,
            "sha256_hex": "a" * 64,
        },
    )
    monkeypatch.setattr(
        export_jobs.audit,
        "log_export",
        lambda **_kwargs: {
            "skipped": True,
            "reason": "release_validation_fenced",
        },
    )

    result = export_jobs.create_export(
        export_format="csv",
        payload={"export_type": "projects"},
        staff={"id": 5},
    )

    assert result["status"] == "ready"
    assert result["audit_status"] == "skipped"
    assert result["audit_reason"] == "release_validation_fenced"
