from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
HELPER = OPS / "pgbouncer_release_map.py"
sys.path.insert(0, str(OPS))

import pgbouncer_release_map as release_map  # noqa: E402


SOURCE = "viltrox2_test_release_11111111111111111111"
TARGET = "viltrox2_test_release_22222222222222222222"
STALE = "viltrox2_test_release_00000000000000000000"
SECRET = "do-not-print-this-password"


def _mapping(database: str, *, host: str = "127.0.0.1", extra: str = "") -> str:
    suffix = f" {extra}" if extra else ""
    return f"{database} = host={host} port=5432 dbname={database}{suffix}"


def _config_bytes(
    databases: list[str] | None = None,
    *,
    newline: bytes = b"\n",
    trailing_newline: bool = True,
) -> bytes:
    mappings = databases or [SOURCE]
    lines = [
        ";; managed production config",
        "[databases]",
        *[_mapping(database) for database in mappings],
        "",
        "[pgbouncer]",
        "listen_addr = 127.0.0.1",
        "listen_port = 6432",
        "auth_file = /etc/pgbouncer/userlist.txt",
        f"; secret-shaped comment stays private: {SECRET}",
    ]
    payload = newline.join(line.encode("utf-8") for line in lines)
    return payload + (newline if trailing_newline else b"")


def _private_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    work = tmp_path / "private"
    work.mkdir(mode=0o700)
    work.chmod(0o700)
    config = work / "pgbouncer.ini"
    backup = work / "pgbouncer.ini.original"
    receipt = work / "pgbouncer-map.json"
    config.write_bytes(_config_bytes())
    config.chmod(0o640)
    return config, backup, receipt


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_inspect_is_read_only_and_emits_only_redacted_mapping_metadata(
    tmp_path: Path,
) -> None:
    config, _, _ = _private_tree(tmp_path)
    before = config.read_bytes()

    result = _run(
        "inspect",
        "--config",
        str(config),
        "--source-db",
        SOURCE,
    )

    assert result.returncode == 0, result.stderr
    assert config.read_bytes() == before
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": release_map.RECEIPT_SCHEMA,
        "operation": "inspect",
        "config_sha256": hashlib.sha256(before).hexdigest(),
        "databases": [SOURCE],
        "mapping_count": 1,
        "mapping_endpoint": "127.0.0.1:5432",
        "database_mapping_credentials_included": False,
    }


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_prepare_is_atomic_private_and_preserves_metadata_and_line_endings(
    tmp_path: Path,
    newline: bytes,
) -> None:
    config, backup, receipt = _private_tree(tmp_path)
    original = _config_bytes([STALE, SOURCE], newline=newline)
    config.write_bytes(original)
    config.chmod(0o640)
    before = config.stat()

    result = release_map.prepare_config(
        config,
        source_database=SOURCE,
        target_database=TARGET,
        backup_path=backup,
        receipt_path=receipt,
    )

    after = config.stat()
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
        before.st_uid,
        before.st_gid,
        stat.S_IMODE(before.st_mode),
    )
    assert release_map.parse_config(config.read_bytes()).databases == (SOURCE, TARGET)
    assert config.read_bytes().replace(newline, b"") == config.read_bytes().replace(
        b"\r\n",
        b"",
    ).replace(b"\n", b"")
    assert STALE.encode() not in config.read_bytes()
    assert f"[pgbouncer]{newline.decode()}".encode() in config.read_bytes()
    assert SECRET.encode() in config.read_bytes()
    assert backup.read_bytes() == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert SECRET not in receipt.read_text(encoding="utf-8")
    assert receipt_payload["config_sha256_before"] == hashlib.sha256(original).hexdigest()
    assert receipt_payload["config_sha256_after"] == hashlib.sha256(
        config.read_bytes()
    ).hexdigest()
    assert receipt_payload["backup_sha256"] == hashlib.sha256(original).hexdigest()
    assert receipt_payload["databases_after"] == [SOURCE, TARGET]
    assert receipt_payload["database_mapping_credentials_included"] is False
    assert result["config_sha256_after"] == receipt_payload["config_sha256_after"]
    assert not list(config.parent.glob(".pgbouncer.ini.release-map-*"))


