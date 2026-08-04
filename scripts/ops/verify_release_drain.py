#!/usr/bin/env python3
"""Read-only pre-mutation drain proof for the V-KPI release boundary.

The probe reads connection settings only from an explicitly supplied protected
dotenv, inspects the existing Redis Stream consumer group without creating or
acknowledging anything, and runs aggregate counts in a PostgreSQL read-only
transaction.  It emits bounded metadata/counts only; URLs, credentials, job
payloads, stream fields, and business rows are never emitted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit

from dotenv import dotenv_values


SCHEMA_VERSION = "vkpi-release-drain/v1"
SAFE_ENV_MODES = {0o400, 0o440, 0o600, 0o640}
MAX_ENV_BYTES = 64 * 1024

MIGRATION_NAME = re.compile(r"^[0-9]{3}[a-z]?_[A-Za-z0-9_-]+\.sql$")
SAFE_DATABASE_QUERY_PARAMETERS = {
    "application_name",
    "channel_binding",
    "connect_timeout",
    "fallback_application_name",
    "gssencmode",
    "keepalives",
    "keepalives_count",
    "keepalives_idle",
    "keepalives_interval",
    "ssl_min_protocol_version",
    "ssl_max_protocol_version",
    "sslcrl",
    "sslcrldir",
    "sslmode",
    "sslrootcert",
    "sslsni",
    "tcp_user_timeout",
}

# The helper runs against the pre-migration source database.  This reviewed map
# is therefore the authority for whether an absent table is expected.  A table
# absent before its introduction is reported as unavailable/zero; a table that
# should already exist but does not is a fail-closed schema error.
TABLE_INTRODUCTION_MIGRATIONS: dict[str, str] = {
    "job_execution_ledger": "009_job_runtime_stack.sql",
    "apify_jobs": "095_apify_jobs.sql",  # gitleaks:allow - migration filename, not a credential
    "vkpi_action_inbox": "141_vkpi_action_inbox.sql",
    "vkpi_llm_batches": "166_vkpi_llm_batches.sql",
    "vkpi_agent_orchestration_plan": "180_vkpi_agent_orchestration.sql",
    "vkpi_agent_tool_run": "180_vkpi_agent_orchestration.sql",
    "vkpi_workflow_runs": "193_vkpi_workflow_runs.sql",
    "vkpi_advisor_turn_claims": "252_vkpi_advisor_turn_claims.sql",
    "vkpi_provider_execution_claims": "254_vkpi_provider_execution_fencing.sql",
    "vkpi_apify_budget_reservations": "254_vkpi_provider_execution_fencing.sql",
    "vkpi_llm_budget_reservations": "258_vkpi_llm_budget_reservations.sql",
}

# The unfenced migration-193 workflow state remains blocking until migration
# 265 adds an observable lease: before then, status=running may still represent
# real execution.  The other `blocking=False` states are deliberate: migration
# 180 is PLAN-ONLY, an approved tool is not executing, an external LLM batch
# remains durable while its poller is stopped, and a 265+ expired/unleased
# workflow cannot own a live execution lease.  Provider-started Advisor turns
# and open Apify/LLM budget reservations remain blocking without a lease test:
# once provider I/O starts, a stopped local process or expired local lease is
# not proof that the external execution or billing boundary has settled.
DB_COUNT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "key": "apify_jobs_active",
        "table": "apify_jobs",
        "available_from": "095_apify_jobs.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.apify_jobs WHERE status IN ('queued','running')",
    },
    {
        "key": "job_ledger_active",
        "table": "job_execution_ledger",
        "available_from": "009_job_runtime_stack.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.job_execution_ledger "
        "WHERE status IN ('queued','retrying','processing','running')",
    },
    {
        "key": "action_executions_active",
        "table": "vkpi_action_inbox",
        "available_from": "141_vkpi_action_inbox.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.vkpi_action_inbox WHERE status='executing'",
    },
    {
        "key": "workflow_runs_unfenced",
        "table": "vkpi_workflow_runs",
        "available_from": "193_vkpi_workflow_runs.sql",
        "retired_from": "265_vkpi_workflow_execution_fencing.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.vkpi_workflow_runs WHERE status='running'",
    },
    {
        "key": "workflow_runs_live",
        "table": "vkpi_workflow_runs",
        "available_from": "265_vkpi_workflow_execution_fencing.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.vkpi_workflow_runs "
        "WHERE status='running' AND lease_expires_at>NOW()",
    },
    {
        "key": "workflow_runs_expired_or_unleased",
        "table": "vkpi_workflow_runs",
        "available_from": "265_vkpi_workflow_execution_fencing.sql",
        "blocking": False,
        "sql": "SELECT COUNT(*) FROM public.vkpi_workflow_runs WHERE status='running' "
        "AND (lease_expires_at IS NULL OR lease_expires_at<=NOW())",
    },
    {
        "key": "agent_plans_executing_plan_only",
        "table": "vkpi_agent_orchestration_plan",
        "available_from": "180_vkpi_agent_orchestration.sql",
        "blocking": False,
        "sql": "SELECT COUNT(*) FROM public.vkpi_agent_orchestration_plan "
        "WHERE status='executing'",
    },
    {
        "key": "agent_tool_runs_approved_plan_only",
        "table": "vkpi_agent_tool_run",
        "available_from": "180_vkpi_agent_orchestration.sql",
        "blocking": False,
        "sql": "SELECT COUNT(*) FROM public.vkpi_agent_tool_run WHERE status='approved'",
    },
    {
        "key": "llm_batches_in_progress_durable",
        "table": "vkpi_llm_batches",
        "available_from": "166_vkpi_llm_batches.sql",
        "blocking": False,
        "sql": "SELECT COUNT(*) FROM public.vkpi_llm_batches WHERE status='in_progress'",
    },
    {
        "key": "advisor_turns_provider_started",
        "table": "vkpi_advisor_turn_claims",
        "available_from": "252_vkpi_advisor_turn_claims.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.vkpi_advisor_turn_claims "
        "WHERE state='provider_started'",
    },
    {
        "key": "provider_claims_live",
        "table": "vkpi_provider_execution_claims",
        "available_from": "254_vkpi_provider_execution_fencing.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.vkpi_provider_execution_claims "
        "WHERE state='active' AND lease_expires_at>NOW()",
    },
    {
        "key": "apify_budget_reservations_open",
        "table": "vkpi_apify_budget_reservations",
        "available_from": "254_vkpi_provider_execution_fencing.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.vkpi_apify_budget_reservations "
        "WHERE state IN ('reserved','provider_started','unknown')",
    },
    {
        "key": "llm_budget_reservations_open",
        "table": "vkpi_llm_budget_reservations",
        "available_from": "258_vkpi_llm_budget_reservations.sql",
        "blocking": True,
        "sql": "SELECT COUNT(*) FROM public.vkpi_llm_budget_reservations "
        "WHERE state IN ('reserved','provider_started','unknown')",
    },
)


class DrainProbeError(RuntimeError):
    """Fail-closed release-drain probe error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value or "")


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise DrainProbeError(f"{field} is not an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DrainProbeError(f"{field} is not an integer") from exc
    if result < 0:
        raise DrainProbeError(f"{field} is negative")
    return result


def _optional_nonnegative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field=field)


