#!/usr/bin/env python3
"""Rehearse migrations 245-247 in a disposable PostgreSQL database.

Safety rules:
- never reads ``DATABASE_URL`` and never connects to ``viltrox2``;
- the supplied admin DSN must name ``postgres`` or ``template1``;
- creates a random ``vkpi_round12_ephemeral_*`` database from ``template1``;
- drops that database in ``finally`` and emits no DSN or credentials.

The script applies the repository's complete forward chain through 244 before
exercising 245 -> 246 -> 247, 247 rollback, and 247 re-forward.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import sys
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import UniqueViolation


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_245 = "245_vkpi_staff_organization_membership_backfill.sql"
MIGRATION_246 = "246_vkpi_worker_runtime_identity.sql"
MIGRATION_247 = "247_apify_jobs_active_idempotency.sql"
MIGRATION_247_DOWN = "247_apify_jobs_active_idempotency_down.sql"
TARGET_PREFIX = "vkpi_round12_ephemeral_"
# Keep this manifest policy exactly aligned with
# ``backend.app.db.connection._MIGRATION_EXCLUDE``.  Importing the application
# module here would load the ambient application environment, which this
# isolated rehearsal deliberately avoids; the focused test suite compares the
# two constants and the complete discovered prefix instead.
EXCLUDED_FORWARD = frozenset(
    {
        "001_verification.sql",
        "002_intelligence.sql",
        "004_viltrox_matrix.sql",
        "010_party_layer_rollback.sql",
    }
)
RUNNER_OWNED_TRANSACTION_MIN_VERSION = 234
FORWARD_TRANSACTION_CONTROL_RE = re.compile(
    r"(?mi)^\s*(?:BEGIN(?:\s+TRANSACTION)?|COMMIT(?:\s+TRANSACTION)?)\s*;"
)
ACTIVE_CONFLICT_SQL = """
INSERT INTO apify_jobs (job_type, payload, idempotency_key, status, created_at, updated_at)
VALUES ('round12_probe', '{}'::jsonb, %s, 'queued', NOW(), NOW())
ON CONFLICT (idempotency_key)
  WHERE idempotency_key IS NOT NULL
    AND idempotency_key <> ''
    AND status IN ('queued', 'running')
DO NOTHING
RETURNING id
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_admin_database_name(value: object) -> str:
    name = str(value or "").strip().lower()
    if name == "viltrox2":
        raise RuntimeError("refusing to connect to viltrox2")
    if name not in {"postgres", "template1"}:
        raise RuntimeError("admin DSN must name postgres or template1")
    return name


def _validate_admin_server(database: object, server_address: object) -> str:
    name = _validate_admin_database_name(database)
    if server_address is not None:
        raise RuntimeError("admin DSN must use a local Unix-domain socket, not TCP")
    return name


def _target_database_name() -> str:
    return TARGET_PREFIX + secrets.token_hex(8)


def _discover_forward_migrations(root: Path, *, through: str) -> list[Path]:
    files = sorted(
        path
        for path in (root / "migrations").glob("*.sql")
        if not path.name.endswith("_down.sql") and path.name not in EXCLUDED_FORWARD
    )
    selected = [path for path in files if path.name <= through]
    if not selected or selected[-1].name != through:
        raise RuntimeError(f"forward migration boundary missing: {through}")
    for path in selected:
        match = re.match(r"^(\d{3})", path.name)
        if (
            match is not None
            and int(match.group(1)) >= RUNNER_OWNED_TRANSACTION_MIN_VERSION
            and FORWARD_TRANSACTION_CONTROL_RE.search(path.read_text(encoding="utf-8"))
        ):
            raise RuntimeError(
                "forward migration contains transaction control owned by the "
                f"Postgres migration runner: {path.name}"
            )
    return selected


