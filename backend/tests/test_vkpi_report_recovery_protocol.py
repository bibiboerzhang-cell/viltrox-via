from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.domains.reports import pdf_renderer
from app.domains.reports import render_recovery


FIXED_NOW = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
ATTEMPT_ID = "0123456789abcdef0123456789abcdef"


def _metadata(
    *,
    report_uid: str = "weekly-recovery",
    formats: tuple[str, ...] = ("markdown", "pdf"),
    now: datetime = FIXED_NOW,
) -> dict[str, Any]:
    suffixes = {"markdown": "md", "pdf": "pdf"}
    protocol = render_recovery.new_report_render_protocol(
        report_uid,
        [
            {"format": file_format, "name": f"{report_uid}.{suffixes[file_format]}"}
            for file_format in formats
        ],
        now=now,
        lease_seconds=900,
        attempt_id=ATTEMPT_ID,
    )
    return render_recovery.with_report_render_protocol(
        {"report_type": "weekly"},
        protocol,
    )


def _published_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    publish_metadata: bool = True,
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(tmp_path / "reports"))
    metadata = _metadata()
    files = [
        (
            "markdown",
            pdf_renderer.store_bytes(b"# bounded report\n", filename="weekly-recovery.md"),
        ),
        (
            "pdf",
            pdf_renderer.store_bytes(b"%PDF-bounded", filename="weekly-recovery.pdf"),
        ),
    ]
    document, manifest = render_recovery.publish_report_completion_manifest(
        metadata,
        files,
        completed_at=FIXED_NOW + timedelta(minutes=1),
    )
    if publish_metadata:
        metadata = render_recovery.metadata_with_published_manifest(
            metadata,
            manifest,
            published_at=FIXED_NOW + timedelta(minutes=1),
        )
    return metadata, files, document, manifest


def _file_rows(files: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        {
            "file_format": file_format,
            "file_path": stored["file_path"],
            "file_size_bytes": stored["file_size_bytes"],
            "sha256_hex": stored["sha256_hex"],
        }
        for file_format, stored in files
    ]


def test_protocol_metadata_has_attempt_lease_expected_files_and_hidden_manifest() -> None:
    metadata = _metadata()
    protocol = metadata[render_recovery.RENDER_METADATA_KEY]

    assert protocol == {
        "schema_version": "report-render.v1",
        "report_uid": "weekly-recovery",
        "attempt_id": ATTEMPT_ID,
        "lease_expires_at": "2026-07-13T20:15:00Z",
        "expected_files": [
            {"format": "markdown", "name": "weekly-recovery.md"},
            {"format": "pdf", "name": "weekly-recovery.pdf"},
        ],
        "completion_manifest": {
            "schema_version": "report-completion.v1",
            "name": f".weekly-recovery.{ATTEMPT_ID}.complete.json",
            "state": "pending",
        },
    }


def test_manifest_binds_report_attempt_and_each_file_and_inspector_matches_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metadata, files, document, manifest = _published_bundle(monkeypatch, tmp_path)

    assert document["report_uid"] == "weekly-recovery"
    assert document["attempt_id"] == ATTEMPT_ID
    assert [(item["format"], item["name"]) for item in document["files"]] == [
        ("markdown", "weekly-recovery.md"),
        ("pdf", "weekly-recovery.pdf"),
    ]
    assert all(item["size"] >= 1 and len(item["sha256"]) == 64 for item in document["files"])
    assert Path(manifest["file_path"]).name.startswith(".weekly-recovery.")
    assert metadata["_render_v1"]["completion_manifest"]["state"] == "published"

    inspection = render_recovery.inspect_report_completion(metadata, _file_rows(files))
    assert inspection.status == "valid"
    assert inspection.storage_valid is True
    assert inspection.db_rows_match is True
    assert inspection.reasons == ()
    assert inspection.db_row_reasons == ()


