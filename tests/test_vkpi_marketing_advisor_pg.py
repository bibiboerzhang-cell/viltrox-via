"""Real-Postgres contract for the Marketing Advisor message window.

The production repository uses a descending inner LIMIT to select the newest
messages, then an ascending outer order for conversation rendering.  TEMP
tables shadow the production names, so this test exercises the exact compat
SQL on PostgreSQL without reading or mutating business rows.
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from pathlib import Path

import pytest

from app.domains.advisor import repository
from app.domains.advisor.scope import AdvisorScope


pytestmark = pytest.mark.pg


def _request_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create_claim_schema(pg_dsn: str, schema: str) -> None:
    import psycopg
    from psycopg import sql

    root = repository.__file__
    migration = (
        Path(root).resolve().parents[4]
        / "migrations"
        / "252_vkpi_advisor_turn_claims.sql"
    ).read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
        )
        conn.execute(
            """
            CREATE TABLE vkpi_advisor_threads (
                id BIGSERIAL PRIMARY KEY,
                thread_uid TEXT NOT NULL,
                organization_id BIGINT NOT NULL,
                staff_id BIGINT NOT NULL,
                deleted_at TIMESTAMPTZ,
                UNIQUE (organization_id, staff_id, thread_uid)
            );
            CREATE TABLE vkpi_advisor_messages (
                id BIGSERIAL PRIMARY KEY,
                message_uid TEXT NOT NULL,
                organization_id BIGINT NOT NULL,
                staff_id BIGINT NOT NULL,
                thread_uid TEXT NOT NULL,
                role TEXT NOT NULL,
                client_request_id TEXT NOT NULL DEFAULT '',
                deleted_at TIMESTAMPTZ
            );
            CREATE TABLE vkpi_advisor_action_drafts (
                id BIGSERIAL PRIMARY KEY,
                organization_id BIGINT NOT NULL,
                staff_id BIGINT NOT NULL,
                thread_uid TEXT NOT NULL,
                source_message_uid TEXT NOT NULL
            );
            """
        )
        conn.execute(migration)
        conn.execute(
            "INSERT INTO vkpi_advisor_threads "
            "(thread_uid, organization_id, staff_id) VALUES ('thread-claim', 901, 902)"
        )


def _drop_claim_schema(pg_dsn: str, schema: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _claim_connection(pg_dsn: str, schema: str):
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    raw.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    raw.commit()
    return PostgresCompatConnection(raw, pool=None)


def test_latest_message_window_is_chronological_on_postgres(
    pg_compat, monkeypatch: pytest.MonkeyPatch
) -> None:
    pg_compat.execute(
        """
        CREATE TEMP TABLE vkpi_advisor_threads (
            id BIGSERIAL PRIMARY KEY,
            thread_uid TEXT NOT NULL,
            organization_id BIGINT NOT NULL,
            staff_id BIGINT NOT NULL,
            deleted_at TIMESTAMPTZ
        ) ON COMMIT PRESERVE ROWS
        """
    )
    pg_compat.execute(
        """
        CREATE TEMP TABLE vkpi_advisor_messages (
            id BIGSERIAL PRIMARY KEY,
            message_uid TEXT NOT NULL,
            organization_id BIGINT NOT NULL,
            staff_id BIGINT NOT NULL,
            thread_uid TEXT NOT NULL,
            role TEXT NOT NULL,
            content_text TEXT NOT NULL,
            deleted_at TIMESTAMPTZ
        ) ON COMMIT PRESERVE ROWS
        """
    )
    pg_compat.execute(
        "INSERT INTO vkpi_advisor_threads "
        "(thread_uid, organization_id, staff_id) VALUES (?,?,?)",
        ("thread-pg-window", 901, 902),
    )
    for ordinal in range(1, 8):
        pg_compat.execute(
            "INSERT INTO vkpi_advisor_messages "
            "(message_uid, organization_id, staff_id, thread_uid, role, content_text) "
            "VALUES (?,?,?,?,?,?)",
            (
                f"message-{ordinal}",
                901,
                902,
                "thread-pg-window",
                "user" if ordinal % 2 else "assistant",
                f"content-{ordinal}",
            ),
        )
    pg_compat.execute(
        "UPDATE vkpi_advisor_messages SET deleted_at=NOW() WHERE message_uid=?",
        ("message-6",),
    )

    monkeypatch.setattr(repository, "get_conn", lambda: pg_compat)
    monkeypatch.setattr(repository, "table_exists", lambda name: name == "vkpi_advisor_threads")
    scope = AdvisorScope(organization_id=901, staff_id=902, user_id=903)

    rows = repository.list_messages(scope, "thread-pg-window", limit=3)

    assert [row["message_uid"] for row in rows] == ["message-4", "message-5", "message-7"]
    assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)


def test_memory_retention_window_uses_real_postgres_timestamps(
    pg_compat, monkeypatch: pytest.MonkeyPatch
) -> None:
    pg_compat.execute(
        """
        CREATE TEMP TABLE vkpi_advisor_memory_settings (
            organization_id BIGINT NOT NULL,
            staff_id BIGINT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            retention_days INTEGER NOT NULL DEFAULT 180,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (organization_id, staff_id)
        ) ON COMMIT PRESERVE ROWS
        """
    )
    pg_compat.execute(
        """
        CREATE TEMP TABLE vkpi_advisor_memory_candidates (
            id BIGSERIAL PRIMARY KEY,
            candidate_uid TEXT NOT NULL,
            organization_id BIGINT NOT NULL,
            staff_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ
        ) ON COMMIT PRESERVE ROWS
        """
    )
    pg_compat.execute(
        """
        CREATE TEMP TABLE vkpi_advisor_memory_facts (
            id BIGSERIAL PRIMARY KEY,
            fact_uid TEXT NOT NULL,
            organization_id BIGINT NOT NULL,
            staff_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ
        ) ON COMMIT PRESERVE ROWS
        """
    )
    pg_compat.execute(
        "INSERT INTO vkpi_advisor_memory_settings "
        "(organization_id, staff_id, retention_days) VALUES (?,?,?)",
        (901, 902, 30),
    )
    pg_compat.execute(
        "INSERT INTO vkpi_advisor_memory_candidates "
        "(candidate_uid, organization_id, staff_id, created_at) VALUES "
        "('candidate-old',901,902,NOW()-INTERVAL '31 days'),"
        "('candidate-fresh',901,902,NOW()-INTERVAL '29 days')"
    )
    pg_compat.execute(
        "INSERT INTO vkpi_advisor_memory_facts "
        "(fact_uid, organization_id, staff_id, updated_at) VALUES "
        "('fact-old',901,902,NOW()-INTERVAL '31 days'),"
        "('fact-fresh',901,902,NOW()-INTERVAL '29 days')"
    )

    monkeypatch.setattr(repository, "get_conn", lambda: pg_compat)
    monkeypatch.setattr(repository, "table_exists", lambda name: name == "vkpi_advisor_threads")
    scope = AdvisorScope(organization_id=901, staff_id=902, user_id=903)

    snapshot = repository.get_memory(scope)

    assert [row["candidate_uid"] for row in snapshot["candidates"]] == ["candidate-fresh"]
    assert [row["fact_uid"] for row in snapshot["facts"]] == ["fact-fresh"]
    assert snapshot["retention_policy"]["mode"] == "read_window"
    assert snapshot["retention_policy"]["physical_delete_performed"] is False
    assert pg_compat.execute("SELECT COUNT(*) FROM vkpi_advisor_memory_candidates").fetchone()[0] == 2
    assert pg_compat.execute("SELECT COUNT(*) FROM vkpi_advisor_memory_facts").fetchone()[0] == 2


def test_real_postgres_concurrent_advisor_claim_has_one_winner(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = f"vkpi_advisor_claim_{uuid.uuid4().hex}"
    _create_claim_schema(pg_dsn, schema)
    first = _claim_connection(pg_dsn, schema)
    second = _claim_connection(pg_dsn, schema)
    scope = AdvisorScope(organization_id=901, staff_id=902, user_id=903)
    barrier = threading.Barrier(2)
    outcomes: list[dict[str, object]] = []
    errors: list[BaseException] = []
    connection_by_thread = {"claim-first": first, "claim-second": second}
    monkeypatch.setattr(
        repository,
        "get_conn",
        lambda: connection_by_thread[threading.current_thread().name],
    )
    monkeypatch.setattr(
        repository,
        "table_exists",
        lambda name: name in {"vkpi_advisor_threads", "vkpi_advisor_turn_claims"},
    )

    def run_claim() -> None:
        try:
            barrier.wait(timeout=5)
            outcomes.append(
                repository.claim_turn_request(
                    scope,
                    "thread-claim",
                    "concurrent-request",
                    request_sha256=_request_hash("same-content"),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=run_claim, name="claim-first"),
        threading.Thread(target=run_claim, name="claim-second"),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(str(item["status"]) for item in outcomes) == ["acquired", "in_progress"]

        winner = next(item for item in outcomes if item["status"] == "acquired")
        holder = {"conn": first}
        monkeypatch.setattr(repository, "get_conn", lambda: holder["conn"])
        repository.mark_turn_provider_started(
            scope,
            "thread-claim",
            "concurrent-request",
            str(winner["claim_token"]),
            provider_binding="openai/gpt-5.4-mini",
        )
        holder["conn"] = second
        with pytest.raises(repository.AdvisorConflict, match="not eligible"):
            repository.mark_turn_provider_started(
                scope,
                "thread-claim",
                "concurrent-request",
                str(winner["claim_token"]),
                provider_binding="openai/gpt-5.4-mini",
            )
        state = second.execute(
            "SELECT state, provider_attempted FROM vkpi_advisor_turn_claims "
            "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=?",
            (901, 902, "thread-claim", "concurrent-request"),
        ).fetchone()
        second.commit()
        assert state[0] == "provider_started"
        assert bool(state[1]) is True
    finally:
        first.close()
        second.close()
        _drop_claim_schema(pg_dsn, schema)


def test_real_postgres_advisor_claim_recovery_is_conservative(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = f"vkpi_advisor_recovery_{uuid.uuid4().hex}"
    _create_claim_schema(pg_dsn, schema)
    conn = _claim_connection(pg_dsn, schema)
    scope = AdvisorScope(organization_id=901, staff_id=902, user_id=903)
    monkeypatch.setattr(repository, "get_conn", lambda: conn)
    monkeypatch.setattr(
        repository,
        "table_exists",
        lambda name: name in {"vkpi_advisor_threads", "vkpi_advisor_turn_claims"},
    )
    try:
        before_provider = repository.claim_turn_request(
            scope,
            "thread-claim",
            "reclaim-before-provider",
            request_sha256=_request_hash("safe-to-reclaim"),
        )
        conn.execute(
            "UPDATE vkpi_advisor_turn_claims SET lease_expires_at=NOW()-INTERVAL '1 second' "
            "WHERE client_request_id=?",
            ("reclaim-before-provider",),
        )
        conn.commit()
        reclaimed = repository.claim_turn_request(
            scope,
            "thread-claim",
            "reclaim-before-provider",
            request_sha256=_request_hash("safe-to-reclaim"),
        )
        assert before_provider["claim_token"] != reclaimed["claim_token"]
        assert reclaimed["status"] == "acquired"
        assert reclaimed["lease_reclaimed_before_provider"] is True

        attempted = repository.claim_turn_request(
            scope,
            "thread-claim",
            "never-replay-after-provider",
            request_sha256=_request_hash("uncertain-provider-outcome"),
        )
        repository.mark_turn_provider_started(
            scope,
            "thread-claim",
            "never-replay-after-provider",
            str(attempted["claim_token"]),
            provider_binding="openai/gpt-5.4-mini",
        )
        conn.execute(
            "UPDATE vkpi_advisor_turn_claims SET lease_expires_at=NOW()-INTERVAL '1 second' "
            "WHERE client_request_id=?",
            ("never-replay-after-provider",),
        )
        conn.commit()
        blocked = repository.claim_turn_request(
            scope,
            "thread-claim",
            "never-replay-after-provider",
            request_sha256=_request_hash("uncertain-provider-outcome"),
        )
        assert blocked["status"] == "blocked"
        assert blocked["state"] == "outcome_unknown"
        assert blocked["provider_attempted"] is True
        assert blocked["reason"] == "provider_outcome_unknown_manual_reconciliation_required"
    finally:
        conn.close()
        _drop_claim_schema(pg_dsn, schema)


def test_advisor_claim_migration_up_and_down_on_real_postgres(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    repo_root = Path(repository.__file__).resolve().parents[4]
    up_sql = (repo_root / "migrations" / "252_vkpi_advisor_turn_claims.sql").read_text(
        encoding="utf-8"
    )
    down_sql = (
        repo_root / "migrations" / "252_vkpi_advisor_turn_claims_down.sql"
    ).read_text(encoding="utf-8")
    schema = f"vkpi_advisor_migration_{uuid.uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
            )
            conn.execute(
                "CREATE TABLE vkpi_advisor_threads ("
                "organization_id BIGINT NOT NULL, staff_id BIGINT NOT NULL, thread_uid TEXT NOT NULL, "
                "UNIQUE (organization_id, staff_id, thread_uid))"
            )
            conn.execute(up_sql)
            row = conn.execute(
                "SELECT state, provider_attempted FROM vkpi_advisor_turn_claims LIMIT 0"
            )
            assert [column.name for column in row.description] == ["state", "provider_attempted"]
            conn.execute(down_sql)
            exists = conn.execute(
                "SELECT to_regclass(%s)",
                (f"{schema}.vkpi_advisor_turn_claims",),
            ).fetchone()[0]
            assert exists is None
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
