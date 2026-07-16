from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from app.domains.reports import pdf_renderer
from app.domains.reports import export_jobs
from app.domains.reports import reports


class _Result:
    def __init__(self, row: dict[str, Any] | None = None, *, rowcount: int | None = 1) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class InjectedInsertFailure(RuntimeError):
    pass


class SecondFileFailure(RuntimeError):
    pass


class ExportReadyFailure(RuntimeError):
    pass


class CommitAmbiguous(RuntimeError):
    pass


class SimulatedProcessDeath(BaseException):
    pass


class _PgAbortedConn:
    """Small transactional fake that rejects SQL until rollback after an error."""

    def __init__(
        self,
        *,
        fail_file_insert: int | None = None,
        fail_ready_cas: bool = False,
        ready_rowcount: int | None = 1,
        ambiguous_ready_commit: bool = False,
    ) -> None:
        self.fail_file_insert = fail_file_insert
        self.fail_ready_cas = fail_ready_cas
        self.ready_rowcount = ready_rowcount
        self.ambiguous_ready_commit = ambiguous_ready_commit
        self._ambiguous_commit_raised = False
        self.file_insert_count = 0
        self.aborted = False
        self.rollbacks = 0
        self.events: list[str] = []
        self.committed_transactions: list[list[str]] = []
        self.runs: dict[int, dict[str, Any]] = {
            41: {"id": 41, "report_uid": "other-report", "status": "ready"}
        }
        self.files: list[dict[str, Any]] = [
            {
                "report_run_id": 41,
                "file_format": "pdf",
                "file_path": "/kept/other-report.pdf",
            }
        ]
        self._tx_runs: dict[int, dict[str, Any]] | None = None
        self._tx_files: list[dict[str, Any]] | None = None
        self._tx_sql: list[str] = []

    def _start(self) -> None:
        if self._tx_runs is None:
            self._tx_runs = copy.deepcopy(self.runs)
            self._tx_files = copy.deepcopy(self.files)
            self._tx_sql = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        if self.aborted:
            raise AssertionError("current transaction is aborted; rollback required")
        self._start()
        assert self._tx_runs is not None
        assert self._tx_files is not None
        self._tx_sql.append(compact)
        self.events.append(compact)

        if compact.startswith("INSERT INTO vkpi_report_runs"):
            self._tx_runs[88] = {
                "id": 88,
                "report_uid": params[0],
                "status": params[9],
                "error_message": "",
                "metadata_json": params[11],
            }
            return _Result()
        if compact.startswith("SELECT id FROM vkpi_report_runs"):
            row = next(
                (row for row in self._tx_runs.values() if row["report_uid"] == params[0]),
                None,
            )
            return _Result({"id": row["id"]} if row else None)
        if compact.startswith("INSERT INTO vkpi_report_files"):
            self.file_insert_count += 1
            if self.file_insert_count == self.fail_file_insert:
                self.aborted = True
                raise InjectedInsertFailure("injected report file INSERT failure")
            self._tx_files.append(
                {
                    "report_run_id": params[0],
                    "file_format": params[1],
                    "file_path": params[2],
                }
            )
            return _Result()
        if compact.startswith("UPDATE vkpi_report_runs SET status='ready'"):
            report_run_id = int(params[1])
            row = self._tx_runs[report_run_id]
            if (
                self.fail_ready_cas
                or self.ready_rowcount != 1
                or row.get("status") != "rendering"
                or row.get("metadata_json") != params[2]
            ):
                return _Result(rowcount=self.ready_rowcount)
            row["status"] = "ready"
            row["metadata_json"] = params[0]
            row["error_message"] = ""
            return _Result(rowcount=1)
        if compact.startswith("DELETE FROM vkpi_report_files"):
            report_run_id = int(params[0])
            self._tx_files = [
                row for row in self._tx_files if int(row["report_run_id"]) != report_run_id
            ]
            return _Result()
        if compact.startswith("UPDATE vkpi_report_runs SET status='failed'"):
            row = self._tx_runs[int(params[1])]
            if row.get("status") != "rendering" or row.get("metadata_json") != params[2]:
                return _Result(rowcount=0)
            row["status"] = "failed"
            row["error_message"] = params[0]
            return _Result(rowcount=1)
        raise AssertionError(f"unexpected SQL: {compact}")

    def commit(self) -> None:
        if self.aborted:
            raise AssertionError("cannot commit an aborted transaction")
        assert self._tx_runs is not None
        assert self._tx_files is not None
        ready_commit = self._tx_runs.get(88, {}).get("status") == "ready"
        self.runs = self._tx_runs
        self.files = self._tx_files
        self.committed_transactions.append(list(self._tx_sql))
        self.events.append("COMMIT")
        self._tx_runs = None
        self._tx_files = None
        self._tx_sql = []
        if self.ambiguous_ready_commit and ready_commit and not self._ambiguous_commit_raised:
            self._ambiguous_commit_raised = True
            raise CommitAmbiguous("commit outcome unavailable after server accepted it")

    def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("ROLLBACK")
        self.aborted = False
        self._tx_runs = None
        self._tx_files = None
        self._tx_sql = []