def test_cli_prepare_verify_restore_recovers_original_bytes_and_metadata(
    tmp_path: Path,
) -> None:
    config, backup, receipt = _private_tree(tmp_path)
    original = _config_bytes([STALE, SOURCE], newline=b"\r\n", trailing_newline=False)
    config.write_bytes(original)
    config.chmod(0o640)
    original_info = config.stat()

    prepared = _run(
        "prepare",
        "--config",
        str(config),
        "--source-db",
        SOURCE,
        "--target-db",
        TARGET,
        "--backup",
        str(backup),
        "--receipt",
        str(receipt),
        "--expected-sha256",
        hashlib.sha256(original).hexdigest(),
    )

    assert prepared.returncode == 0, prepared.stderr
    assert SECRET not in prepared.stdout + prepared.stderr
    prepared_payload = json.loads(prepared.stdout)
    verified = _run(
        "verify",
        "--config",
        str(config),
        "--source-db",
        SOURCE,
        "--target-db",
        TARGET,
        "--expected-sha256",
        prepared_payload["config_sha256_after"],
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["verified"] is True

    restored = _run(
        "restore-original",
        "--config",
        str(config),
        "--backup",
        str(backup),
        "--receipt",
        str(receipt),
    )

    assert restored.returncode == 0, restored.stderr
    assert SECRET not in restored.stdout + restored.stderr
    assert config.read_bytes() == original
    restored_info = config.stat()
    assert (
        restored_info.st_uid,
        restored_info.st_gid,
        stat.S_IMODE(restored_info.st_mode),
    ) == (
        original_info.st_uid,
        original_info.st_gid,
        stat.S_IMODE(original_info.st_mode),
    )
    restored_payload = json.loads(restored.stdout)
    assert restored_payload["restored"] is True
    assert restored_payload["changed"] is True
    second = release_map.restore_original(
        config,
        backup_path=backup,
        receipt_path=receipt,
    )
    assert second["restored"] is True
    assert second["changed"] is False


@pytest.mark.parametrize(
    "body",
    [
        (
            f"[databases]\n{_mapping(SOURCE)}\n"
            f"{_mapping(SOURCE)}\n[pgbouncer]\nlisten_port=6432\n"
        ),
        (
            f"[databases]\n{_mapping(SOURCE)}\n"
            f"[DATABASES]\n{_mapping(TARGET)}\n"
        ),
        (
            "[databases]\n"
            "* = host=127.0.0.1 port=5432 dbname=viltrox2_test\n"
            "[pgbouncer]\nlisten_port=6432\n"
        ),
        (
            f"[databases]\n{_mapping(SOURCE, host='db.internal')}\n"
            "[pgbouncer]\nlisten_port=6432\n"
        ),
        (
            f"[databases]\n{_mapping(SOURCE, extra='password=' + SECRET)}\n"
            "[pgbouncer]\nlisten_port=6432\n"
        ),
        (
            f"%include /etc/pgbouncer/extra.ini\n[databases]\n{_mapping(SOURCE)}\n"
            "[pgbouncer]\nlisten_port=6432\n"
        ),
    ],
    ids=[
        "duplicate-alias",
        "duplicate-section",
        "wildcard",
        "remote-host",
        "credential-option",
        "include-directive",
    ],
)
def test_unsafe_database_entries_fail_closed_without_leaking_values(
    tmp_path: Path,
    body: str,
) -> None:
    config, _, _ = _private_tree(tmp_path)
    config.write_text(body, encoding="utf-8")
    before = config.read_bytes()

    result = _run("inspect", "--config", str(config), "--source-db", SOURCE)

    assert result.returncode == 2
    assert config.read_bytes() == before
    assert SECRET not in result.stdout + result.stderr
    assert not result.stdout


def test_prepare_rejects_symlinks_hardlinks_and_public_evidence_parent(
    tmp_path: Path,
) -> None:
    config, backup, receipt = _private_tree(tmp_path)
    linked_config = config.parent / "linked.ini"
    linked_config.symlink_to(config)
    with pytest.raises(release_map.PgbouncerMapError, match="single-link"):
        release_map.inspect_config(linked_config)

    hardlink = config.parent / "hardlink.ini"
    os.link(config, hardlink)
    with pytest.raises(release_map.PgbouncerMapError, match="single-link"):
        release_map.inspect_config(config)
    hardlink.unlink()

    public = tmp_path / "public"
    public.mkdir(mode=0o777)
    public.chmod(0o777)
    with pytest.raises(release_map.PgbouncerMapError, match="group/world writable"):
        release_map.prepare_config(
            config,
            source_database=SOURCE,
            target_database=TARGET,
            backup_path=public / backup.name,
            receipt_path=receipt,
        )
    assert release_map.parse_config(config.read_bytes()).databases == (SOURCE,)


def test_prepare_rejects_existing_backup_or_receipt_without_mutating_config(
    tmp_path: Path,
) -> None:
    config, backup, receipt = _private_tree(tmp_path)
    before = config.read_bytes()
    backup.symlink_to(config)

    with pytest.raises(release_map.PgbouncerMapError, match="already exists"):
        release_map.prepare_config(
            config,
            source_database=SOURCE,
            target_database=TARGET,
            backup_path=backup,
            receipt_path=receipt,
        )

    assert config.read_bytes() == before
    assert not receipt.exists()


def test_prepare_expected_hash_is_a_prewrite_compare_and_swap(tmp_path: Path) -> None:
    config, backup, receipt = _private_tree(tmp_path)
    before = config.read_bytes()

    with pytest.raises(release_map.PgbouncerMapError, match="changed before prepare"):
        release_map.prepare_config(
            config,
            source_database=SOURCE,
            target_database=TARGET,
            backup_path=backup,
            receipt_path=receipt,
            expected_sha256="0" * 64,
        )

    assert config.read_bytes() == before
    assert not backup.exists()
    assert not receipt.exists()


def test_restore_refuses_unrecognized_post_prepare_change(tmp_path: Path) -> None:
    config, backup, receipt = _private_tree(tmp_path)
    release_map.prepare_config(
        config,
        source_database=SOURCE,
        target_database=TARGET,
        backup_path=backup,
        receipt_path=receipt,
    )
    changed = config.read_bytes().replace(b"listen_port = 6432", b"listen_port = 6433")
    config.write_bytes(changed)
    config.chmod(0o640)

    with pytest.raises(release_map.PgbouncerMapError, match="changed after prepare"):
        release_map.restore_original(
            config,
            backup_path=backup,
            receipt_path=receipt,
        )

    assert config.read_bytes() == changed


def test_mixed_newlines_fail_closed(tmp_path: Path) -> None:
    config, _, _ = _private_tree(tmp_path)
    config.write_bytes(
        (
            f"[databases]\r\n{_mapping(SOURCE)}\n"
            "[pgbouncer]\r\nlisten_port = 6432\r\n"
        ).encode()
    )

    with pytest.raises(release_map.PgbouncerMapError, match="mixed line endings"):
        release_map.inspect_config(config)


def test_config_backup_and_receipt_are_bounded_to_one_mibibyte(
    tmp_path: Path,
) -> None:
    config, backup, receipt = _private_tree(tmp_path)
    config.write_bytes(
        _config_bytes()
        + b";"
        + b"x" * release_map.MAX_FILE_BYTES
        + b"\n"
    )
    with pytest.raises(release_map.PgbouncerMapError, match="1 MiB"):
        release_map.inspect_config(config)

    config.write_bytes(_config_bytes())
    release_map.prepare_config(
        config,
        source_database=SOURCE,
        target_database=TARGET,
        backup_path=backup,
        receipt_path=receipt,
    )
    backup.write_bytes(b"x" * (release_map.MAX_FILE_BYTES + 1))
    backup.chmod(0o600)
    with pytest.raises(release_map.PgbouncerMapError, match="1 MiB"):
        release_map.restore_original(
            config,
            backup_path=backup,
            receipt_path=receipt,
        )


def test_probe_uses_only_validated_loopback_pool_url_and_returns_redacted_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "runtime"
    work.mkdir(mode=0o700)
    env_path = work / ".env"
    pool_url = (
        f"postgresql://app:{SECRET}@127.0.0.1:6432/{TARGET}"
        "?sslmode=disable&application_name=vkpi"
    )
    env_path.write_text(
        f"DATABASE_POOL_URL='{pool_url}'\nOTHER_SECRET={SECRET}\n",
        encoding="utf-8",
    )
    env_path.chmod(0o640)
    calls: list[object] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str) -> None:
            calls.append(query)

        def fetchone(self) -> tuple[str]:
            return (TARGET,)

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

    class Psycopg:
        @staticmethod
        def connect(value: str, *, connect_timeout: int) -> Connection:
            assert value == pool_url
            assert connect_timeout == 5
            calls.append("connected")
            return Connection()

    monkeypatch.setattr(release_map, "_load_psycopg", lambda: Psycopg)

    result = release_map.probe_pool(env_path, expected_database=TARGET)

    assert result == {
        "schema_version": release_map.RECEIPT_SCHEMA,
        "operation": "probe",
        "connected": True,
        "database_name": TARGET,
        "mapping_endpoint": "127.0.0.1:6432",
        "credentials_included": False,
    }
    assert calls == [
        "connected",
        "SET statement_timeout = 5000",
        "SELECT current_database()",
    ]
    assert SECRET not in json.dumps(result)


