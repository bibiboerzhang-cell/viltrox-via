from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.access import scope
from app.domains.reports import export_jobs
from app.domains.reports import pdf_renderer
from app.domains.reports import reports


class _Result:
    def __init__(self, *, row: dict[str, Any] | None = None, rows: list[dict[str, Any]] | None = None):
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _ReportConn:
    def __init__(self, row: dict[str, Any]):
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        self.calls.append((compact, tuple(params)))
        if compact.startswith("SELECT * FROM vkpi_report_runs"):
            return _Result(row=dict(self.row))
        if "SET status='archived'" in compact:
            self.row = {**self.row, "status": "archived", "metadata_json": params[0]}
            return _Result()
        if "SET status=?" in compact and "status='archived'" in compact:
            self.row = {**self.row, "status": params[0], "metadata_json": params[1]}
            return _Result()
        raise AssertionError(f"unexpected SQL: {compact}")

    def commit(self) -> None:
        self.commits += 1


def _report_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 44,
        "report_uid": "weekly-44",
        "report_type": "weekly",
        "scope_type": "staff",
        "scope_id": 7,
        "triggered_by_staff_id": 7,
        "status": "ready",
        "metadata_json": "{}",
    }
    row.update(overrides)
    return row


def test_export_creation_requires_write_and_full_pool_requires_management() -> None:
    viewer = {"id": 7, "role": "viewer"}
    employee = {"id": 7, "role": "employee"}
    manager = {"id": 8, "role": "manager"}

    with pytest.raises(scope.ScopeDenied, match="write permission"):
        export_jobs._assert_export_create_access("favorites", viewer)
    export_jobs._assert_export_create_access("favorites", employee)
    with pytest.raises(scope.ScopeDenied, match="management scope"):
        export_jobs._assert_export_create_access("vkpi_kol_pool", employee)
    export_jobs._assert_export_create_access("vkpi_kol_pool", manager)


def test_export_payload_and_rows_fail_closed_on_sensitive_fields() -> None:
    export_type, filters = export_jobs._normalize_export_payload(
        {
            "reportType": "project_kols",
            "projectId": "42",
            "startDate": "2026-07-01",
            "password": "must-not-persist",
        }
    )
    sanitized = export_jobs._strip_sensitive_fields(
        {
            "id": 1,
            "staff_email": "person@example.com",
            "contact_phone": "+1-555-0100",
            "order_id": 99,
            "tracking_number": "secret-shipment",
            "access_token": "secret-token",
        }
    )

    assert export_type == "project_kols"
    assert filters == {"report_type": "project_kols", "project_id": 42, "date_from": "2026-07-01"}
    assert sanitized == {"id": 1}
    assert export_jobs._pool_rows_gated([{"handle": "safe", "email": "hidden@example.com"}], {"role": "manager"}) == [
        {"handle": "safe"}
    ]


def test_kols_export_query_uses_explicit_safe_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    class _CaptureConn:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            del params
            self.sql = " ".join(sql.split())
            return _Result(rows=[])

    conn = _CaptureConn()
    monkeypatch.setattr(export_jobs, "get_conn", lambda: conn)

    assert export_jobs._rows("kols", {}, staff={"id": 7, "role": "employee"}) == []
    assert "SELECT k.*" not in conn.sql
    assert "contact_email" not in conn.sql
    assert "contact_phone" not in conn.sql
    assert "contact_raw_json" not in conn.sql


