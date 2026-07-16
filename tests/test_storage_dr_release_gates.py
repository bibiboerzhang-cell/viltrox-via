from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

import pytest

from scripts.ops import postgres_restore_rehearsal as restore_gate
from scripts.ops import r2_release_canary as r2_gate


ROOT = Path(__file__).parents[1]
RELEASE_ID = "20260715T120000Z-fe3871c438ff"
APP_SHA = "fe3871c438ff9de8884589e052d8dd8b82b94b83"


class FakeClientError(RuntimeError):
    def __init__(self, status: int, code: str) -> None:
        self.response = {
            "Error": {"Code": code, "Message": "redacted fixture"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }
        super().__init__("fixture client error")


class FakeBody(io.BytesIO):
    pass


class FakeR2Client:
    def __init__(self, *, corrupt_full_get: bool = False) -> None:
        self.corrupt_full_get = corrupt_full_get
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.calls: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, Metadata: dict[str, str], **_kwargs: Any) -> None:
        self.calls.append("put")
        self.objects[(Bucket, Key)] = (bytes(Body), dict(Metadata))

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("head")
        value = self.objects.get((Bucket, Key))
        if value is None:
            raise FakeClientError(404, "NoSuchKey")
        body, metadata = value
        return {"ContentLength": len(body), "Metadata": metadata}

    def get_object(self, *, Bucket: str, Key: str, Range: str = "") -> dict[str, Any]:
        self.calls.append("range_get" if Range else "full_get")
        body, _metadata = self.objects[(Bucket, Key)]
        if Range:
            match = Range.removeprefix("bytes=").split("-", 1)
            selected = body[int(match[0]) : int(match[1]) + 1]
        else:
            selected = b"corrupt" if self.corrupt_full_get else body
        return {"Body": FakeBody(selected)}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.calls.append("delete")
        self.objects.pop((Bucket, Key), None)


def _r2_settings() -> r2_gate.Settings:
    return r2_gate.Settings(
        endpoint="https://account-id.r2.cloudflarestorage.com",
        access_key="do-not-persist-access",
        secret_key="do-not-persist-secret",
        bucket="private-vkpi-bucket",
    )


def _r2_key() -> str:
    return "vkpi/release-canary/20260715T120000Z-0123456789abcdef0123456789abcdef.bin"


def test_r2_canary_proves_put_head_range_full_delete_and_absence() -> None:
    client = FakeR2Client()
    payload = bytes(index % 251 for index in range(r2_gate.PAYLOAD_BYTES))

    result = r2_gate.run_canary(
        client=client,
        settings=_r2_settings(),
        payload=payload,
        key=_r2_key(),
        release_id=RELEASE_ID,
        expected_app_sha=APP_SHA,
    )

    assert result["status"] == "passed"
    assert result["release_id"] == RELEASE_ID
    assert result["expected_app_sha"] == APP_SHA
    assert result["release_gate_eligible"] is False
    assert result["operations"] == {
        "put": True,
        "head": True,
        "range_get": True,
        "full_get": True,
        "delete": True,
        "absence_confirmed": True,
    }
    assert client.calls == ["put", "head", "range_get", "full_get", "delete", "head"]
    assert client.objects == {}
    evidence = json.dumps(result, sort_keys=True)
    assert "do-not-persist-access" not in evidence
    assert "do-not-persist-secret" not in evidence
    assert "private-vkpi-bucket" not in evidence


def test_r2_canary_cleanup_runs_after_full_download_mismatch() -> None:
    client = FakeR2Client(corrupt_full_get=True)
    payload = b"x" * r2_gate.PAYLOAD_BYTES

    with pytest.raises(r2_gate.CanaryError, match="full_get") as raised:
        r2_gate.run_canary(
            client=client,
            settings=_r2_settings(),
            payload=payload,
            key=_r2_key(),
            release_id=RELEASE_ID,
            expected_app_sha=APP_SHA,
        )

    assert raised.value.category == "sha256_or_byte_mismatch"
    assert raised.value.operations["delete"] is True
    assert raised.value.operations["absence_confirmed"] is True
    assert client.objects == {}


def test_r2_canary_requires_double_opt_in_before_client_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(r2_gate.CONFIRM_ENV, raising=False)
    constructed = False

    def forbidden_factory(_settings: r2_gate.Settings) -> Any:
        nonlocal constructed
        constructed = True
        raise AssertionError("client must not be created")

    result = r2_gate.main(
        [
            "--execute",
            "--artifact",
            str(tmp_path / "receipt.json"),
            "--release-id",
            RELEASE_ID,
            "--expected-app-sha",
            APP_SHA,
        ],
        client_factory=forbidden_factory,
    )

    assert result == 2
    assert constructed is False
    assert not (tmp_path / "receipt.json").exists()