class _ExportPgConn:
    """Transactional export fake with PostgreSQL-style aborted state."""

    def __init__(self, *, fail_ready: bool = False) -> None:
        self.fail_ready = fail_ready
        self.aborted = False
        self.rollbacks = 0
        self.jobs: dict[int, dict[str, Any]] = {}
        self._tx_jobs: dict[int, dict[str, Any]] | None = None

    def _start(self) -> None:
        if self._tx_jobs is None:
            self._tx_jobs = copy.deepcopy(self.jobs)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        if self.aborted:
            raise AssertionError("current transaction is aborted; rollback required")
        self._start()
        assert self._tx_jobs is not None
        if compact.startswith("INSERT INTO vkpi_export_jobs"):
            self._tx_jobs[77] = {
                "id": 77,
                "export_uid": params[0],
                "status": params[5],
                "filters_json": params[4],
                "file_path": "",
            }
            return _Result()
        if compact.startswith("SELECT id FROM vkpi_export_jobs"):
            return _Result({"id": 77})
        if compact.startswith("UPDATE vkpi_export_jobs SET status='ready'"):
            if self.fail_ready:
                self.aborted = True
                raise ExportReadyFailure("injected export ready update failure")
            job = self._tx_jobs[int(params[5])]
            job.update(
                {
                    "status": "ready",
                    "file_path": params[0],
                    "filters_json": params[4],
                    "error_message": "",
                }
            )
            return _Result()
        if compact.startswith("UPDATE vkpi_export_jobs SET status='failed'"):
            job = self._tx_jobs.get(int(params[1]))
            if job and job.get("status") == "running":
                job.update({"status": "failed", "error_message": params[0]})
            return _Result()
        raise AssertionError(f"unexpected SQL: {compact}")

    def commit(self) -> None:
        if self.aborted:
            raise AssertionError("cannot commit an aborted transaction")
        assert self._tx_jobs is not None
        self.jobs = self._tx_jobs
        self._tx_jobs = None

    def rollback(self) -> None:
        self.rollbacks += 1
        self.aborted = False
        self._tx_jobs = None


def _generation_context() -> dict[str, Any]:
    return {
        "report_spec": {"schema_version": "report.v1"},
        "data_status": "real",
        "model_policy": {"mode": "deterministic_descriptive"},
        "period_start": "2026-07-01T00:00:00Z",
        "period_end": "2026-07-07T23:59:59Z",
        "summary_text": "bounded summary",
    }


def _prepare_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    conn: Any,
) -> None:
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(tmp_path / "reports"))
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(reports, "get_conn", lambda: conn)
    monkeypatch.setattr(reports, "build_weekly_context", lambda **_kwargs: _generation_context())
    monkeypatch.setattr(reports, "_render_markdown_report", lambda _context: "# atomic report\n")
    monkeypatch.setattr(reports, "_uid", lambda _prefix: "weekly-atomic")


def _generate_markdown() -> dict[str, Any]:
    return reports.generate_weekly_report(
        staff={"id": 7, "role": "manager", "organization_id": 1},
        filters={
            "period_days": 7,
            "date_from": "2026-07-01",
            "date_to": "2026-07-07",
            "sections": ["kpiOverview"],
            "format": "markdown",
            "scope": "self",
        },
        render_pdf=True,
    )