def test_probe_can_prove_previous_alias_with_current_env_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "runtime"
    work.mkdir(mode=0o700)
    env_path = work / ".env"
    env_path.write_text(
        (
            f"DATABASE_POOL_URL=postgresql://app:{SECRET}@127.0.0.1:6432/{TARGET}"
            "?sslmode=disable\n"
        ),
        encoding="utf-8",
    )
    env_path.chmod(0o640)
    observed: list[str] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str) -> None:
            observed.append(query)

        def fetchone(self) -> tuple[str]:
            return (SOURCE,)

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

    class Psycopg:
        @staticmethod
        def connect(value: str, *, connect_timeout: int) -> Connection:
            assert value == (
                f"postgresql://app:{SECRET}@127.0.0.1:6432/{SOURCE}"
                "?sslmode=disable"
            )
            assert connect_timeout == 5
            return Connection()

    monkeypatch.setattr(release_map, "_load_psycopg", lambda: Psycopg)

    result = release_map.probe_pool(env_path, expected_database=SOURCE)

    assert result["connected"] is True
    assert result["database_name"] == SOURCE
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(
    "pool_url",
    [
        f"postgresql://app:{SECRET}@db.internal:6432/{TARGET}",
        f"postgresql://app:{SECRET}@127.0.0.1:5432/{TARGET}",
        f"postgresql://app:{SECRET}@127.0.0.1:6432/not-a-safe-database",
        (
            f"postgresql://app:{SECRET}@127.0.0.1:6432/{TARGET}"
            "?host=db.internal"
        ),
    ],
)
def test_probe_rejects_wrong_endpoint_or_database_without_leaking_url(
    tmp_path: Path,
    pool_url: str,
) -> None:
    work = tmp_path / "runtime"
    work.mkdir(mode=0o700)
    env_path = work / ".env"
    env_path.write_text(f"DATABASE_POOL_URL={pool_url}\n", encoding="utf-8")
    env_path.chmod(0o640)

    result = _run(
        "probe",
        "--env-file",
        str(env_path),
        "--expected-db",
        TARGET,
    )

    assert result.returncode == 2
    assert SECRET not in result.stdout + result.stderr
    assert pool_url not in result.stdout + result.stderr
