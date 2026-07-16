from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.ops import staging_soak_collectors as collectors


def test_health_url_allows_https_default_port_and_rejects_unsafe_targets() -> None:
    assert collectors._validate_health_url("https://staging.example.test/health")
    assert collectors._validate_health_url("http://127.0.0.1:8001/health")
    for value in (
        "http://staging.example.test/health",
        "https://user:password@staging.example.test/health",
        "https://staging.example.test/health?token=secret",
        "https://staging.example.test/other",
    ):
        with pytest.raises(collectors.CollectionError, match="health_url_invalid"):
            collectors._validate_health_url(value)


def test_environment_fingerprint_is_content_bound_without_persisting_values(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    secret = "do-not-persist-this-secret"
    env_file.write_text(f"DATABASE_URL={secret}\n", encoding="utf-8")
    env_file.chmod(0o640)

    result = collectors.collect_environment_fingerprint(env_file)

    assert len(result["content_sha256"]) == 64
    assert result["bytes"] == env_file.stat().st_size
    assert secret not in json.dumps(result)

    env_file.chmod(0o644)
    with pytest.raises(collectors.CollectionError, match="environment_file_unsafe"):
        collectors.collect_environment_fingerprint(env_file)


def test_database_collector_is_read_only_and_uses_valid_filtered_oldest_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://fixture.invalid/vkpi\n", encoding="utf-8")
    queries: list[str] = []

    class Cursor:
        current = ""

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, query: str) -> None:
            self.current = query
            queries.append(query)

        def fetchone(self) -> tuple[Any, ...]:
            if "pg_stat_activity" in self.current:
                return ("viltrox2", "260_vkpi_dealer_map_management.sql", 1, 2, 0)
            if "pg_locks" in self.current:
                return (0,)
            if "to_regclass" in self.current:
                return ("apify_jobs",)
            if "FROM apify_jobs" in self.current:
                return (3, 12.5, 1)
            raise AssertionError(self.current)

    class Context:
        def __enter__(self) -> "Context":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    class Connection(Context):
        def transaction(self) -> Context:
            return Context()

        def cursor(self) -> Cursor:
            return Cursor()

    class FakePsycopg:
        @staticmethod
        def connect(url: str, *, connect_timeout: int) -> Connection:
            assert url == "postgresql://fixture.invalid/vkpi"
            assert connect_timeout == 5
            return Connection()

    monkeypatch.setitem(sys.modules, "psycopg", FakePsycopg)

    result = collectors.collect_database(env_file, timeout=5.0)

    assert queries[0] == "SET TRANSACTION READ ONLY"
    queue_query = next(query for query in queries if "FROM apify_jobs" in query)
    assert "MIN(created_at) FILTER (WHERE status = 'queued')" in queue_query
    assert result["transaction_mode"] == "read_only"
    assert result["queue"] == {
        "present": True,
        "queued": 3,
        "oldest_queued_age_seconds": 12.5,
        "failed_or_triage": 1,
    }
    assert "viltrox2" not in json.dumps(result)


def test_journal_collector_never_persists_raw_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-persist-this-journal-message"
    rows = "\n".join(
        [
            json.dumps({"__CURSOR": "cursor-2", "PRIORITY": "3", "MESSAGE": secret}),
            json.dumps({"__CURSOR": "cursor-3", "PRIORITY": "6", "MESSAGE": secret}),
        ]
    )
    monkeypatch.setattr(collectors.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        collectors.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=rows),
    )

    result, cursor = collectors.collect_journal(
        ("viltrox-2.0-test.service",),
        cursor="cursor-1",
        timeout=5.0,
    )

    assert cursor == "cursor-3"
    assert result["entries"] == 2
    assert result["priority_error_entries"] == 1
    assert result["raw_messages_persisted"] is False
    assert secret not in json.dumps(result)