def test_store_bytes_validates_stage_then_atomically_publishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    original_link = pdf_renderer.os.link
    observed: dict[str, Any] = {}

    def _link(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        observed.update(
            {
                "source_name": source_path.name,
                "source_bytes": source_path.read_bytes(),
                "destination_existed": destination_path.exists(),
            }
        )
        original_link(source, destination)

    monkeypatch.setattr(pdf_renderer.os, "link", _link)
    stored = pdf_renderer.store_bytes(b"verified report bytes", filename="weekly-safe.pdf")

    assert observed["source_name"].startswith(".weekly-safe.pdf.")
    assert observed["source_name"].endswith(".tmp")
    assert observed["source_bytes"] == b"verified report bytes"
    assert observed["destination_existed"] is False
    assert Path(stored["file_path"]).read_bytes() == b"verified report bytes"
    assert [path.name for path in storage.iterdir()] == ["weekly-safe.pdf"]


def test_store_bytes_cleans_stage_on_publish_failure_and_never_overwrites(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    original_link = pdf_renderer.os.link
    monkeypatch.setattr(
        pdf_renderer.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(OSError, match="publish failed"):
        pdf_renderer.store_bytes(b"new", filename="weekly-safe.pdf")
    assert list(storage.iterdir()) == []

    monkeypatch.setattr(pdf_renderer.os, "link", original_link)
    existing = storage / "weekly-safe.pdf"
    existing.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        pdf_renderer.store_bytes(b"replacement", filename="weekly-safe.pdf")
    assert existing.read_bytes() == b"existing"
    assert [path.name for path in storage.iterdir()] == ["weekly-safe.pdf"]


def test_store_bytes_concurrent_same_name_has_exactly_one_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    original_link = pdf_renderer.os.link
    barrier = Barrier(2)

    def _racing_link(source: str | Path, destination: str | Path) -> None:
        barrier.wait(timeout=5)
        original_link(source, destination)

    monkeypatch.setattr(pdf_renderer.os, "link", _racing_link)

    def _store(content: bytes) -> tuple[str, Any]:
        try:
            return "ok", pdf_renderer.store_bytes(content, filename="same.pdf")
        except Exception as exc:  # capture both contenders for exact assertions
            return "error", exc

    payloads = (b"first contender", b"second contender")
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(_store, payloads))

    assert [state for state, _value in outcomes].count("ok") == 1
    errors = [value for state, value in outcomes if state == "error"]
    assert len(errors) == 1 and isinstance(errors[0], FileExistsError)
    assert (storage / "same.pdf").read_bytes() in payloads
    assert [path.name for path in storage.iterdir()] == ["same.pdf"]


def test_cleanup_rejects_symlink_without_deleting_its_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    storage.mkdir(parents=True)
    target_dir = storage / "unrelated"
    target_dir.mkdir()
    target = target_dir / "weekly-safe.pdf"
    target.write_bytes(b"must survive")
    link = storage / "weekly-safe.pdf"
    link.symlink_to(target)

    reports._cleanup_generated_report_files(
        "weekly-safe",
        [("pdf", {"file_path": str(link)})],
    )

    assert link.is_symlink()
    assert target.read_bytes() == b"must survive"


def test_second_file_failure_rolls_back_cleans_only_current_run_and_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn()
    _prepare_generation(monkeypatch, tmp_path, conn)
    storage = pdf_renderer.report_storage_dir()
    unrelated = storage / "other-report.pdf"
    unrelated.write_bytes(b"keep me")
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SecondFileFailure("pdf failed")),
    )

    with pytest.raises(SecondFileFailure):
        _generate_markdown()

    assert conn.runs[88]["status"] == "failed"
    assert conn.runs[88]["error_message"] == "SecondFileFailure"
    assert conn.files == [
        {
            "report_run_id": 41,
            "file_format": "pdf",
            "file_path": "/kept/other-report.pdf",
        }
    ]
    assert unrelated.read_bytes() == b"keep me"
    assert sorted(path.name for path in storage.iterdir()) == ["other-report.pdf"]
    rollback_index = conn.events.index("ROLLBACK")
    delete_index = next(
        index
        for index, event in enumerate(conn.events)
        if event.startswith("DELETE FROM vkpi_report_files")
    )
    failed_index = next(
        index
        for index, event in enumerate(conn.events)
        if event.startswith("UPDATE vkpi_report_runs SET status='failed'")
    )
    assert rollback_index < delete_index < failed_index