def test_create_export_sanitizes_persisted_filters_and_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExportConn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            self.calls.append((compact, tuple(params)))
            if compact.startswith("SELECT id FROM vkpi_export_jobs"):
                return _Result(row={"id": 77})
            return _Result()

        def commit(self) -> None:
            return None

    conn = _ExportConn()
    stored: dict[str, Any] = {}
    monkeypatch.setattr(export_jobs, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(export_jobs, "get_conn", lambda: conn)
    monkeypatch.setattr(
        export_jobs,
        "_rows",
        lambda *_args, **_kwargs: [
            {
                "id": 1,
                "amount_cents": 250,
                "staff_email": "person@example.com",
                "order_id": 99,
                "access_token": "secret-token",
            }
        ],
    )

    def _store(content: bytes, *, filename: str) -> dict[str, Any]:
        stored.update({"content": content, "filename": filename})
        return {"file_path": "/safe/export.csv", "file_size_bytes": len(content), "sha256_hex": "abc"}

    monkeypatch.setattr(export_jobs, "store_bytes", _store)
    monkeypatch.setattr(export_jobs.audit, "log_export", lambda **_kwargs: None)

    result = export_jobs.create_export(
        export_format="csv",
        payload={"reportType": "finance", "staffId": 7, "access_token": "must-not-persist"},
        staff={"id": 7, "role": "employee", "organization_id": 1},
    )

    insert_params = next(params for sql, params in conn.calls if sql.startswith("INSERT INTO vkpi_export_jobs"))
    persisted_filters = json.loads(str(insert_params[4]))
    csv_text = stored["content"].decode("utf-8-sig")
    assert persisted_filters == {"report_type": "finance", "staff_id": 7}
    assert "access_token" not in csv_text
    assert "staff_email" not in csv_text
    assert "order_id" not in csv_text
    assert "amount_usd" in csv_text
    assert result["download_url"] == "/api/admin/vkpi/exports/77/download"


def test_report_archive_and_restore_never_delete_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _ReportConn(_report_row())
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(reports, "get_conn", lambda: conn)

    archived = reports.archive_report(
        44,
        staff={"id": 7, "role": "employee", "organization_id": 1},
        reason="cleanup",
    )
    restored = reports.restore_report(
        44,
        staff={"id": 7, "role": "employee", "organization_id": 1},
    )

    assert archived["status"] == "archived"
    assert archived["previous_status"] == "ready"
    assert restored == {"status": "restored", "report_run_id": 44, "report_status": "ready"}
    assert conn.row["status"] == "ready"
    assert conn.commits == 2
    assert not any("DELETE" in sql for sql, _params in conn.calls)
    metadata = json.loads(str(conn.row["metadata_json"]))
    assert metadata["_archive_history"][0]["reason"] == "cleanup"


def test_truth_invalidated_report_cannot_be_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _ReportConn(
        _report_row(
            status="archived",
            truth_invalidated_at="2026-07-14T00:00:00Z",
            truth_invalidation_reason="pre_native_shopify_financial_truth",
            truth_invalidation_migration=256,
            truth_restorable=False,
        )
    )
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(reports, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="truth-invalidated"):
        reports.restore_report(
            44,
            staff={"id": 7, "role": "employee", "organization_id": 1},
        )

    assert conn.commits == 0
    assert conn.row["status"] == "archived"


def test_active_or_out_of_scope_report_cannot_be_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    active = _ReportConn(_report_row(status="rendering"))
    monkeypatch.setattr(reports, "get_conn", lambda: active)
    with pytest.raises(ValueError, match="active report"):
        reports.archive_report(
            44,
            staff={"id": 7, "role": "employee", "organization_id": 1},
        )

    foreign = _ReportConn(_report_row(scope_id=9, triggered_by_staff_id=9))
    monkeypatch.setattr(reports, "get_conn", lambda: foreign)
    with pytest.raises(scope.ScopeDenied):
        reports.archive_report(
            44,
            staff={"id": 7, "role": "employee", "organization_id": 1},
        )


def test_report_scope_recipient_can_read_manager_generated_report() -> None:
    reports._assert_report_access(
        {"triggered_by_staff_id": 99, "scope_id": 7},
        {"id": 7, "role": "employee"},
    )
    with pytest.raises(scope.ScopeDenied):
        reports._assert_report_access(
            {"triggered_by_staff_id": 99, "scope_id": 8},
            {"id": 7, "role": "employee"},
        )


def test_report_history_selects_archive_bucket_and_hides_internal_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _HistoryConn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            del params
            compact = " ".join(sql.split())
            self.sql.append(compact)
            status = "archived" if "status='archived'" in compact else "ready"
            metadata = {
                "report_type": "weekly",
                "period": "weekly",
                "period_days": 7,
                "date": "2026-07-08",
                "date_from": "2026-07-02",
                "date_to": "2026-07-08",
                "language": "en",
                "sections": ["kpiOverview", "summary"],
                "format": "markdown",
                "scope": "self",
                "_report_contract": {"schema_version": "report.v1", "data_status": "real"},
                "_archive": {
                    "archived_at": "2026-07-13T00:00:00Z",
                    "archived_by_staff_id": 7,
                    "reason": "cleanup",
                },
                "access_token": "must-not-leak",
            }
            return _Result(
                rows=[
                    {
                        **_report_row(status=status),
                        "period_start": "2026-07-01T00:00:00Z",
                        "period_end": "2026-07-08T00:00:00Z",
                        "triggered_at": "2026-07-08T00:00:00Z",
                        "summary_text": "safe summary",
                        "metric_run_id": 1,
                        "metadata_json": json.dumps(metadata),
                    }
                ]
            )

    conn = _HistoryConn()
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(reports, "get_conn", lambda: conn)

    active = reports.list_reports(
        staff={"id": 7, "role": "employee", "organization_id": 1}
    )
    archived = reports.list_reports(
        staff={"id": 7, "role": "employee", "organization_id": 1},
        archived=True,
    )

    assert "status<>'archived'" in conn.sql[0]
    assert "status='archived'" in conn.sql[1]
    assert active["reports"][0]["schema_version"] == "report.v1"
    assert active["reports"][0]["request"] == {
        "report_type": "weekly",
        "period": "weekly",
        "period_days": 7,
        "date": "2026-07-08",
        "date_from": "2026-07-02",
        "date_to": "2026-07-08",
        "language": "en",
        "sections": ["kpiOverview", "summary"],
        "format": "markdown",
        "scope": "self",
    }
    assert active["reports"][0]["format"] == "markdown"
    assert archived["reports"][0]["archive_reason"] == "cleanup"
    assert "metadata_json" not in archived["reports"][0]
    assert "access_token" not in str(archived)


def _html_context() -> dict[str, Any]:
    return {
        "title": "<script>alert(1)</script>",
        "period_label": "2026-07-01 to 2026-07-07",
        "watermark_user": "<img src=file:///etc/passwd>",
        "generated_at": "2026-07-13T00:00:00Z",
        "report_uid": "weekly-test",
        "summary_text": "<b>unsafe</b>",
        "kpis": [],
        "funnel": [],
        "staff_rows": [],
        "projects": [],
        "alerts": [],
        "source_appendix": [],
        "kpi_appendix": {},
        "metric_run_id": None,
    }


def test_html_autoescapes_and_renderer_denies_resources() -> None:
    html = pdf_renderer.render_report_html(_html_context())

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=file:///etc/passwd>" not in html
    with pytest.raises(ValueError, match="resource loading is disabled"):
        pdf_renderer._deny_report_resource("file:///etc/passwd")
    with pytest.raises(ValueError, match="resource loading is disabled"):
        pdf_renderer._deny_report_resource("https://example.com/tracker.png")


def test_report_storage_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage = tmp_path / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))

    stored = pdf_renderer.store_bytes(b"safe", filename="weekly-safe.pdf")

    assert pdf_renderer.resolve_stored_path(stored["file_path"]).read_bytes() == b"safe"
    with pytest.raises(ValueError, match="must not contain a path"):
        pdf_renderer.store_bytes(b"unsafe", filename="../outside.pdf")
    with pytest.raises(ValueError, match="outside report storage"):
        pdf_renderer.resolve_stored_path(tmp_path / "outside.pdf")