def _assert_schema_migration_manifest(
    conn: psycopg.Connection[Any], expected: list[str]
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT version_key FROM schema_migrations ORDER BY version_key"
    ).fetchall()
    observed = [str(row[0]) for row in rows]
    expected_sorted = sorted(expected)
    if observed != expected_sorted:
        missing = sorted(set(expected_sorted) - set(observed))
        unexpected = sorted(set(observed) - set(expected_sorted))
        raise AssertionError(
            "schema_migrations manifest mismatch: "
            f"missing={missing[:5]} unexpected={unexpected[:5]}"
        )
    return {
        "applied_count": len(observed),
        "first": observed[0] if observed else None,
        "last": observed[-1] if observed else None,
        "exact_manifest_match": True,
    }


def _derived_dsn(admin: psycopg.Connection[Any], dbname: str) -> str:
    params = dict(admin.info.get_parameters())
    params["dbname"] = dbname
    params.pop("password", None)
    # Password, when required, remains inside libpq's established environment
    # or passfile; it is never copied into evidence or logs.
    return make_conninfo(**params)


def _apply_sql(conn: psycopg.Connection[Any], path: Path, *, mark: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute(path.read_text(encoding="utf-8"))
        if mark:
            cur.execute(
                "INSERT INTO schema_migrations(version_key) VALUES (%s) ON CONFLICT DO NOTHING",
                (path.name,),
            )
    conn.commit()


def _index_state(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname='public' AND indexname='uq_apify_jobs_active_idempotency'
            """
        )
        row = cur.fetchone()
    return {"present": bool(row), "definition": str(row[0]) if row else ""}


def _assert_isolated_target(conn: psycopg.Connection[Any], target: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT current_database(),
                   (SELECT oid FROM pg_database WHERE datname=current_database()),
                   (SELECT oid FROM pg_database WHERE datname='viltrox2')
            """
        )
        current, current_oid, production_oid = cur.fetchone()
    if current != target or not str(current).startswith(TARGET_PREFIX):
        raise RuntimeError("target database identity mismatch")
    if production_oid is not None and current_oid == production_oid:
        raise RuntimeError("ephemeral database unexpectedly matches viltrox2 OID")
    return {
        "database_name_prefix": TARGET_PREFIX,
        "target_oid_distinct_from_viltrox2": current_oid != production_oid,
    }


def _seed_pre_245(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO staff (role, is_owner) VALUES
              ('readonly', 1),
              ('admin', 0),
              ('readonly', 0),
              ('editor', 0)
            RETURNING id
            """
        )
        staff_ids = [int(row[0]) for row in cur.fetchall()]
        cur.execute(
            """
            INSERT INTO vkpi_worker_heartbeat (worker_name, last_heartbeat_at, pid)
            VALUES ('round12-worker', NOW(), 4242)
            """
        )
        cur.execute(
            """
            INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
            VALUES
              ('round12_null_a', '{}'::jsonb, NULL, 'queued'),
              ('round12_null_b', '{}'::jsonb, NULL, 'queued'),
              ('round12_empty_a', '{}'::jsonb, '', 'queued'),
              ('round12_empty_b', '{}'::jsonb, '', 'running')
            """
        )
    conn.commit()
    return {"staff_ids": staff_ids, "legacy_null_or_empty_rows": 4}


def _verify_245(conn: psycopg.Connection[Any], staff_ids: list[int]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, m.role
            FROM staff s JOIN organization_members m ON m.staff_id=s.id
            WHERE s.id = ANY(%s)
            ORDER BY s.id
            """,
            (staff_ids,),
        )
        roles = [str(row[1]) for row in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM organization_members WHERE staff_id = ANY(%s)", (staff_ids,))
        count = int(cur.fetchone()[0])
    expected = ["owner", "admin", "viewer", "member"]
    if roles != expected or count != len(staff_ids):
        raise AssertionError(f"migration 245 role mapping mismatch: {roles}")
    return {"membership_count": count, "roles": roles}


def _verify_246(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='vkpi_worker_heartbeat'
              AND column_name IN ('worker_git_sha','boot_nonce_sha256','started_at')
            ORDER BY column_name
            """
        )
        columns = [str(row[0]) for row in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM vkpi_worker_heartbeat WHERE worker_name='round12-worker'")
        preserved = int(cur.fetchone()[0]) == 1
    if columns != ["boot_nonce_sha256", "started_at", "worker_git_sha"] or not preserved:
        raise AssertionError("migration 246 column/preservation mismatch")
    return {"columns": columns, "preexisting_row_preserved": preserved}


def _expect_unique_violation(conn: psycopg.Connection[Any], key: str) -> bool:
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
                    VALUES ('round12_duplicate', '{}'::jsonb, %s, 'running')
                    """,
                    (key,),
                )
    except UniqueViolation:
        return True
    return False


def _verify_247_semantics(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    state = _index_state(conn)
    if not state["present"]:
        raise AssertionError("migration 247 index missing")
    definition = state["definition"]
    for required in ("idempotency_key", "queued", "running"):
        if required not in definition:
            raise AssertionError(f"migration 247 index predicate missing {required}")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
            VALUES ('round12_active', '{}'::jsonb, 'round12-active-key', 'queued')
            RETURNING id
            """
        )
        active_id = int(cur.fetchone()[0])
    conn.commit()
    active_duplicate_rejected = _expect_unique_violation(conn, "round12-active-key")
    conn.rollback()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
            VALUES
              ('round12_terminal_a', '{}'::jsonb, 'round12-terminal-key', 'done'),
              ('round12_terminal_b', '{}'::jsonb, 'round12-terminal-key', 'failed')
            """
        )
        cur.execute("UPDATE apify_jobs SET status='done' WHERE id=%s", (active_id,))
        cur.execute(
            """
            INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
            VALUES ('round12_rerun', '{}'::jsonb, 'round12-active-key', 'queued')
            """
        )
        cur.execute(
            """
            SELECT COUNT(*) FROM apify_jobs
            WHERE idempotency_key IS NULL OR idempotency_key=''
            """
        )
        null_empty_count = int(cur.fetchone()[0])
    conn.commit()
    if not active_duplicate_rejected or null_empty_count < 4:
        raise AssertionError("migration 247 uniqueness/null semantics mismatch")
    return {
        "index_present": True,
        "active_duplicate_rejected": active_duplicate_rejected,
        "terminal_rows_repeatable": True,
        "same_key_rerun_after_done": True,
        "null_and_empty_keys_allowed": True,
        "null_or_empty_fixture_count": null_empty_count,
    }


def _concurrent_on_conflict(target_dsn: str, key: str) -> dict[str, Any]:
    def insert_once() -> int | None:
        with psycopg.connect(target_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(ACTIVE_CONFLICT_SQL, (key,))
                row = cur.fetchone()
            conn.commit()
            return int(row[0]) if row else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: insert_once(), range(2)))
    with psycopg.connect(target_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM apify_jobs WHERE idempotency_key=%s AND status IN ('queued','running')",
                (key,),
            )
            active_count = int(cur.fetchone()[0])
    inserted = [value for value in results if value is not None]
    if len(inserted) != 1 or active_count != 1:
        raise AssertionError(f"concurrent ON CONFLICT mismatch: results={results} active={active_count}")
    return {"attempts": 2, "inserted": 1, "conflict_reused": 1, "active_rows": active_count}


def run_rehearsal(admin_dsn: str, *, root: Path = ROOT) -> dict[str, Any]:
    started_at = _utc_now()
    target = _target_database_name()
    report: dict[str, Any] = {
        "schema_version": 1,
        "rehearsal": "vkpi_round12_migrations_245_247",
        "started_at": started_at,
        "status": "failed",
        "target_database_prefix": TARGET_PREFIX,
        "checks": {},
    }
    admin: psycopg.Connection[Any] | None = None
    target_dsn = ""
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
        with admin.cursor() as cur:
            cur.execute("SELECT current_database(), inet_server_addr()")
            _validate_admin_server(*cur.fetchone())
            cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template1").format(sql.Identifier(target)))
        target_dsn = _derived_dsn(admin, target)
        with psycopg.connect(target_dsn) as conn:
            report["checks"]["isolation"] = _assert_isolated_target(conn, target)
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations (
                         version_key TEXT PRIMARY KEY,
                         applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                       )"""
                )
            conn.commit()
            pre_chain = _discover_forward_migrations(root, through="244_vkpi_event_radar_truth_scope.sql")
            for path in pre_chain:
                _apply_sql(conn, path)
            pre_manifest = [path.name for path in pre_chain]
            report["checks"]["forward_chain_through_244"] = (
                _assert_schema_migration_manifest(conn, pre_manifest)
            )
            seed = _seed_pre_245(conn)
            _apply_sql(conn, root / "migrations" / MIGRATION_245)
            report["checks"]["migration_245"] = _verify_245(conn, seed["staff_ids"])
            _apply_sql(conn, root / "migrations" / MIGRATION_246)
            report["checks"]["migration_246"] = _verify_246(conn)
            _apply_sql(conn, root / "migrations" / MIGRATION_247)
            report["checks"]["migration_247_forward"] = _verify_247_semantics(conn)
            forward_manifest = pre_manifest + [MIGRATION_245, MIGRATION_246, MIGRATION_247]
            report["checks"]["schema_migrations_forward"] = (
                _assert_schema_migration_manifest(conn, forward_manifest)
            )

        report["checks"]["concurrent_on_conflict_forward"] = _concurrent_on_conflict(
            target_dsn, "round12-concurrent-forward"
        )

        with psycopg.connect(target_dsn) as conn:
            _apply_sql(conn, root / "migrations" / MIGRATION_247_DOWN, mark=False)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM schema_migrations WHERE version_key=%s", (MIGRATION_247,))
            conn.commit()
            if _index_state(conn)["present"]:
                raise AssertionError("migration 247 rollback left index behind")
            rollback_manifest = pre_manifest + [MIGRATION_245, MIGRATION_246]
            report["checks"]["schema_migrations_rollback"] = (
                _assert_schema_migration_manifest(conn, rollback_manifest)
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
                    VALUES
                      ('round12_rollback_a', '{}'::jsonb, 'round12-rollback-duplicate', 'queued'),
                      ('round12_rollback_b', '{}'::jsonb, 'round12-rollback-duplicate', 'running')
                    """
                )
                cur.execute(
                    "DELETE FROM apify_jobs WHERE idempotency_key='round12-rollback-duplicate'"
                )
            conn.commit()
            report["checks"]["migration_247_rollback"] = {
                "index_absent": True,
                "duplicate_active_rows_temporarily_allowed": True,
                "migration_245_memberships_preserved": _verify_245(conn, seed["staff_ids"])["membership_count"],
                "migration_246_columns_preserved": _verify_246(conn)["columns"],
            }
            _apply_sql(conn, root / "migrations" / MIGRATION_247)
            report["checks"]["migration_247_reforward"] = _index_state(conn)
            report["checks"]["schema_migrations_reforward"] = (
                _assert_schema_migration_manifest(conn, forward_manifest)
            )
        report["checks"]["concurrent_on_conflict_reforward"] = _concurrent_on_conflict(
            target_dsn, "round12-concurrent-reforward"
        )
        report["status"] = "passed"
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:500]
        return report
    finally:
        if admin is not None:
            try:
                with admin.cursor() as cur:
                    cur.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                        (target,),
                    )
                    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(target)))
                    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target,))
                    report["ephemeral_database_dropped"] = cur.fetchone() is None
            finally:
                admin.close()
        report["completed_at"] = _utc_now()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-dsn", required=True, help="Explicit DSN naming postgres or template1; never viltrox2")
    args = parser.parse_args(argv)
    try:
        payload = run_rehearsal(args.admin_dsn)
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "rehearsal": "vkpi_round12_migrations_245_247",
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "completed_at": _utc_now(),
        }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return 0 if payload.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