def test_r2_endpoint_must_be_cloudflare_https() -> None:
    base = {
        "R2_ENDPOINT": "https://account-id.r2.cloudflarestorage.com",
        "R2_ACCESS_KEY_ID": "access",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET_NAME": "valid-bucket",
    }
    settings = r2_gate.settings_from_environment(base)
    assert settings.endpoint == base["R2_ENDPOINT"]
    assert "access" not in repr(settings)
    assert "secret" not in repr(settings)
    with pytest.raises(r2_gate.CanaryError, match="invalid_cloudflare_r2_endpoint"):
        r2_gate.settings_from_environment({**base, "R2_ENDPOINT": "https://attacker.invalid"})


class FakeRestoreRunner:
    def __init__(self, *, fail_restore: bool = False) -> None:
        self.fail_restore = fail_restore
        self.calls: list[str] = []

    def check_archive(self, _dump: Path) -> None:
        self.calls.append("archive_list")

    def restore(self, _dump: Path, database: str) -> None:
        assert restore_gate.DATABASE_RE.fullmatch(database)
        self.calls.append("pg_restore")
        if self.fail_restore:
            raise restore_gate.RestoreError("pg_restore", "nonzero_exit")


class FakeDatabaseOps:
    def __init__(self, *, migration: str, business_rows: int = 10) -> None:
        self.migration = migration
        self.business_rows = business_rows
        self.databases: set[str] = set()
        self.calls: list[str] = []

    def assert_local_admin(self) -> None:
        self.calls.append("local_admin")

    def database_exists(self, database: str) -> bool:
        self.calls.append("exists")
        return database in self.databases

    def create_database(self, database: str) -> None:
        self.calls.append("create")
        self.databases.add(database)

    def inspect_database(self, database: str, anchors: Sequence[str]) -> dict[str, Any]:
        self.calls.append("inspect")
        assert database in self.databases
        return {
            "transaction_mode": "read_only",
            "migration_max": self.migration,
            "anchors": {
                table: {
                    "row_count": 258 if table == "schema_migrations" else self.business_rows,
                    "primary_key_columns": ["id"] if table != "schema_migrations" else ["version_key"],
                    "primary_key_sample_limit": 256,
                    "primary_key_sample_sha256": "a" * 64,
                }
                for table in anchors
            },
        }

    def drop_database(self, database: str) -> None:
        self.calls.append("drop")
        self.databases.discard(database)


def _dump_bundle(tmp_path: Path) -> tuple[Path, Path]:
    dump = tmp_path / "prod-db.dump"
    dump.write_bytes(b"PGDMP-offline-fixture")
    sidecar = tmp_path / "prod-db.dump.sha256"
    sidecar.write_text(f"{hashlib.sha256(dump.read_bytes()).hexdigest()}  prod-db.dump\n", encoding="ascii")
    dump.chmod(0o600)
    sidecar.chmod(0o600)
    return dump, sidecar


def test_restore_bundle_must_be_private_and_owned_by_executor(tmp_path: Path) -> None:
    dump, sidecar = _dump_bundle(tmp_path)
    dump.chmod(0o644)

    with pytest.raises(restore_gate.RestoreError, match="file_not_private_owned_regular"):
        restore_gate.verify_bundle(dump, sidecar)


def test_restore_rehearsal_executes_restore_reads_anchors_and_drops_database(tmp_path: Path) -> None:
    dump, sidecar = _dump_bundle(tmp_path)
    expected = "258_vkpi_llm_budget_reservations.sql"
    database = "vkpi_restore_rehearsal_20260715t120000z_0123456789abcdef"
    db = FakeDatabaseOps(migration=expected)
    runner = FakeRestoreRunner()

    result = restore_gate.run_rehearsal(
        dump=dump,
        sidecar=sidecar,
        expected_migration=expected,
        anchors=restore_gate.DEFAULT_ANCHORS,
        database_ops=db,
        restore_runner=runner,
        database=database,
        release_id=RELEASE_ID,
        expected_app_sha=APP_SHA,
    )

    assert result["status"] == "passed"
    assert result["release_id"] == RELEASE_ID
    assert result["expected_app_sha"] == APP_SHA
    assert result["release_gate_eligible"] is False
    assert result["inspection"]["transaction_mode"] == "read_only"
    assert result["inspection"]["migration_max"] == expected
    assert result["operations"] == {
        "dump_pinned": True,
        "archive_list": True,
        "local_admin_identity": True,
        "database_created": True,
        "pg_restore": True,
        "post_restore_dump_reverified": True,
        "read_only_anchors": True,
        "database_dropped": True,
        "absence_confirmed": True,
        "pinned_dump_removed": True,
    }
    assert runner.calls == ["archive_list", "pg_restore"]
    assert "drop" in db.calls
    assert db.databases == set()


