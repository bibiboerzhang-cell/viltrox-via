from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from scripts.ops import seal_postgres_backup_bundle as seal


HEAD = "fe3871c438ff9de8884589e052d8dd8b82b94b83"
MIGRATION = "260_vkpi_dealer_map_management.sql"


def _private_dump(tmp_path: Path) -> Path:
    tmp_path.chmod(0o700)
    dump = tmp_path / "candidate.dump"
    dump.write_bytes(b"PGDMP-private-test-archive")
    dump.chmod(0o600)
    return dump


def _seal(
    tmp_path: Path,
    archive_checker=lambda _path: None,
    migration_reader=lambda _path: MIGRATION,
) -> dict:
    return seal.seal_bundle(
        dump=tmp_path / "candidate.dump",
        sidecar=tmp_path / "candidate.dump.sha256",
        metadata=tmp_path / "candidate.meta.json",
        expected_migration=MIGRATION,
        release_id="20260715T200000Z-fe3871c438ff",
        expected_app_sha=HEAD,
        archive_checker=archive_checker,
        migration_reader=migration_reader,
    )


def test_seal_publishes_private_verified_sidecars_but_never_release_pass(tmp_path: Path) -> None:
    dump = _private_dump(tmp_path)
    calls: list[Path] = []

    result = _seal(tmp_path, archive_checker=calls.append)

    sidecar = tmp_path / "candidate.dump.sha256"
    metadata = tmp_path / "candidate.meta.json"
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    assert calls == [dump]
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {dump.name}\n"
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    assert stat.S_IMODE(metadata.stat().st_mode) == 0o600
    assert json.loads(metadata.read_text(encoding="utf-8")) == result
    assert result["bundle"]["dump_sha256"] == digest
    assert result["archive_migration_max"] == MIGRATION
    assert result["archive_migration_verified"] is True
    assert result["database_contacted"] is False
    assert result["release_gate_eligible"] is False
    assert "restore_rehearsal_not_attested" in result["release_gate_blockers"]


def test_seal_rejects_mislabeled_archive_before_publishing_sidecars(tmp_path: Path) -> None:
    _private_dump(tmp_path)

    with pytest.raises(seal.SealError, match="migration does not match"):
        _seal(
            tmp_path,
            migration_reader=lambda _path: "259_vkpi_dealer_reviewed_evidence.sql",
        )

    assert not (tmp_path / "candidate.dump.sha256").exists()
    assert not (tmp_path / "candidate.meta.json").exists()


def test_archive_failure_and_metadata_failure_leave_no_success_looking_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _private_dump(tmp_path)

    def archive_failure(_path: Path) -> None:
        raise seal.SealError("fixture archive failure")

    with pytest.raises(seal.SealError, match="fixture"):
        _seal(tmp_path, archive_checker=archive_failure)
    assert not (tmp_path / "candidate.dump.sha256").exists()
    assert not (tmp_path / "candidate.meta.json").exists()

    original = seal._write_exclusive

    def fail_metadata(path: Path, content: bytes) -> None:
        if path.name.endswith(".meta.json"):
            raise seal.SealError("fixture metadata failure")
        original(path, content)

    monkeypatch.setattr(seal, "_write_exclusive", fail_metadata)
    with pytest.raises(seal.SealError, match="metadata"):
        _seal(tmp_path)
    assert not (tmp_path / "candidate.dump.sha256").exists()
    assert not (tmp_path / "candidate.meta.json").exists()


def test_seal_refuses_public_dump_and_evidence_overwrite(tmp_path: Path) -> None:
    dump = _private_dump(tmp_path)
    dump.chmod(0o644)
    with pytest.raises(Exception, match="private"):
        _seal(tmp_path)

    dump.chmod(0o600)
    (tmp_path / "candidate.dump.sha256").write_text("do not replace\n", encoding="utf-8")
    with pytest.raises(seal.SealError, match="overwrite"):
        _seal(tmp_path)
    assert (tmp_path / "candidate.dump.sha256").read_text(encoding="utf-8") == "do not replace\n"