def test_report_storage_requires_absolute_regular_direct_child_and_rejects_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    storage.mkdir()
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside secret")
    link = storage / "linked.pdf"
    link.symlink_to(outside)
    nested = storage / "nested"
    nested.mkdir()
    nested_file = nested / "report.pdf"
    nested_file.write_bytes(b"nested")

    with pytest.raises(ValueError, match="absolute"):
        pdf_renderer.resolve_stored_path("linked.pdf")
    with pytest.raises(ValueError, match="direct child"):
        pdf_renderer.resolve_stored_path(nested_file)
    with pytest.raises(ValueError, match="must not be a symlink"):
        pdf_renderer.resolve_stored_path(link)
    with pytest.raises((OSError, ValueError)):
        pdf_renderer.open_stored_file(link)
    assert outside.read_bytes() == b"outside secret"


def test_open_stored_file_keeps_original_inode_after_path_swap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    storage.mkdir()
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    path = storage / "weekly-safe.pdf"
    original = b"original report bytes"
    path.write_bytes(original)

    opened = pdf_renderer.open_stored_file(path)
    path.replace(storage / "weekly-retired.pdf")
    path.write_bytes(b"attacker replacement")

    assert opened.size == len(original)
    assert b"".join(opened.iter_bytes(chunk_size=3)) == original
    assert opened.closed is True