@pytest.mark.parametrize(
    ("migration", "business_rows", "failure_category"),
    [
        ("257_vkpi_dealer_event_candidate_staging.sql", 10, "migration_max_mismatch"),
        ("258_vkpi_llm_budget_reservations.sql", 0, "all_business_anchors_empty"),
    ],
)
def test_restore_rehearsal_failure_still_drops_unique_database(
    tmp_path: Path, migration: str, business_rows: int, failure_category: str
) -> None:
    dump, sidecar = _dump_bundle(tmp_path)
    expected = "258_vkpi_llm_budget_reservations.sql"
    database = "vkpi_restore_rehearsal_20260715t120000z_0123456789abcdef"
    db = FakeDatabaseOps(migration=migration, business_rows=business_rows)

    with pytest.raises(restore_gate.RestoreError) as raised:
        restore_gate.run_rehearsal(
            dump=dump,
            sidecar=sidecar,
            expected_migration=expected,
            anchors=restore_gate.DEFAULT_ANCHORS,
            database_ops=db,
            restore_runner=FakeRestoreRunner(),
            database=database,
            release_id=RELEASE_ID,
            expected_app_sha=APP_SHA,
        )

    assert raised.value.category == failure_category
    assert raised.value.operations["database_dropped"] is True
    assert raised.value.operations["absence_confirmed"] is True
    assert db.databases == set()


def test_restore_rehearsal_rejects_ambient_routing_and_is_inert_without_execute(
    tmp_path: Path,
) -> None:
    dump, sidecar = _dump_bundle(tmp_path)
    script = ROOT / "scripts" / "ops" / "postgres_restore_rehearsal.py"
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://user:do-not-print@remote.invalid/production"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dump",
            str(dump),
            "--sha256-file",
            str(sidecar),
            "--expected-migration-max",
            "258_vkpi_llm_budget_reservations.sql",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_executed"
    assert payload["postgres_contacted"] is False
    assert "do-not-print" not in result.stdout
    assert "do-not-print" not in result.stderr


def test_restore_preflight_verifies_bundle_and_attestation_without_database_contact(
    tmp_path: Path,
) -> None:
    dump, sidecar = _dump_bundle(tmp_path)
    binding = restore_gate.ClusterBinding(
        data_root=Path("/var/lib/vkpi-restore-rehearsal-clusters/fixture/data"),
        socket_dir=Path("/var/lib/vkpi-restore-rehearsal-clusters/fixture/socket"),
        port=25432,
        owner_user="vkpi-rehearsal",
        system_identifier="12345678901234567890",
        attestation_sha256="a" * 64,
    )
    runner = FakeRestoreRunner()

    result = restore_gate.preflight_rehearsal(
        dump=dump,
        sidecar=sidecar,
        expected_migration="258_vkpi_llm_budget_reservations.sql",
        binding=binding,
        restore_runner=runner,
        release_id=RELEASE_ID,
        expected_app_sha=APP_SHA,
    )

    assert result["status"] == "ready_for_diagnostic_restore"
    assert result["postgres_contacted"] is False
    assert result["database_created"] is False
    assert result["archive_list_verified"] is True
    assert result["release_gate_eligible"] is False
    assert "restore_not_executed" in result["release_gate_blockers"]
    assert runner.calls == ["archive_list"]


def test_restore_preflight_without_root_attestation_fails_before_archive_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump, sidecar = _dump_bundle(tmp_path)
    artifact = tmp_path / "preflight.json"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(restore_gate, "_effective_username", lambda: "vkpi-rehearsal")
    monkeypatch.setattr(restore_gate.os, "geteuid", lambda: 501)

    result = restore_gate.main(
        [
            "--preflight",
            "--dump",
            str(dump),
            "--sha256-file",
            str(sidecar),
            "--expected-migration-max",
            "258_vkpi_llm_budget_reservations.sql",
            "--artifact",
            str(artifact),
            "--release-id",
            RELEASE_ID,
            "--expected-app-sha",
            APP_SHA,
        ]
    )

    assert result == 2
    assert not artifact.exists()


def test_restore_subprocess_environment_cannot_inherit_postgres_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in restore_gate.PG_ENV_KEYS:
        monkeypatch.setenv(key, "must-not-survive")
    socket_dir = Path("/var/lib/vkpi-restore-rehearsal-clusters/test/socket")
    env = restore_gate._local_peer_environment(
        socket_dir=socket_dir,
        port=25432,
        user="vkpi-rehearsal",
    )
    assert env["PGHOST"] == str(socket_dir)
    assert env["PGPORT"] == "25432"
    assert env["PGUSER"] == "vkpi-rehearsal"
    assert not (
        (set(env) & set(restore_gate.PG_ENV_KEYS))
        - {"PGHOST", "PGPORT", "PGUSER"}
    )
    assert env["HOME"].startswith("/nonexistent/")