def test_second_file_insert_failure_recovers_from_pg_aborted_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn(fail_file_insert=2)
    _prepare_generation(monkeypatch, tmp_path, conn)
    storage = pdf_renderer.report_storage_dir()
    unrelated = storage / "other-report.pdf"
    unrelated.write_bytes(b"keep me")
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-atomic", filename=filename),
            "html": "<html></html>",
        },
    )

    with pytest.raises(InjectedInsertFailure):
        _generate_markdown()

    assert conn.rollbacks >= 1
    assert conn.runs[88]["status"] == "failed"
    assert conn.runs[88]["error_message"] == "InjectedInsertFailure"
    assert [row["report_run_id"] for row in conn.files] == [41]
    assert unrelated.read_bytes() == b"keep me"
    assert sorted(path.name for path in storage.iterdir()) == ["other-report.pdf"]
    assert all(
        not (
            any(sql.startswith("INSERT INTO vkpi_report_files") for sql in transaction)
            and any("status='ready'" in sql for sql in transaction)
        )
        for transaction in conn.committed_transactions
    )


def test_file_rows_and_ready_status_commit_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn()
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-atomic", filename=filename),
            "html": "<html></html>",
        },
    )

    result = _generate_markdown()

    assert result["status"] == "ready"
    assert conn.runs[88]["status"] == "ready"
    assert [row["file_format"] for row in conn.files if row["report_run_id"] == 88] == [
        "markdown",
        "pdf",
    ]
    assert len(conn.committed_transactions) == 2
    publication_transaction = conn.committed_transactions[1]
    assert sum(sql.startswith("INSERT INTO vkpi_report_files") for sql in publication_transaction) == 2
    assert publication_transaction[-1].startswith(
        "UPDATE vkpi_report_runs SET status='ready'"
    )


@pytest.mark.parametrize("rowcount", [0, None])
def test_terminal_ready_cas_requires_exact_rowcount_and_preserves_completed_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rowcount: int | None,
) -> None:
    conn = _PgAbortedConn(ready_rowcount=rowcount)
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-atomic", filename=filename),
            "html": "<html></html>",
        },
    )

    with pytest.raises(reports.render_recovery.ReportReadyCasConflict):
        _generate_markdown()

    # Completion is durable, so failure is left rendering for reconciliation;
    # the old cleanup/fail path must not destroy an ambiguous successful bundle.
    assert conn.runs[88]["status"] == "rendering"
    assert [row["report_run_id"] for row in conn.files] == [41]
    names = sorted(path.name for path in pdf_renderer.report_storage_dir().iterdir())
    assert "weekly-atomic.md" in names
    assert "weekly-atomic.pdf" in names
    assert any(name.startswith(".weekly-atomic.") and name.endswith(".complete.json") for name in names)
    assert not any(event.startswith("DELETE FROM vkpi_report_files") for event in conn.events)
    assert not any(event.startswith("UPDATE vkpi_report_runs SET status='failed'") for event in conn.events)


def test_ambiguous_commit_after_manifest_never_cleans_or_downgrades_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn(ambiguous_ready_commit=True)
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-atomic", filename=filename),
            "html": "<html></html>",
        },
    )

    with pytest.raises(CommitAmbiguous, match="outcome unavailable"):
        _generate_markdown()

    assert conn.runs[88]["status"] == "ready"
    assert [row["file_format"] for row in conn.files if row["report_run_id"] == 88] == [
        "markdown",
        "pdf",
    ]
    names = sorted(path.name for path in pdf_renderer.report_storage_dir().iterdir())
    assert "weekly-atomic.md" in names and "weekly-atomic.pdf" in names
    assert any(name.startswith(".weekly-atomic.") and name.endswith(".complete.json") for name in names)
    assert not any(event.startswith("DELETE FROM vkpi_report_files") for event in conn.events)
    assert not any(event.startswith("UPDATE vkpi_report_runs SET status='failed'") for event in conn.events)


def test_process_death_after_first_file_leaves_rendering_without_completion_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn()
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SimulatedProcessDeath()),
    )

    with pytest.raises(SimulatedProcessDeath):
        _generate_markdown()

    assert conn.runs[88]["status"] == "rendering"
    assert [row["report_run_id"] for row in conn.files] == [41]
    assert [path.name for path in pdf_renderer.report_storage_dir().iterdir()] == [
        "weekly-atomic.md"
    ]