def test_open_stored_file_fails_closed_when_child_is_swapped_to_symlink_before_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    storage.mkdir()
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    path = storage / "weekly-safe.pdf"
    path.write_bytes(b"original")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside secret")
    original_open = pdf_renderer.os.open
    swapped = False

    def _swap_then_open(raw_path, flags, *args, **kwargs):
        nonlocal swapped
        if raw_path == path.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            path.unlink()
            path.symlink_to(outside)
        return original_open(raw_path, flags, *args, **kwargs)

    monkeypatch.setattr(pdf_renderer.os, "open", _swap_then_open)

    with pytest.raises(OSError):
        pdf_renderer.open_stored_file(path)
    assert swapped is True
    assert outside.read_bytes() == b"outside secret"


def test_store_bytes_removes_stage_before_single_directory_fsync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    observations: list[tuple[list[str], bytes]] = []

    def _observe_fsync(path: Path) -> None:
        observations.append(
            (
                sorted(item.name for item in path.iterdir()),
                (path / "weekly-safe.pdf").read_bytes(),
            )
        )

    monkeypatch.setattr(pdf_renderer, "_fsync_directory", _observe_fsync)
    pdf_renderer.store_bytes(b"durable", filename="weekly-safe.pdf")

    assert observations == [(["weekly-safe.pdf"], b"durable")]


def test_report_and_export_download_stream_validated_fd_and_close_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers import vkpi_reports

    storage = tmp_path / "reports"
    storage.mkdir()
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    report_path = storage / "weekly-safe.pdf"
    export_path = storage / "safe-export.csv"
    report_bytes = b"%PDF-original-report"
    export_bytes = b"id,value\n1,safe\n"
    report_path.write_bytes(report_bytes)
    export_path.write_bytes(export_bytes)

    app = FastAPI()
    app.include_router(vkpi_reports.router)
    download_paths = {
        "/api/admin/vkpi/reports/files/{report_run_id}/download",
        "/api/admin/vkpi/exports/{export_id}/download",
    }
    for route in app.routes:
        if getattr(route, "path", "") in download_paths:
            for dependency in route.dependant.dependencies:
                app.dependency_overrides[dependency.call] = lambda: {
                    "id": 7,
                    "role": "manager",
                    "organization_id": 1,
                }

    monkeypatch.setattr(
        vkpi_reports.reports,
        "report_file",
        lambda *_args, **_kwargs: {
            "id": 44,
            "file_path": str(report_path),
            "file_format": "pdf",
            "file_size_bytes": len(report_bytes),
            "sha256_hex": hashlib.sha256(report_bytes).hexdigest(),
        },
    )
    monkeypatch.setattr(
        vkpi_reports.exports,
        "export_file",
        lambda *_args, **_kwargs: {
            "file_path": str(export_path),
            "file_format": "csv",
            "export_type": "projects",
            "file_size_bytes": len(export_bytes),
            "sha256_hex": hashlib.sha256(export_bytes).hexdigest(),
        },
    )
    recorded_downloads: list[int] = []
    monkeypatch.setattr(
        vkpi_reports.reports,
        "record_report_download",
        lambda file_id, **_kwargs: recorded_downloads.append(int(file_id)),
    )
    monkeypatch.setattr(vkpi_reports.audit, "log_sensitive_access", lambda **_kwargs: None)
    monkeypatch.setattr(vkpi_reports.audit, "log_business_event", lambda **_kwargs: None)

    real_open = pdf_renderer.open_stored_file
    opened_handles: list[pdf_renderer.OpenedStoredFile] = []

    def _open_then_swap(file_path: str) -> pdf_renderer.OpenedStoredFile:
        opened = real_open(file_path)
        opened_handles.append(opened)
        if opened.path.name == report_path.name:
            opened.path.replace(storage / "weekly-retired.pdf")
            opened.path.write_bytes(b"replacement after secure open")
        return opened

    monkeypatch.setattr(vkpi_reports.reports, "open_stored_file", _open_then_swap)

    with TestClient(app, raise_server_exceptions=False) as client:
        report_response = client.get("/api/admin/vkpi/reports/files/44/download?format=pdf")
        export_response = client.get("/api/admin/vkpi/exports/77/download")

    assert report_response.status_code == 200
    assert report_response.content == report_bytes
    assert report_response.headers["content-length"] == str(len(report_bytes))
    assert report_response.headers["content-disposition"] == 'attachment; filename="weekly-safe.pdf"'
    assert export_response.status_code == 200
    assert export_response.content == export_bytes
    assert export_response.headers["content-length"] == str(len(export_bytes))
    assert export_response.headers["content-disposition"] == 'attachment; filename="safe-export.csv"'
    assert len(opened_handles) == 2
    assert all(opened.closed for opened in opened_handles)
    assert recorded_downloads == [44]