def _mapping_value(row: Mapping[Any, Any], key: str) -> Any:
    if key in row:
        return row[key]
    encoded = key.encode("utf-8")
    return row.get(encoded)


def read_protected_env(path: Path) -> dict[str, str]:
    """Read one private, single-link dotenv without following symlinks."""

    env_path = Path(path).expanduser()
    if not env_path.is_absolute():
        raise DrainProbeError("--env-file must be an absolute path")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(env_path, flags)
    except OSError as exc:
        raise DrainProbeError("protected environment is unreadable") from exc
    try:
        metadata = os.fstat(descriptor)
        effective_uid = os.geteuid() if hasattr(os, "geteuid") else os.getuid()
        effective_gid = os.getegid() if hasattr(os, "getegid") else os.getgid()
        trusted_groups = {effective_gid, *getattr(os, "getgroups", lambda: [])()}
        mode = stat.S_IMODE(metadata.st_mode)
        if metadata.st_uid == effective_uid:
            access_shape_is_trusted = mode in {0o400, 0o600}
        else:
            access_shape_is_trusted = (
                metadata.st_uid == 0
                and mode in {0o440, 0o640}
                and bool(mode & stat.S_IRGRP)
                and metadata.st_gid in trusted_groups
            )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or mode not in SAFE_ENV_MODES
            or not access_shape_is_trusted
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_ENV_BYTES
        ):
            raise DrainProbeError(
                "environment must be trusted-owner/private-group, single-link, and regular"
            )
        chunks: list[bytes] = []
        remaining = MAX_ENV_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > MAX_ENV_BYTES:
            raise DrainProbeError("protected environment exceeds 64 KiB")
        text = encoded.decode("utf-8")
    finally:
        os.close(descriptor)

    critical = {
        "DATABASE_URL",
        "REDIS_URL",
        "APP_STACK_NAME",
        "REDIS_NAMESPACE",
        "REDIS_JOB_STREAM_KEY",
        "REDIS_JOB_GROUP",
    }
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in critical:
            if key in seen:
                raise DrainProbeError("protected environment has duplicate critical keys")
            seen.add(key)
    parsed = dotenv_values(stream=StringIO(text))
    values = {
        str(key): str(value)
        for key, value in parsed.items()
        if key and value is not None
    }
    for required in ("DATABASE_URL", "REDIS_URL"):
        if not values.get(required, "").strip():
            raise DrainProbeError(f"protected environment is missing {required}")
    return values