def test_completion_manifest_publish_is_no_clobber(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metadata, files, _document, manifest = _published_bundle(
        monkeypatch,
        tmp_path,
        publish_metadata=False,
    )
    manifest_path = Path(manifest["file_path"])
    original = manifest_path.read_bytes()

    with pytest.raises(FileExistsError, match="already exists"):
        render_recovery.publish_report_completion_manifest(
            metadata,
            files,
            completed_at=FIXED_NOW + timedelta(minutes=2),
        )

    assert manifest_path.read_bytes() == original
    assert not any(path.name.endswith(".tmp") for path in manifest_path.parent.iterdir())


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda doc: doc.update(attempt_id="f" * 32), "attempt_id_mismatch"),
        (
            lambda doc: doc["files"].append(copy.deepcopy(doc["files"][0])),
            "manifest_format_duplicate",
        ),
        (
            lambda doc: doc["files"][0].update(name="../outside.md"),
            "manifest_name_invalid",
        ),
    ],
)
def test_pure_validator_rejects_wrong_token_duplicate_format_and_path_escape(
    mutation: Any,
    reason: str,
) -> None:
    metadata = _metadata()
    files = [
        (
            "markdown",
            {
                "file_path": "/safe/weekly-recovery.md",
                "file_size_bytes": 4,
                "sha256_hex": "a" * 64,
            },
        ),
        (
            "pdf",
            {
                "file_path": "/safe/weekly-recovery.pdf",
                "file_size_bytes": 5,
                "sha256_hex": "b" * 64,
            },
        ),
    ]
    document = render_recovery.build_completion_manifest_document(
        metadata,
        files,
        completed_at=FIXED_NOW,
    )
    mutation(document)

    result = render_recovery.validate_completion_document(metadata, document)

    assert result.status == "invalid"
    assert reason in result.reasons


def test_inspector_rejects_missing_or_tampered_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metadata, files, _document, manifest = _published_bundle(monkeypatch, tmp_path)
    manifest_path = Path(manifest["file_path"])
    manifest_path.unlink()

    missing = render_recovery.inspect_report_completion(metadata, _file_rows(files))
    assert missing.status == "invalid"
    assert missing.reasons == ("manifest_missing_or_unsafe",)

    # Re-publish a fresh bundle in another isolated root and mutate bytes after
    # the descriptor SHA was committed into metadata.
    metadata, files, _document, manifest = _published_bundle(
        monkeypatch,
        tmp_path / "tampered",
    )
    Path(manifest["file_path"]).write_bytes(b"{}\n")
    tampered = render_recovery.inspect_report_completion(metadata, _file_rows(files))
    assert tampered.status == "invalid"
    assert tampered.reasons == ("manifest_integrity_invalid",)


def test_inspector_rejects_data_tamper_and_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metadata, files, _document, _manifest = _published_bundle(monkeypatch, tmp_path)
    pdf_path = Path(files[1][1]["file_path"])
    pdf_path.write_bytes(b"tampered")

    tampered = render_recovery.inspect_report_completion(metadata, _file_rows(files))
    assert tampered.status == "invalid"
    assert tampered.reasons == ("data_file_invalid:pdf",)

    metadata, files, _document, _manifest = _published_bundle(
        monkeypatch,
        tmp_path / "symlink",
    )
    pdf_path = Path(files[1][1]["file_path"])
    external = tmp_path / "external.pdf"
    external.write_bytes(b"%PDF-bounded")
    pdf_path.unlink()
    pdf_path.symlink_to(external)

    symlinked = render_recovery.inspect_report_completion(metadata, _file_rows(files))
    assert symlinked.status == "invalid"
    assert symlinked.reasons == ("data_file_invalid:pdf",)