def test_download_stream_closes_fd_when_client_disconnects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import asyncio

    from starlette.requests import ClientDisconnect

    from app.api.routers import vkpi_reports

    storage = tmp_path / "reports"
    storage.mkdir()
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    path = storage / "large.pdf"
    path.write_bytes(b"x" * (128 * 1024))
    opened = pdf_renderer.open_stored_file(path)
    response = vkpi_reports._stream_download(opened, media_type="application/pdf")

    async def _receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def _send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")

    with pytest.raises(ClientDisconnect):
        asyncio.run(
            response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                _receive,
                _send,
            )
        )

    assert opened.closed is True


@pytest.mark.parametrize("tampered", [b"evil", b"evil-longer"])
def test_report_download_rejects_size_or_digest_tamper_before_count_and_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tampered: bytes,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers import vkpi_reports

    storage = tmp_path / "reports"
    storage.mkdir()
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    path = storage / "weekly-integrity.pdf"
    expected = b"safe"
    path.write_bytes(tampered)

    app = FastAPI()
    app.include_router(vkpi_reports.router)
    for route in app.routes:
        if getattr(route, "path", "") == "/api/admin/vkpi/reports/files/{report_run_id}/download":
            for dependency in route.dependant.dependencies:
                app.dependency_overrides[dependency.call] = lambda: {
                    "id": 7,
                    "role": "manager",
                    "organization_id": 1,
                }

    monkeypatch.setattr(
        vkpi_reports.reports,
        "report_file",
        lambda *_args, **_kwargs: {
            "id": 91,
            "file_path": str(path),
            "file_format": "pdf",
            "file_size_bytes": len(expected),
            "sha256_hex": hashlib.sha256(expected).hexdigest(),
        },
    )
    counted: list[int] = []
    audited: list[str] = []
    monkeypatch.setattr(
        vkpi_reports.reports,
        "record_report_download",
        lambda file_id, **_kwargs: counted.append(int(file_id)),
    )
    monkeypatch.setattr(
        vkpi_reports.audit,
        "log_sensitive_access",
        lambda **_kwargs: audited.append("sensitive"),
    )
    monkeypatch.setattr(
        vkpi_reports.audit,
        "log_business_event",
        lambda **_kwargs: audited.append("business"),
    )
    real_open = pdf_renderer.open_stored_file
    opened: list[pdf_renderer.OpenedStoredFile] = []

    def _track_open(file_path: str) -> pdf_renderer.OpenedStoredFile:
        handle = real_open(file_path)
        opened.append(handle)
        return handle

    monkeypatch.setattr(vkpi_reports.reports, "open_stored_file", _track_open)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/vkpi/reports/files/91/download?format=pdf")

    assert response.status_code == 404
    assert counted == []
    assert audited == []
    assert len(opened) == 1 and opened[0].closed is True