def evaluate_redis_drain(
    *,
    pending_count: int,
    undelivered_count: int,
    last_delivered_id: str,
    raw_xinfo_lag: int | None,
    raw_xinfo_consumer_count: int | None,
    historical_consumer_count: int,
) -> dict[str, Any]:
    """Evaluate only real pending/undelivered work; lag/consumer totals diagnose."""

    blocking_reasons: list[str] = []
    if pending_count != 0:
        blocking_reasons.append("redis_pending_not_zero")
    if undelivered_count != 0:
        blocking_reasons.append("redis_undelivered_after_last_delivered")
    return {
        "passed": not blocking_reasons,
        "pending_count": pending_count,
        "undelivered_count": undelivered_count,
        "last_delivered_id": last_delivered_id,
        "blocking_reasons": blocking_reasons,
        "diagnostics": {
            "raw_xinfo_lag": raw_xinfo_lag,
            "raw_xinfo_consumer_count": raw_xinfo_consumer_count,
            "historical_consumer_count": historical_consumer_count,
            "lag_or_consumer_count_blocks_release": False,
        },
        "read_only": True,
    }


def collect_redis_state(client: Any, *, stream_key: str, group_name: str) -> dict[str, Any]:
    """Inspect an existing Redis stream/group without mutating queue history."""

    pong = client.ping()
    if pong is not True and _text(pong).upper() != "PONG":
        raise DrainProbeError("Redis ping did not return PONG")
    groups = client.xinfo_groups(stream_key)
    matches = [
        row
        for row in groups
        if isinstance(row, Mapping) and _text(_mapping_value(row, "name")) == group_name
    ]
    if len(matches) != 1:
        raise DrainProbeError("reviewed Redis consumer group is missing or ambiguous")
    group = matches[0]
    last_delivered_id = _text(_mapping_value(group, "last-delivered-id")).strip()
    if not last_delivered_id or "-" not in last_delivered_id:
        raise DrainProbeError("Redis last-delivered-id is invalid")

    pending_summary = client.xpending(stream_key, group_name)
    if isinstance(pending_summary, Mapping):
        pending_from_command = _nonnegative_int(
            _mapping_value(pending_summary, "pending"), field="Redis pending_count"
        )
    else:
        pending_from_command = _nonnegative_int(
            pending_summary, field="Redis pending_count"
        )
    pending_from_group = _nonnegative_int(
        _mapping_value(group, "pending"), field="Redis XINFO pending"
    )
    pending_count = max(pending_from_command, pending_from_group)

    # Exclusive lower bound is the semantic proof.  XINFO lag can be stale or
    # unavailable after stream trimming and is intentionally diagnostic only.
    undelivered = client.xrange(
        stream_key,
        min=f"({last_delivered_id}",
        max="+",
        count=1,
    )
    consumers = client.xinfo_consumers(stream_key, group_name)
    return evaluate_redis_drain(
        pending_count=pending_count,
        undelivered_count=len(undelivered or []),
        last_delivered_id=last_delivered_id,
        raw_xinfo_lag=_optional_nonnegative_int(
            _mapping_value(group, "lag"), field="Redis XINFO lag"
        ),
        raw_xinfo_consumer_count=_optional_nonnegative_int(
            _mapping_value(group, "consumers"), field="Redis XINFO consumers"
        ),
        historical_consumer_count=len(consumers or []),
    )


def _validated_current_migration(value: str) -> str:
    current = str(value or "").strip()
    if not MIGRATION_NAME.fullmatch(current):
        raise DrainProbeError("--current-migration is invalid")
    return current


def _migration_order(value: str) -> tuple[int, str]:
    match = MIGRATION_NAME.fullmatch(value)
    if match is None:
        raise DrainProbeError("reviewed migration name is invalid")
    prefix = value.split("_", 1)[0]
    number = int(prefix[:3])
    suffix = prefix[3:]
    return number, suffix


