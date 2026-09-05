#!/usr/bin/env python3
"""Synthetic-only PostgreSQL upgrade rehearsal; execution requires explicit opt-in.

Never starts PostgreSQL, loads dotenv, imports the app or uses its database URL.
An operator must first create a private, disposable cluster under /tmp with
a data/ child and its socket in that root (or socket/). Identity is checked
before creating any database.
The existing 245-247 helper supplies the reviewed historical manifest policy;
307-310 use one transaction and the production migration advisory-lock key.
This proves upgrade/transaction properties, not old-app or destructive rollback
compatibility. Normal invocation prints a plan only; --execute requires review.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from threading import Barrier
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

try:
    from scripts.ops import rehearse_apify_migrations_245_247 as historical
except ModuleNotFoundError:  # Direct script invocation, without importing the app.
    import rehearse_apify_migrations_245_247 as historical


ROOT = Path(__file__).resolve().parents[2]
BASELINE = "306_vkpi_product_persona_term_performance.sql"
MIGRATIONS = (
    "307_users_token_version.sql",
    "308_vkpi_privacy_retention_columns.sql",
    "309_vkpi_dsar_public_intake.sql",
    "310_vkpi_kol_search_refresh_scheduler.sql",
)
TARGET_PREFIX = "vkpi_migration_test_"
CLUSTER_PREFIX = "vkpi-migrations-307-310."
TASK_KEY = "kol_profile_incremental_refresh"
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('viltrox_schema_migrations'))"
LEDGER_SQL = "CREATE TABLE IF NOT EXISTS schema_migrations (version_key TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
MARK_SQL = "INSERT INTO schema_migrations(version_key) VALUES (%s) ON CONFLICT DO NOTHING"
SLOT_SQL = """INSERT INTO vkpi_kol_search_inventory_daily_slots
    (batch_date, slot_no, reservation_token) VALUES (%s,%s,%s)
    ON CONFLICT (batch_date,slot_no) DO NOTHING RETURNING slot_no"""
NEW_COLUMNS = {
    "users": ("token_version",),
    "vkpi_kol_portal_tokens": ("expires_at",),
    "apify_jobs": ("payload_purged_at",),
    "vkpi_dsar_requests": ("source", "public_ref", "requester_contact", "requester_message",
                          "subject_profile_url", "suppression_json", "client_ip_hash"),
}
INDEXES = (
    "idx_vkpi_kol_portal_tokens_expires", "idx_apify_jobs_retention_candidates",
    "idx_vkpi_comments_fetched_at", "idx_kol_comments_created_at",
    "uq_vkpi_dsar_public_ref", "idx_vkpi_dsar_source_status",
    "idx_apify_jobs_kol_search_inventory_source_created",
)


class RehearsalError(RuntimeError):
    """Only fixed, credential-free diagnostics leave the rehearsal."""


@dataclass(frozen=True)
class Migration:
    name: str
    text: str
    sha256: str


@dataclass(frozen=True)
class ClusterBinding:
    root: Path
    identity: tuple[int, int]
    data_identity: tuple[int, int]
    params: dict[str, str]
    system_identifier: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _private_directory(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise RehearsalError("private_directory_required")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RehearsalError("private_directory_owner_or_mode")
    return info.st_dev, info.st_ino


def _cluster_binding(admin_dsn: str, cluster_root: Path) -> ClusterBinding:
    """Reject ambiguous destinations before any connection is attempted."""
    if cluster_root.is_symlink():
        raise RehearsalError("cluster_symlink_forbidden")
    root = cluster_root.resolve(strict=True)
    if root.parent != Path("/tmp").resolve() or not root.name.startswith(CLUSTER_PREFIX):
        raise RehearsalError("disposable_cluster_root_required")
    identity = _private_directory(root)
    data_identity = _private_directory(root / "data")
    try:
        params = conninfo_to_dict(admin_dsn)
    except psycopg.Error:
        raise RehearsalError("invalid_admin_dsn") from None
    if set(params) != {"host", "port", "user", "dbname"}:
        raise RehearsalError("admin_dsn_requires_only_host_port_user_dbname")
    if params["dbname"] not in {"postgres", "template1"} or params["user"] != "postgres":
        raise RehearsalError("local_postgres_admin_required")
    host = Path(params["host"])
    if not host.is_absolute() or host.is_symlink() or host.resolve() not in {root, root / "socket"}:
        raise RehearsalError("private_unix_socket_required")
    _private_directory(host)
    if not re.fullmatch(r"[0-9]{1,5}", params["port"]) or not 1 <= int(params["port"]) <= 65535:
        raise RehearsalError("explicit_valid_port_required")
    params["host"] = str(host.resolve())
    return ClusterBinding(root, identity, data_identity, params)


def _connect(binding: ClusterBinding, database: str) -> Any:
    # libpq PG* variables can redirect hostaddr/service/options despite a DSN.
    # Inspect names only, never ambient values or application credentials.
    if any(key.startswith("PG") for key in os.environ):
        raise RehearsalError("ambient_libpq_configuration_forbidden_use_clean_environment")
    if _private_directory(binding.root) != binding.identity:
        raise RehearsalError("cluster_identity_changed")
    if _private_directory(binding.root / "data") != binding.data_identity:
        raise RehearsalError("cluster_data_identity_changed")
    if database not in {"postgres", "template1"} and not re.fullmatch(TARGET_PREFIX + r"[0-9a-f]{24}", database):
        raise RehearsalError("unsafe_database_name")
    return psycopg.connect(
        **{**binding.params, "dbname": database}, autocommit=True, connect_timeout=5,
        options="-c search_path=public,pg_catalog -c statement_timeout=120000 -c lock_timeout=10000",
        application_name="vkpi-synthetic-migration-rehearsal", sslmode="disable", gssencmode="disable",
        passfile="/dev/null",
    )


def _verify_server(conn: Any, binding: ClusterBinding, database: str) -> str:
    row = conn.execute("SELECT current_database(), inet_server_addr(), current_setting('data_directory'), (SELECT system_identifier::text FROM pg_control_system())").fetchone()
    if row[0] != database or row[1] is not None or Path(row[2]).resolve() != binding.root / "data":
        raise RehearsalError("server_not_bound_to_disposable_cluster")
    if _private_directory(binding.root) != binding.identity:
        raise RehearsalError("cluster_identity_changed")
    if _private_directory(binding.root / "data") != binding.data_identity:
        raise RehearsalError("cluster_data_identity_changed")
    if not str(row[3]).isdigit() or (binding.system_identifier and str(row[3]) != binding.system_identifier):
        raise RehearsalError("postgres_system_identifier_changed")
    return str(row[3])


def _bind_admin(conn: Any, binding: ClusterBinding) -> ClusterBinding:
    identifier = _verify_server(conn, binding, binding.params["dbname"])
    locked = conn.execute("SELECT pg_try_advisory_lock(hashtext('vkpi_migrations_307_310_rehearsal'))").fetchone()[0]
    _check(locked is True, "another_rehearsal_owns_cluster")
    # Only the pristine cluster created for this rehearsal is accepted. A name
    # prefix alone must never authorize another database on a shared server.
    others = conn.execute("SELECT datname FROM pg_database WHERE datname NOT IN ('postgres','template0','template1')").fetchall()
    _check(not others, "disposable_cluster_has_other_databases")
    sessions = conn.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()[0]
    _check(sessions == 0, "disposable_cluster_has_other_client_sessions")
    return replace(binding, system_identifier=identifier)


def _source_plan(root: Path) -> tuple[list[Path], tuple[Migration, ...]]:
    prefix = historical._discover_forward_migrations(root, through=BASELINE)
    pending: list[Migration] = []
    for name in MIGRATIONS:
        path = root / "migrations" / name
        if path.is_symlink() or not path.is_file():
            raise RehearsalError("migration_regular_file_required")
        payload = path.read_text(encoding="utf-8")
        if historical.FORWARD_TRANSACTION_CONTROL_RE.search(payload):
            raise RehearsalError("pending_migration_must_not_control_transaction")
        pending.append(Migration(name, payload, hashlib.sha256(payload.encode()).hexdigest()))
    return prefix, tuple(pending)


def _prefix_hashes(prefix: list[Path]) -> dict[str, str]:
    hashes = {}
    for path in prefix:
        if path.is_symlink() or not path.is_file():
            raise RehearsalError("baseline_regular_file_required")
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _apply_pending(conn: Any, migrations: tuple[Migration, ...], *, replay: bool = False,
                   fail_after: str = "") -> list[str]:
    """Production ordering: ledger, transaction advisory lock, all DDL+marks, one commit."""
    applied_now: list[str] = []
    with conn.transaction():
        conn.execute(LEDGER_SQL)
        conn.execute(LOCK_SQL)
        applied = {row[0] for row in conn.execute("SELECT version_key FROM schema_migrations").fetchall()}
        for migration in migrations:
            if migration.name in applied and not replay:
                continue
            conn.execute(migration.text)
            conn.execute(MARK_SQL, (migration.name,))
            applied_now.append(migration.name)
            if migration.name == fail_after:
                conn.execute("SELECT 1 / 0")  # Real SQL failure inside the same transaction.
    return applied_now


def _ledger(conn: Any, expected: list[str]) -> dict[str, Any]:
    return historical._assert_schema_migration_manifest(conn, expected)


def _seed(conn: Any) -> dict[str, list[int]]:
    seed: dict[str, list[int]] = {}
    def one(table: str, statement: str, params: tuple[Any, ...] = ()) -> int:
        identifier = int(conn.execute(statement + " RETURNING id", params).fetchone()[0])
        seed.setdefault(table, []).append(identifier)
        return identifier
    one("users", "INSERT INTO users(email,password_hash) VALUES ('synthetic-upgrade@example.invalid','not-a-login-hash')")
    pool = one("vkpi_kol_pool", "INSERT INTO vkpi_kol_pool(pool_uid,platform,handle,followers) VALUES ('migration-fixture','youtube','fixture_creator',4000)")
    one("vkpi_kol_portal_tokens", "INSERT INTO vkpi_kol_portal_tokens(kol_pool_id,token,created_at) VALUES (%s,'synthetic-portal-value',NOW()-INTERVAL '120 days')", (pool,))
    for state in ("done", "failed", "queued"):
        one("apify_jobs", "INSERT INTO apify_jobs(job_type,payload,status,created_at) VALUES ('synthetic_only','{\"fixture\":true}'::jsonb,%s,NOW()-INTERVAL '200 days')", (state,))
    one("vkpi_comments", "INSERT INTO vkpi_comments(platform,external_comment_id,comment_text,created_at,fetched_at) VALUES ('youtube','migration-comment','synthetic retained comment',NOW()-INTERVAL '200 days',NOW()-INTERVAL '200 days')")
    kol = one("kols", "INSERT INTO kols(channel_name,platform) VALUES ('Synthetic Creator','youtube')")
    one("kol_comments", "INSERT INTO kol_comments(kol_id,platform,comment_text,created_at) VALUES (%s,'youtube','synthetic retained comment',NOW()-INTERVAL '200 days')", (kol,))
    for kind in ("erasure", "access", "rectification"):
        one("vkpi_dsar_requests", "INSERT INTO vkpi_dsar_requests(request_type,subject_kol_pool_id,note) VALUES (%s,%s,'synthetic retained request')", (kind, pool))
    conn.execute("UPDATE scheduler_tasks SET enabled=TRUE,owner='synthetic-owner' WHERE task_key=%s", (TASK_KEY,))
    return seed


def _legacy_snapshot(conn: Any, seed: dict[str, list[int]]) -> dict[str, Any]:
    snapshot = {}
    for table, identifiers in seed.items():
        statement = sql.SQL("SELECT to_jsonb(t) - %s::text[] FROM {} t WHERE id=ANY(%s) ORDER BY id").format(sql.Identifier(table))
        snapshot[table] = [row[0] for row in conn.execute(statement, (list(NEW_COLUMNS.get(table, ())), identifiers)).fetchall()]
    return snapshot


def _schema_state(conn: Any) -> dict[str, Any]:
    columns = conn.execute("SELECT table_name,column_name,is_nullable,column_default,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s) ORDER BY table_name,ordinal_position", (list(NEW_COLUMNS),)).fetchall()
    indexes = conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND indexname=ANY(%s) ORDER BY indexname", (list(INDEXES),)).fetchall()
    constraints = conn.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='vkpi_dsar_requests'::regclass ORDER BY conname").fetchall()
    slot = conn.execute("SELECT to_regclass('public.vkpi_kol_search_inventory_daily_slots') IS NOT NULL").fetchone()[0]
    return {"columns": columns, "indexes": indexes, "constraints": constraints, "slots": slot}


def _scheduler_row(conn: Any) -> Any:
    return conn.execute("SELECT enabled,max_daily_runs,max_daily_cost_cents,allowed_hours,owner FROM scheduler_tasks WHERE task_key=%s", (TASK_KEY,)).fetchone()


def _check(condition: bool, code: str) -> None:
    if not condition:
        raise RehearsalError(code)


def _failed_batch_proof(conn: Any, pending: tuple[Migration, ...], seed: dict[str, list[int]],
                        expected: list[str]) -> dict[str, Any]:
    before = (_schema_state(conn), _legacy_snapshot(conn, seed), _scheduler_row(conn))
    failures = []
    for migration in pending:
        try:
            _apply_pending(conn, pending, fail_after=migration.name)
        except psycopg.errors.DivisionByZero:
            failures.append(migration.name)
        else:
            raise RehearsalError("failure_injection_did_not_fail")
        _ledger(conn, expected)
        after = (_schema_state(conn), _legacy_snapshot(conn, seed), _scheduler_row(conn))
        _check(after == before, "failed_batch_left_schema_data_or_switch_changes")
    return {"injected_after": failures, "whole_batch_rollback": True}


def _upgrade_proof(conn: Any, seed: dict[str, list[int]], before: dict[str, Any]) -> dict[str, Any]:
    _check(_legacy_snapshot(conn, seed) == before, "legacy_rows_changed")
    state = _schema_state(conn)
    columns = {(row[0], row[1]): row[2:] for row in state["columns"]}
    for table, names in NEW_COLUMNS.items():
        _check(all((table, name) in columns for name in names), "new_columns_missing")
    _check(columns[("users", "token_version")] == ("YES", None, "integer"), "token_version_contract_changed")
    for key in (("apify_jobs", "payload_purged_at"), ("vkpi_kol_portal_tokens", "expires_at")):
        _check(columns[key] == ("YES", None, "timestamp with time zone"), "retention_timestamp_contract_changed")
    for table, column in (("users", "token_version"), ("apify_jobs", "payload_purged_at"), ("vkpi_kol_portal_tokens", "expires_at")):
        query = sql.SQL("SELECT COUNT(*) FROM {} WHERE id=ANY(%s) AND {} IS NOT NULL").format(sql.Identifier(table), sql.Identifier(column))
        _check(conn.execute(query, (seed[table],)).fetchone()[0] == 0, "legacy_nullable_field_was_backfilled")
    _check(set(row[0] for row in state["indexes"]) == set(INDEXES), "upgrade_indexes_missing")
    _check(bool(state["slots"]), "daily_slot_table_missing")
    _check(tuple(_scheduler_row(conn)) == (False, 1, 0, "03:00-06:00 America/New_York", "synthetic-owner"), "refresh_not_forced_off")
    legacy = conn.execute("SELECT source,public_ref,requester_contact,suppression_json FROM vkpi_dsar_requests WHERE id=ANY(%s)", (seed["vkpi_dsar_requests"],)).fetchall()
    _check(all(tuple(row) == ("staff", None, "", "{}") for row in legacy), "legacy_dsar_defaults_changed")
    return {"legacy_rows_preserved": True, "new_columns_and_indexes": True,
            "refresh_forced_off": True, "operator_owner_preserved": True}


def _expect_database_error(conn: Any, statement: str, params: tuple[Any, ...], error_type: type[Exception]) -> None:
    try:
        with conn.transaction():
            conn.execute(statement, params)
    except error_type:
        return
    raise RehearsalError("expected_database_constraint_did_not_reject")


def _constraint_proof(conn: Any) -> dict[str, bool]:
    insert = "INSERT INTO vkpi_dsar_requests(request_type,source,public_ref) VALUES (%s,%s,%s)"
    conn.execute(insert, ("do_not_contact", "public_form", "SYNTHETIC-DSAR-1"))
    _expect_database_error(conn, insert, ("erasure", "staff", "SYNTHETIC-DSAR-1"), psycopg.errors.UniqueViolation)
    _expect_database_error(conn, insert, ("invalid", "staff", None), psycopg.errors.CheckViolation)
    _expect_database_error(conn, insert, ("erasure", "invalid", None), psycopg.errors.CheckViolation)
    conn.execute(insert, ("erasure", "staff", None))
    conn.execute(insert, ("erasure", "staff", None))
    for slot in (0, 6):
        _expect_database_error(conn, SLOT_SQL, ("2026-09-04", slot, "synthetic-invalid"), psycopg.errors.CheckViolation)
    return {"dsar_type_and_source_checks": True, "public_ref_unique_nulls_allowed": True, "slot_range_check": True}


def _reserve_slots(conn: Any, day: str, token: str) -> list[int]:
    slots = []
    with conn.transaction():
        for number in range(1, 6):
            row = conn.execute(SLOT_SQL, (day, number, token)).fetchone()
            if row:
                slots.append(int(row[0]))
    return slots


def _slot_concurrency(binding: ClusterBinding, target: str) -> dict[str, Any]:
    barrier = Barrier(8)
    def reserve(index: int) -> list[int]:
        with _connect(binding, target) as conn:
            _verify_server(conn, binding, target)
            barrier.wait(timeout=10)
            return _reserve_slots(conn, "2026-09-04", f"synthetic-owner-{index}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    inserted = [slot for attempt in results for slot in attempt]
    with _connect(binding, target) as conn:
        _verify_server(conn, binding, target)
        count = conn.execute("SELECT COUNT(*) FROM vkpi_kol_search_inventory_daily_slots WHERE batch_date=DATE '2026-09-04'").fetchone()[0]
        _check(len(inserted) == len(set(inserted)) == count == 5, "concurrent_daily_cap_failed")
        _check(len(_reserve_slots(conn, "2026-09-05", "synthetic-next-day")) == 5, "next_day_cap_failed")
        job = conn.execute("INSERT INTO apify_jobs(job_type,payload,status) VALUES ('synthetic_slot','{}','done') RETURNING id").fetchone()[0]
        conn.execute("UPDATE vkpi_kol_search_inventory_daily_slots SET job_id=%s WHERE batch_date=DATE '2026-09-04' AND slot_no=1", (job,))
        conn.execute("DELETE FROM apify_jobs WHERE id=%s", (job,))
        cleared = conn.execute("SELECT job_id IS NULL FROM vkpi_kol_search_inventory_daily_slots WHERE batch_date=DATE '2026-09-04' AND slot_no=1").fetchone()[0]
        _check(bool(cleared), "slot_job_foreign_key_failed")
    return {"connections": 8, "requested_total": 40, "inserted": count, "next_day_independent": True, "job_delete_sets_null": True}


def _scenario(conn: Any, prefix: list[Path], migrations: tuple[Migration, ...], *, already_307: bool) -> dict[str, Any]:
    conn.execute(LEDGER_SQL)
    # Historical migrations <234 contain their own transaction control. Only
    # this synthetic baseline bootstrap uses the legacy per-file helper.
    for path in prefix:
        historical._apply_sql(conn, path)
    expected = [path.name for path in prefix]
    _ledger(conn, expected)
    seed = _seed(conn)
    before = _legacy_snapshot(conn, seed)
    pending = migrations
    if already_307:
        _apply_pending(conn, migrations[:1])
        expected += [MIGRATIONS[0]]
        pending = migrations[1:]
    failures = _failed_batch_proof(conn, pending, seed, expected)
    _apply_pending(conn, pending)
    expected += [item.name for item in pending]
    ledger = _ledger(conn, expected)
    upgrade = _upgrade_proof(conn, seed, before)
    constraints = _constraint_proof(conn)
    conn.execute("UPDATE scheduler_tasks SET enabled=TRUE WHERE task_key=%s", (TASK_KEY,))
    _check(_apply_pending(conn, migrations) == [], "ledger_replay_reexecuted_sql")
    _check(_scheduler_row(conn)[0] is True, "ledger_replay_overwrote_operator_switch")
    _apply_pending(conn, migrations, replay=True)
    _upgrade_proof(conn, seed, before)
    _ledger(conn, expected)
    return {"starting_version": 307 if already_307 else 306, "failure_injection": failures,
            "upgrade": upgrade, "constraints": constraints, "ledger": ledger,
            "ledger_replay_noop": True, "explicit_sql_replay_forces_off": True}


def _database_oid(admin: Any, target: str) -> int | None:
    row = admin.execute("SELECT oid FROM pg_database WHERE datname=%s", (target,)).fetchone()
    return int(row[0]) if row else None


def _drop_owned_database(admin: Any, binding: ClusterBinding, target: str, expected_oid: int | None) -> None:
    _verify_server(admin, binding, binding.params["dbname"])
    _check(bool(re.fullmatch(TARGET_PREFIX + r"[0-9a-f]{24}", target)), "unsafe_cleanup_target")
    _check(expected_oid is not None, "cleanup_target_oid_unknown")
    observed_oid = _database_oid(admin, target)
    if observed_oid is None:
        return
    _check(observed_oid == expected_oid, "cleanup_target_oid_changed")
    admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datid=%s AND datname=%s AND pid<>pg_backend_pid()", (expected_oid, target))
    _check(_database_oid(admin, target) == expected_oid, "cleanup_target_oid_changed")
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(target)))
    _check(_database_oid(admin, target) is None, "disposable_database_cleanup_failed")


def _run_owned_scenario(admin: Any, binding: ClusterBinding, prefix: list[Path],
                        pending: tuple[Migration, ...], *, already_307: bool) -> dict[str, Any]:
    target = TARGET_PREFIX + secrets.token_hex(12)
    result: dict[str, Any] = {"target": target, "status": "failed", "cleanup_state": "not_created"}
    created, target_oid = False, None
    stage = "create_synthetic_database"
    try:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(target)))
        created = True
        result["cleanup_state"] = "required"
        target_oid = _database_oid(admin, target)
        _check(target_oid is not None, "created_database_oid_missing")
        stage = "upgrade_307_base" if already_307 else "upgrade_306_base"
        with _connect(binding, target) as conn:
            _verify_server(conn, binding, target)
            result.update(_scenario(conn, prefix, pending, already_307=already_307))
        stage = "slot_concurrency"
        result["slots"] = _slot_concurrency(binding, target)
        result["status"] = "passed"
    except Exception as exc:
        result.update(error_type=type(exc).__name__, failed_stage=stage)
        if not created:
            # CREATE transport failure may be indeterminate. Do not guess OID
            # ownership or delete a pre-existing name on an error path.
            result["cleanup_state"] = "creation_unconfirmed_no_drop_attempted"
    finally:
        if created:
            try:
                _drop_owned_database(admin, binding, target, target_oid)
                result.update(cleanup_state="dropped", ephemeral_database_dropped=True)
            except Exception as exc:
                result.update(status="failed", cleanup_state="blocked", cleanup_error_type=type(exc).__name__, ephemeral_database_dropped=False)
    return result


def run_rehearsal(admin_dsn: str, cluster_root: Path, *, root: Path = ROOT) -> dict[str, Any]:
    report: dict[str, Any] = {"schema_version": 1, "rehearsal": "migrations_307_310",
                             "status": "failed", "started_at": _now(), "scenarios": [],
                             "synthetic_only": True, "business_database_accessed": False,
                             "overall_application_rollback_proven": False}
    stage = "preflight"
    try:
        binding = _cluster_binding(admin_dsn, cluster_root)
        prefix, pending = _source_plan(root)
        prefix_hashes = _prefix_hashes(prefix)
        report["baseline_hashes"] = prefix_hashes
        report["migration_hashes"] = {item.name: item.sha256 for item in pending}
        with _connect(binding, binding.params["dbname"]) as admin:
            binding = _bind_admin(admin, binding)
            report["cluster_system_identifier"] = binding.system_identifier
            report["server_version"] = str(admin.execute("SHOW server_version").fetchone()[0])
            for already_307 in (False, True):
                scenario = _run_owned_scenario(admin, binding, prefix, pending, already_307=already_307)
                report["scenarios"].append(scenario)
                stage = scenario.get("failed_stage", "scenario_or_cleanup")
                _check(scenario["status"] == "passed", "scenario_failed")
        stage = "source_binding"
        current_prefix, current = _source_plan(root)
        _check(current == pending, "migration_source_changed_during_rehearsal")
        _check(_prefix_hashes(current_prefix) == prefix_hashes, "baseline_source_changed_during_rehearsal")
        report["status"] = "passed"
    except Exception as exc:
        report.update(error_type=type(exc).__name__, failed_stage=stage)
        # No str(exc), DSN, SQL params or synthetic row bodies in evidence.
    report["completed_at"] = _now()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Run only after review against a separately created disposable cluster")
    parser.add_argument("--admin-dsn", default="", help="Explicit host=<private socket> port=<port> user=postgres dbname=postgres; no credentials")
    parser.add_argument("--cluster-root", type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        payload = {"status": "plan_only", "migration_names": list(MIGRATIONS),
                   "starting_versions": [306, 307], "database_connections": 0,
                   "requires_reviewed_execute": True, "overall_application_rollback_proven": False}
    elif not args.admin_dsn or args.cluster_root is None:
        payload = {"status": "failed", "failed_stage": "explicit_disposable_cluster_required"}
    else:
        payload = run_rehearsal(args.admin_dsn, args.cluster_root)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0 if payload["status"] in {"passed", "plan_only"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