def test_download_audit_failure_does_not_block_validated_report_or_reverse_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers import vkpi_reports

    storage = tmp_path / "reports"
    storage.mkdir()
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    path = storage / "weekly-audit.pdf"
    content = b"%PDF-audit-safe"
    path.write_bytes(content)

    app = FastAPI()
    app.include_router(vkpi_reports.router)
    for route in app.routes:
        if getattr(route, "path", "") == "/api/admin/vkpi/reports/files/{report_run_id}/download":
            for dependency in route.dependant.dependencies:
                app.dependency_overrides[dependency.call] = lambda: {
                    "id": 7,
                    "role": "manager",
                    "organization_id": 1,
                }

    monkeypatch.setattr(
        vkpi_reports.reports,
        "report_file",
        lambda *_args, **_kwargs: {
            "id": 92,
            "file_path": str(path),
            "file_format": "pdf",
            "file_size_bytes": len(content),
            "sha256_hex": hashlib.sha256(content).hexdigest(),
        },
    )
    counted: list[int] = []
    rollbacks: list[str] = []
    monkeypatch.setattr(
        vkpi_reports.reports,
        "record_report_download",
        lambda file_id, **_kwargs: counted.append(int(file_id)),
    )
    monkeypatch.setattr(
        vkpi_reports.reports,
        "rollback_current_report_transaction",
        lambda: rollbacks.append("rollback"),
    )
    monkeypatch.setattr(
        vkpi_reports.audit,
        "log_sensitive_access",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/vkpi/reports/files/92/download?format=pdf")

    assert response.status_code == 200
    assert response.content == content
    assert counted == [92]
    assert rollbacks == ["rollback"]


def test_report_and_export_files_require_ready_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StatusConn:
        def __init__(self, row: dict[str, Any]) -> None:
            self.row = row

        def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Result:
            return _Result(row=dict(self.row))

    staff = {"id": 7, "role": "employee", "organization_id": 1}
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(
        reports,
        "get_conn",
        lambda: _StatusConn(_report_row(status="rendering")),
    )
    with pytest.raises(LookupError, match="not ready"):
        reports.report_file(44, staff=staff)

    monkeypatch.setattr(export_jobs, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(
        export_jobs,
        "get_conn",
        lambda: _StatusConn(
            {
                "id": 77,
                "requested_by_staff_id": 7,
                "export_type": "projects",
                "file_format": "csv",
                "status": "failed",
                "file_path": "/safe/failed.csv",
                "filters_json": "{}",
            }
        ),
    )
    with pytest.raises(LookupError, match="not ready"):
        export_jobs.export_file(77, staff=staff)


def test_export_file_returns_persisted_integrity_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integrity = {
        "schema_version": "export-file.v1",
        "file_size_bytes": 13,
        "sha256_hex": "a" * 64,
    }

    class _ReadyConn:
        def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Result:
            return _Result(
                row={
                    "id": 77,
                    "requested_by_staff_id": 7,
                    "export_type": "projects",
                    "file_format": "csv",
                    "status": "ready",
                    "file_path": "/safe/export.csv",
                    "expires_at": None,
                    "filters_json": json.dumps({"report_type": "projects", "_file_integrity": integrity}),
                }
            )

    monkeypatch.setattr(export_jobs, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(export_jobs, "get_conn", lambda: _ReadyConn())
    item = export_jobs.export_file(
        77,
        staff={"id": 7, "role": "employee", "organization_id": 1},
    )

    assert item["file_size_bytes"] == 13
    assert item["sha256_hex"] == "a" * 64
    assert "filters_json" not in item


def test_report_download_count_updates_only_ready_file_after_validation_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CountResult:
        rowcount = 1

    class _CountConn:
        def __init__(self) -> None:
            self.sql = ""
            self.params: tuple[Any, ...] = ()
            self.commits = 0

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _CountResult:
            self.sql = " ".join(sql.split())
            self.params = tuple(params)
            return _CountResult()

        def commit(self) -> None:
            self.commits += 1

    conn = _CountConn()
    monkeypatch.setattr(reports, "get_conn", lambda: conn)
    reports.record_report_download(
        91,
        staff={"id": 7, "role": "employee", "organization_id": 1},
    )

    assert "r.status='ready'" in conn.sql
    assert conn.params[1:] == (7, 91)
    assert conn.commits == 1


def test_unscoped_report_and_export_capability_denies_non_default_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoSql:
        def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
            raise AssertionError("non-default workspace must fail before resource SQL")

    staff = {"id": 9, "role": "manager", "organization_id": 2}
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(reports, "get_conn", lambda: _NoSql())
    with pytest.raises(scope.ScopeDenied, match="organization scope unavailable"):
        reports.list_reports(staff=staff)

    monkeypatch.setattr(export_jobs, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(export_jobs, "get_conn", lambda: _NoSql())
    with pytest.raises(scope.ScopeDenied, match="organization scope unavailable"):
        export_jobs.list_exports(staff=staff)