def _migration_at_least(current: str, required: str) -> bool:
    return _migration_order(current) >= _migration_order(required)


def _table_exists(connection: Any, table: str) -> bool:
    row = connection.execute(
        "SELECT pg_catalog.to_regclass(%s) IS NOT NULL", (f"public.{table}",)
    ).fetchone()
    if not row or not isinstance(row[0], bool):
        raise DrainProbeError("PostgreSQL table-presence result is invalid")
    return row[0]


def collect_db_state(
    connection: Any,
    *,
    expected_database: str,
    current_migration: str,
) -> dict[str, Any]:
    """Collect migration-aware counts inside one read-only transaction.

    The explicit migration is bound to the same source database queried by the
    probe.  This prevents a candidate helper from interpreting a legacy schema
    as current, while still allowing reviewed future tables to be absent before
    their introduction migration.
    """

    expected = _validated_database_name(expected_database)
    current = _validated_current_migration(current_migration)
    try:
        readonly = connection.execute("SHOW transaction_read_only").fetchone()
        if not readonly or _text(readonly[0]).lower() not in {"on", "true", "1"}:
            raise DrainProbeError("PostgreSQL transaction is not read-only")

        search_path_row = connection.execute("SHOW search_path").fetchone()
        search_path = re.sub(r"\s+", "", _text(search_path_row[0] if search_path_row else ""))
        if search_path != "pg_catalog,public":
            raise DrainProbeError("PostgreSQL search_path is not fixed to pg_catalog,public")

        database_row = connection.execute(
            "SELECT pg_catalog.current_database()"
        ).fetchone()
        if not database_row or _text(database_row[0]) != expected:
            raise DrainProbeError("connected PostgreSQL database does not match expected database")

        migration_row = connection.execute(
            "SELECT MAX(version_key) FROM public.schema_migrations"
        ).fetchone()
        database_migration = _validated_current_migration(
            _text(migration_row[0] if migration_row else "")
        )
        if database_migration != current:
            raise DrainProbeError(
                "explicit current migration does not match the source database"
            )

        table_states: dict[str, dict[str, Any]] = {}
        for table, introduced_by in TABLE_INTRODUCTION_MIGRATIONS.items():
            expected = _migration_at_least(current, introduced_by)
            present = _table_exists(connection, table)
            if expected and not present:
                raise DrainProbeError("expected reviewed drain table is missing")
            if expected:
                status = "available"
            elif present:
                # A manually pre-created relation does not make its later
                # column contract safe to query.  Keep it diagnostic and skip
                # all checks until the reviewed introduction is recorded.
                status = "present_before_introduction"
            else:
                status = "not_introduced"
            table_states[table] = {
                "introduced_by": introduced_by,
                "expected": expected,
                "present": present,
                "status": status,
            }

        active_counts: dict[str, int] = {}
        diagnostic_counts: dict[str, int] = {}
        check_status: dict[str, str] = {}
        for spec in DB_COUNT_SPECS:
            key = str(spec["key"])
            blocking = bool(spec["blocking"])
            target = active_counts if blocking else diagnostic_counts
            if not _migration_at_least(current, str(spec["available_from"])):
                target[key] = 0
                check_status[key] = "not_introduced"
                continue
            retired_from = str(spec.get("retired_from") or "")
            if retired_from and _migration_at_least(current, retired_from):
                target[key] = 0
                check_status[key] = "superseded"
                continue
            table_state = table_states[str(spec["table"])]
            if table_state["status"] != "available":
                raise DrainProbeError("reviewed drain table is not queryable")
            row = connection.execute(str(spec["sql"])).fetchone()
            target[key] = _nonnegative_int(
                row[0] if row else None, field=f"database count {key}"
            )
            check_status[key] = "queried"

        blocking_keys = [
            key for key, value in active_counts.items() if value != 0
        ]
        diagnostic_nonzero = [
            key for key, value in diagnostic_counts.items() if value != 0
        ]
        return {
            "passed": not blocking_keys,
            "current_migration": current,
            "active_counts": active_counts,
            "diagnostic_counts": diagnostic_counts,
            "check_status": check_status,
            "tables": table_states,
            "blocking_reasons": [
                f"database_{key}_not_zero" for key in blocking_keys
            ],
            "diagnostic_nonzero": diagnostic_nonzero,
            "database_identity_verified": True,
            "search_path_verified": True,
            "read_only": True,
        }
    finally:
        connection.rollback()