def test_process_death_after_all_files_before_manifest_leaves_no_false_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn()
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-atomic", filename=filename),
            "html": "<html></html>",
        },
    )
    monkeypatch.setattr(
        reports.render_recovery,
        "publish_report_completion_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SimulatedProcessDeath()),
    )

    with pytest.raises(SimulatedProcessDeath):
        _generate_markdown()

    names = sorted(path.name for path in pdf_renderer.report_storage_dir().iterdir())
    assert names == ["weekly-atomic.md", "weekly-atomic.pdf"]
    assert conn.runs[88]["status"] == "rendering"
    assert [row["report_run_id"] for row in conn.files] == [41]


def test_ambiguous_manifest_publish_error_preserves_files_for_manual_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn()
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-atomic", filename=filename),
            "html": "<html></html>",
        },
    )
    monkeypatch.setattr(
        reports.render_recovery,
        "publish_report_completion_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("publish outcome unknown")),
    )

    with pytest.raises(OSError, match="outcome unknown"):
        _generate_markdown()

    assert sorted(path.name for path in pdf_renderer.report_storage_dir().iterdir()) == [
        "weekly-atomic.md",
        "weekly-atomic.pdf",
    ]
    assert conn.runs[88]["status"] == "rendering"
    assert [row["report_run_id"] for row in conn.files] == [41]
    assert not any(event.startswith("UPDATE vkpi_report_runs SET status='failed'") for event in conn.events)


def test_process_death_after_manifest_before_cas_preserves_recoverable_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _PgAbortedConn()
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-atomic", filename=filename),
            "html": "<html></html>",
        },
    )
    original_execute = conn.execute

    def _die_before_cas(sql: str, params: tuple[Any, ...] = ()) -> _Result:
        if "UPDATE vkpi_report_runs" in sql and "status='ready'" in sql:
            raise SimulatedProcessDeath()
        return original_execute(sql, params)

    monkeypatch.setattr(conn, "execute", _die_before_cas)

    with pytest.raises(SimulatedProcessDeath):
        _generate_markdown()

    names = sorted(path.name for path in pdf_renderer.report_storage_dir().iterdir())
    assert "weekly-atomic.md" in names and "weekly-atomic.pdf" in names
    assert any(name.startswith(".weekly-atomic.") and name.endswith(".complete.json") for name in names)
    assert conn.runs[88]["status"] == "rendering"
    assert [row["report_run_id"] for row in conn.files] == [41]