def test_dry_run_waits_for_active_lease_then_returns_candidate_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Keep metadata descriptor pending to model a kill after manifest publish
    # but before the terminal ready metadata CAS.
    metadata, files, _document, _manifest = _published_bundle(
        monkeypatch,
        tmp_path,
        publish_metadata=False,
    )
    report = {
        "id": 88,
        "report_uid": "weekly-recovery",
        "status": "rendering",
        "metadata_json": json.dumps(metadata),
    }

    active = render_recovery.reconcile_report_run_dry_run(
        report,
        _file_rows(files),
        now=FIXED_NOW + timedelta(minutes=5),
    )
    assert active["action"] == "wait_for_lease"
    assert active["mutated_database"] is False
    assert active["deleted_files"] is False

    expired = render_recovery.reconcile_report_run_dry_run(
        report,
        _file_rows(files),
        now=FIXED_NOW + timedelta(minutes=16),
    )
    assert expired["action"] == "ready_cas_candidate"
    assert expired["inspection"]["storage_valid"] is True
    assert expired["inspection"]["db_rows_match"] is True
    assert expired["mutated_database"] is False
    assert expired["deleted_files"] is False


def test_dry_run_marks_legacy_and_conflicting_rows_manual_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy = render_recovery.reconcile_report_run_dry_run(
        {"id": 1, "report_uid": "legacy", "status": "rendering", "metadata_json": "{}"},
        now=FIXED_NOW,
    )
    assert legacy["action"] == "manual_required"
    assert legacy["reasons"] == ["legacy_protocol"]

    metadata, files, _document, _manifest = _published_bundle(monkeypatch, tmp_path)
    conflicting = _file_rows(files)
    conflicting[0] = {**conflicting[0], "file_path": "/tmp/outside.md"}
    report = {
        "id": 88,
        "report_uid": "weekly-recovery",
        "status": "rendering",
        "metadata_json": json.dumps(metadata),
    }
    decision = render_recovery.reconcile_report_run_dry_run(
        report,
        conflicting,
        now=FIXED_NOW + timedelta(minutes=16),
    )
    assert decision["action"] == "manual_required"
    assert "db_file_path_unsafe" in decision["reasons"]
    assert decision["mutated_database"] is False


def test_dry_run_does_not_create_missing_storage_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "does-not-exist" / "reports"
    monkeypatch.setenv("VKPI_REPORT_STORAGE_PATH", str(storage))
    metadata = _metadata()
    report = {
        "id": 88,
        "report_uid": "weekly-recovery",
        "status": "rendering",
        "metadata_json": json.dumps(metadata),
    }

    decision = render_recovery.reconcile_report_run_dry_run(
        report,
        now=FIXED_NOW + timedelta(minutes=16),
    )

    assert decision["action"] == "manual_required"
    assert decision["reasons"] == ["manifest_missing_or_unsafe"]
    assert storage.exists() is False


def test_unexecuted_lock_plans_define_pg_and_sqlite_boundaries() -> None:
    pg = render_recovery.reconciliation_lock_plan("postgresql")
    sqlite = render_recovery.reconciliation_lock_plan("sqlite")

    assert "FOR UPDATE SKIP LOCKED" in pg.select_sql
    assert pg.begin_sql is None
    assert pg.terminal_cas_required is True
    assert pg.tested_against_live_database is False
    assert sqlite.begin_sql == "BEGIN IMMEDIATE"
    assert "FOR UPDATE" not in sqlite.select_sql
    assert sqlite.terminal_cas_required is True
    assert sqlite.tested_against_live_database is False


def test_terminal_cas_sql_is_text_portable_and_fail_closed() -> None:
    postgres = " ".join(render_recovery.terminal_ready_cas_sql("postgresql").split())
    sqlite = " ".join(render_recovery.terminal_ready_cas_sql("sqlite").split())

    assert postgres == sqlite
    assert "metadata_json=?" in postgres
    assert "status='rendering'" in postgres
    assert "::jsonb" not in postgres.lower()
    assert postgres.count("?") == 3
    with pytest.raises(ValueError, match="unsupported report CAS dialect"):
        render_recovery.terminal_ready_cas_sql("mysql")


def test_inspector_rejects_symlinked_completion_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metadata, files, _document, manifest = _published_bundle(monkeypatch, tmp_path)
    manifest_path = Path(manifest["file_path"])
    external = tmp_path / "external-completion.json"
    external.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(external)

    result = render_recovery.inspect_report_completion(metadata, _file_rows(files))

    assert result.status == "invalid"
    assert result.reasons == ("manifest_missing_or_unsafe",)