def _validated_database_name(value: str) -> str:
    database = str(value or "").strip()
    if not database or "/" in database or "\x00" in database:
        raise DrainProbeError("expected database name is invalid")
    return database


def _validated_database_url(value: str, expected_database: str) -> str:
    expected = _validated_database_name(expected_database)
    try:
        parsed = urlsplit(str(value or "").strip())
        query_parameters = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
        )
    except ValueError as exc:
        raise DrainProbeError("DATABASE_URL is invalid") from exc
    database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    unsafe_query_keys = {
        key.lower()
        for key, _value in query_parameters
        if key.lower() not in SAFE_DATABASE_QUERY_PARAMETERS
    }
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not database
        or "/" in database
        or database != expected
    ):
        raise DrainProbeError("DATABASE_URL does not name the expected current database")
    if unsafe_query_keys:
        raise DrainProbeError("DATABASE_URL query parameters may alter connection identity")
    return str(value).strip()


def _validated_redis_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError as exc:
        raise DrainProbeError("REDIS_URL is invalid") from exc
    if parsed.scheme not in {"redis", "rediss", "unix"}:
        raise DrainProbeError("REDIS_URL scheme is invalid")
    if parsed.scheme != "unix" and not parsed.hostname:
        raise DrainProbeError("REDIS_URL host is missing")
    return str(value).strip()


def audit_release_drain(
    *,
    env_file: Path,
    expected_database: str,
    current_migration: str,
    redis_factory: Callable[..., Any] | None = None,
    database_connect: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    values = read_protected_env(env_file)
    reviewed_current_migration = _validated_current_migration(current_migration)
    database_url = _validated_database_url(
        values["DATABASE_URL"], str(expected_database or "").strip()
    )
    redis_url = _validated_redis_url(values["REDIS_URL"])
    app_stack = values.get("APP_STACK_NAME", "viltrox-2.0").strip() or "viltrox-2.0"
    namespace = values.get("REDIS_NAMESPACE", f"{app_stack}:runtime").strip()
    namespace = namespace or f"{app_stack}:runtime"
    stream_key = values.get("REDIS_JOB_STREAM_KEY", f"{namespace}:jobs:stream").strip()
    stream_key = stream_key or f"{namespace}:jobs:stream"
    group_name = values.get("REDIS_JOB_GROUP", f"{app_stack}-workers").strip()
    group_name = group_name or f"{app_stack}-workers"

    if redis_factory is None:
        from redis import Redis  # noqa: PLC0415

        redis_factory = Redis.from_url
    if database_connect is None:
        import psycopg  # noqa: PLC0415

        database_connect = psycopg.connect

    client = redis_factory(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=False,
    )
    try:
        redis_state = collect_redis_state(
            client, stream_key=stream_key, group_name=group_name
        )
    finally:
        client.close()

    with database_connect(
        database_url,
        connect_timeout=5,
        options=(
            "-c default_transaction_read_only=on "
            "-c search_path=pg_catalog,public "
            "-c statement_timeout=5000 -c lock_timeout=1000"
        ),
    ) as connection:
        database_state = collect_db_state(
            connection,
            expected_database=str(expected_database or "").strip(),
            current_migration=reviewed_current_migration,
        )

    blocking = [
        *redis_state["blocking_reasons"],
        *database_state["blocking_reasons"],
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "overall": {"pass": not blocking, "blocking_reasons": blocking},
        "redis": redis_state,
        "database": database_state,
        "read_only": True,
        "history_mutated": False,
        "credentials_emitted": False,
    }


def _emit(payload: Mapping[str, Any], *, stream: Any = None) -> None:
    target = sys.stdout if stream is None else stream
    target.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the read-only release drain boundary")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--current-migration", required=True)
    args = parser.parse_args(argv)
    try:
        payload = audit_release_drain(
            env_file=args.env_file,
            expected_database=args.expected_database,
            current_migration=args.current_migration,
        )
    except Exception as exc:  # noqa: BLE001 - CLI output must stay bounded/redacted.
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _utc_now(),
            "overall": {"pass": False, "blocking_reasons": ["probe_error"]},
            "error_type": type(exc).__name__,
            "read_only": True,
            "history_mutated": False,
            "credentials_emitted": False,
        }
        _emit(payload)
        sys.stderr.write("release drain verification failed\n")
        return 2
    _emit(payload)
    if payload["overall"]["pass"] is not True:
        sys.stderr.write("release drain is not empty\n")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