def test_sqlite_insert_abort_rolls_back_rows_and_persists_failed_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_report_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_uid TEXT NOT NULL UNIQUE,
            report_type TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id INTEGER,
            metric_run_id INTEGER,
            triggered_by_staff_id INTEGER,
            triggered_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT DEFAULT '',
            summary_text TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE vkpi_report_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_run_id INTEGER NOT NULL,
            file_format TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            download_url TEXT,
            sha256_hex TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO vkpi_report_runs
            (id, report_uid, report_type, period_start, period_end, scope_type,
             triggered_at, status, metadata_json)
        VALUES
            (41, 'other-report', 'weekly', '2026-06-01', '2026-06-07', 'all',
             '2026-06-07T00:00:00Z', 'ready', '{}');
        INSERT INTO vkpi_report_files
            (report_run_id, file_format, file_path, file_size_bytes, created_at)
        VALUES (41, 'pdf', '/kept/other-report.pdf', 4, '2026-06-07T00:00:00Z');
        CREATE TRIGGER fail_current_pdf_insert
        BEFORE INSERT ON vkpi_report_files
        WHEN NEW.report_run_id <> 41 AND NEW.file_format = 'pdf'
        BEGIN
            SELECT RAISE(ABORT, 'injected pdf row failure');
        END;
        """
    )
    _prepare_generation(monkeypatch, tmp_path, conn)
    storage = pdf_renderer.report_storage_dir()
    unrelated = storage / "other-report.pdf"
    unrelated.write_bytes(b"keep me")
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-sqlite", filename=filename),
            "html": "<html></html>",
        },
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected pdf row failure"):
        _generate_markdown()

    failed = conn.execute(
        "SELECT status, error_message FROM vkpi_report_runs WHERE report_uid='weekly-atomic'"
    ).fetchone()
    assert dict(failed) == {"status": "failed", "error_message": "IntegrityError"}
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_report_files WHERE report_run_id<>41"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_report_files WHERE report_run_id=41"
    ).fetchone()[0] == 1
    assert unrelated.read_bytes() == b"keep me"
    assert sorted(path.name for path in storage.iterdir()) == ["other-report.pdf"]


def test_sqlite_text_metadata_cas_persists_ready_bundle_and_is_inspectable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_report_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_uid TEXT NOT NULL UNIQUE,
            report_type TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id INTEGER,
            metric_run_id INTEGER,
            triggered_by_staff_id INTEGER,
            triggered_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT DEFAULT '',
            summary_text TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE vkpi_report_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_run_id INTEGER NOT NULL,
            file_format TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            download_url TEXT,
            sha256_hex TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    _prepare_generation(monkeypatch, tmp_path, conn)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            **pdf_renderer.store_bytes(b"%PDF-sqlite", filename=filename),
            "html": "<html></html>",
        },
    )

    generated = _generate_markdown()

    run = dict(conn.execute("SELECT * FROM vkpi_report_runs WHERE id=?", (generated["report_run_id"],)).fetchone())
    files = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM vkpi_report_files WHERE report_run_id=? ORDER BY id",
            (generated["report_run_id"],),
        ).fetchall()
    ]
    protocol = json.loads(run["metadata_json"])["_render_v1"]
    assert run["status"] == "ready"
    assert protocol["completion_manifest"]["state"] == "published"
    assert [row["file_format"] for row in files] == ["markdown", "pdf"]
    inspection = reports.render_recovery.inspect_report_completion(run["metadata_json"], files)
    assert inspection.storage_valid is True
    assert inspection.db_rows_match is True


def _prepare_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    conn: _ExportPgConn,
) -> Path:
    storage = tmp_path / "exports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    monkeypatch.setattr(export_jobs, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(export_jobs, "get_conn", lambda: conn)
    monkeypatch.setattr(export_jobs, "_uid", lambda _prefix: "export-csv-atomic")
    monkeypatch.setattr(export_jobs, "_rows", lambda *_args, **_kwargs: [])
    return storage


def test_export_pg_aborted_ready_failure_preserves_original_error_and_cleans_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _ExportPgConn(fail_ready=True)
    storage = _prepare_export(monkeypatch, tmp_path, conn)

    with pytest.raises(ExportReadyFailure, match="ready update"):
        export_jobs.create_export(
            export_format="csv",
            payload={"reportType": "projects"},
            staff={"id": 7, "role": "manager", "organization_id": 1},
        )

    assert conn.rollbacks >= 1
    assert conn.jobs[77]["status"] == "failed"
    assert conn.jobs[77]["error_message"] == "ExportReadyFailure"
    assert list(storage.iterdir()) == []


def test_export_audit_failure_keeps_ready_file_and_resets_aborted_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conn = _ExportPgConn()
    storage = _prepare_export(monkeypatch, tmp_path, conn)

    def _audit_failure(**_kwargs: Any) -> None:
        conn.aborted = True
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(export_jobs.audit, "log_export", _audit_failure)
    result = export_jobs.create_export(
        export_format="csv",
        payload={"reportType": "projects"},
        staff={"id": 7, "role": "manager", "organization_id": 1},
    )

    assert result["status"] == "ready"
    assert result["audit_status"] == "pending_retry"
    assert conn.aborted is False
    assert conn.jobs[77]["status"] == "ready"
    integrity = json.loads(conn.jobs[77]["filters_json"])["_file_integrity"]
    final_path = Path(conn.jobs[77]["file_path"])
    assert final_path == storage / "export-csv-atomic.csv"
    assert final_path.exists()
    assert integrity["file_size_bytes"] == final_path.stat().st_size
    assert integrity["sha256_hex"] == hashlib.sha256(final_path.read_bytes()).hexdigest()


def test_remove_stored_file_verifies_bytes_and_fsyncs_unlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    stored = pdf_renderer.store_bytes(b"durable cleanup", filename="cleanup.pdf")
    fsynced: list[Path] = []
    monkeypatch.setattr(pdf_renderer, "_fsync_directory", lambda path: fsynced.append(path))

    assert pdf_renderer.remove_stored_file(
        stored["file_path"],
        expected_size=stored["file_size_bytes"],
        expected_sha256=stored["sha256_hex"],
    ) is True

    assert not Path(stored["file_path"]).exists()
    assert fsynced == [storage]
