"""Durable claim, lease and fencing primitives for ``workflow_engine``.

Migration 265 is the sole DDL owner.  This repository deliberately fails
closed when that schema is absent: falling back to the pre-265 engine would
allow two workers to execute and checkpoint the same logical step.

The raw lease token is returned only to the claiming process.  PostgreSQL and
SQLite persist its SHA-256 digest, and every mutation after claim is a fenced
compare-and-swap over ``run_id + owner + token hash + fence + live lease``.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime, table_exists


_RUNS = "vkpi_workflow_runs"
_STEPS = "vkpi_workflow_steps"
_CHECKPOINTS = "vkpi_workflow_checkpoints"
_MIN_LEASE_SECONDS = 30
_MAX_LEASE_SECONDS = 3600


class WorkflowSchemaUnavailable(RuntimeError):
    """Migration 265 is not present or is incomplete."""


@dataclass(frozen=True, slots=True)
class WorkflowClaim:
    run_id: int
    owner_id: str
    lease_token: str
    fence_token: int
    current_step: int
    attempt_no: int
    lease_expires_at: str
    recovered: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _time_param(value: datetime) -> datetime | str:
    return value if is_postgres_runtime() else _iso(value)


def _lease_seconds(value: int) -> int:
    return max(_MIN_LEASE_SECONDS, min(_MAX_LEASE_SECONDS, int(value or 300)))


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_placeholder() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def lease_is_expired(value: Any, *, now: datetime | None = None) -> bool:
    """Normalize compat timestamp values for read-only recovery reporting."""

    if isinstance(value, datetime):
        expiry = value
    else:
        text = str(value or "").strip()
        if not text:
            return True
        try:
            expiry = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc) <= (now or _utcnow())


def schema_ready() -> bool:
    if not all(table_exists(name) for name in (_RUNS, _STEPS, _CHECKPOINTS)):
        return False
    conn = get_conn()
    try:
        conn.execute(
            f"SELECT lease_owner, lease_token_hash, fence_token, lease_expires_at, "
            f"heartbeat_at, attempt_no, row_version FROM {_RUNS} WHERE 1=0"
        )
        conn.execute(f"SELECT fence_token FROM {_STEPS} WHERE 1=0")
        conn.execute(f"SELECT fence_token FROM {_CHECKPOINTS} WHERE 1=0")
        if is_postgres_runtime():
            expected_constraints = {
                "ck_vkpi_workflow_run_fence_nonnegative",
                "ck_vkpi_workflow_run_attempt_nonnegative",
                "ck_vkpi_workflow_run_version_nonnegative",
                "ck_vkpi_workflow_run_lease_identity",
                "fk_vkpi_workflow_step_run",
                "ck_vkpi_workflow_step_fence_nonnegative",
                "fk_vkpi_workflow_checkpoint_run",
                "ck_vkpi_workflow_checkpoint_fence_nonnegative",
            }
            constraint_rows = conn.execute(
                "SELECT constraint_row.conname "
                "FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS relation ON relation.oid=constraint_row.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() AND constraint_row.conname IN ("
                + ",".join("?" for _ in expected_constraints)
                + ")",
                tuple(sorted(expected_constraints)),
            ).fetchall()
            present_constraints = {
                str(dict(row).get("conname") or "") for row in constraint_rows
            }
            index_rows = conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() "
                "AND indexname IN ('uq_vkpi_workflow_step_once',"
                "'uq_vkpi_workflow_checkpoint_once')"
            ).fetchall()
            present_indexes = {
                str(dict(row).get("indexname") or "") for row in index_rows
            }
            return present_constraints == expected_constraints and present_indexes == {
                "uq_vkpi_workflow_step_once",
                "uq_vkpi_workflow_checkpoint_once",
            }
        for table in (_STEPS, _CHECKPOINTS):
            unique_pair = False
            for index_row in conn.execute(f"PRAGMA index_list({table})").fetchall():
                index = dict(index_row)
                if not bool(index.get("unique")):
                    continue
                columns = [
                    str(dict(column).get("name") or "")
                    for column in conn.execute(
                        f"PRAGMA index_info({index.get('name')})"
                    ).fetchall()
                ]
                if columns == ["run_id", "step_index"]:
                    unique_pair = True
                    break
            has_run_fk = any(
                str(dict(row).get("table") or "") == _RUNS
                and str(dict(row).get("from") or "") == "run_id"
                and str(dict(row).get("to") or "") == "id"
                for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            )
            if not unique_pair or not has_run_fk:
                return False
        return True
    except Exception:
        conn.rollback()
        return False


def ensure_schema() -> None:
    if not schema_ready():
        raise WorkflowSchemaUnavailable(
            "migration 265_vkpi_workflow_execution_fencing.sql is not applied"
        )


def create_run(
    workflow_name: str,
    *,
    input_value: dict[str, Any] | None,
    entity_type: str,
    entity_id: str,
    organization_id: int,
    trace_id: str,
) -> dict[str, Any]:
    ensure_schema()
    conn = get_conn()
    now = _utcnow()
    placeholder = _json_placeholder()
    try:
        row = conn.execute(
            f"INSERT INTO {_RUNS} "
            f"(organization_id, workflow_name, status, input_json, entity_type, entity_id, "
            f"trace_id, created_at, updated_at) "
            f"VALUES (?,?,?,{placeholder},?,?,?,?,?) RETURNING id, trace_id",
            (
                int(organization_id),
                str(workflow_name),
                "running",
                _json(input_value or {}),
                str(entity_type or ""),
                str(entity_id or ""),
                str(trace_id or ""),
                _time_param(now),
                _time_param(now),
            ),
        ).fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"id": int(dict(row)["id"]), "trace_id": str(dict(row).get("trace_id") or "")}


def get_run(run_id: int) -> dict[str, Any] | None:
    ensure_schema()
    row = get_conn().execute(
        f"SELECT * FROM {_RUNS} WHERE id=?",
        (int(run_id),),
    ).fetchone()
    return dict(row) if row else None


def list_recoverable_runs(
    *,
    limit: int = 20,
    minimum_age_seconds: int = 60,
    workflow_name: str = "",
) -> list[dict[str, Any]]:
    """Return resumable rows without claiming them.

    The subsequent ``claim_run`` CAS is the authority; this query is only a
    bounded recovery candidate scan.  Failed/paused rows and running rows with
    no live lease are eligible after a short cooldown so a permanently broken
    callback cannot be hammered in a tight scheduler loop.
    """

    ensure_schema()
    now = _utcnow()
    cutoff = now - timedelta(seconds=max(0, int(minimum_age_seconds or 0)))
    clauses = [
        "status IN ('running','failed','paused')",
        "updated_at<=?",
        "(status IN ('failed','paused') OR lease_token_hash IS NULL "
        "OR lease_expires_at IS NULL OR lease_expires_at<=?)",
    ]
    params: list[Any] = [_time_param(cutoff), _time_param(now)]
    clean_name = str(workflow_name or "").strip()
    if clean_name:
        clauses.append("workflow_name=?")
        params.append(clean_name)
    params.append(max(1, min(int(limit or 20), 200)))
    rows = get_conn().execute(
        f"SELECT id, organization_id, workflow_name, status, input_json, current_step, "
        f"entity_type, entity_id, trace_id, last_error, lease_owner, lease_expires_at, "
        f"fence_token, attempt_no, updated_at FROM {_RUNS} "
        f"WHERE {' AND '.join(clauses)} ORDER BY updated_at ASC, id ASC LIMIT ?",
        tuple(params),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["input"] = _decode_json(item.pop("input_json", {}))
        result.append(item)
    return result


def find_unfinished_run(
    workflow_name: str,
    *,
    organization_id: int = 1,
) -> dict[str, Any] | None:
    """Find the oldest unfinished run for scheduler deduplication.

    This deliberately includes a currently live lease.  A scheduled tick must
    report that run as in progress instead of creating a second logical run.
    """

    ensure_schema()
    row = get_conn().execute(
        f"SELECT id, organization_id, workflow_name, status, input_json, current_step, "
        f"entity_type, entity_id, trace_id, last_error, lease_owner, lease_expires_at, "
        f"fence_token, attempt_no, updated_at FROM {_RUNS} "
        f"WHERE workflow_name=? AND organization_id=? "
        f"AND status IN ('running','failed','paused') "
        f"ORDER BY created_at ASC, id ASC LIMIT 1",
        (str(workflow_name or "").strip(), int(organization_id or 1)),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["input"] = _decode_json(item.pop("input_json", {}))
    return item


def latest_state(run_id: int, input_value: Any) -> dict[str, Any]:
    ensure_schema()
    row = get_conn().execute(
        f"SELECT state_json FROM {_CHECKPOINTS} "
        f"WHERE run_id=? ORDER BY step_index DESC, id DESC LIMIT 1",
        (int(run_id),),
    ).fetchone()
    if row:
        return _decode_json(dict(row).get("state_json"))
    return _decode_json(input_value)


def claim_run(
    run_id: int,
    owner_id: str,
    *,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Claim an unowned/expired resumable run and advance its fencing token."""

    ensure_schema()
    clean_owner = str(owner_id or "").strip()[:240]
    if not clean_owner:
        raise ValueError("workflow lease owner is required")
    raw_token = secrets.token_urlsafe(32)
    digest = _token_hash(raw_token)
    now = _utcnow()
    expires = now + timedelta(seconds=_lease_seconds(lease_seconds))
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET status='running', lease_owner=?, lease_token_hash=?,
                fence_token=fence_token+1, lease_expires_at=?, heartbeat_at=?,
                attempt_no=attempt_no+1, row_version=row_version+1,
                last_error='', updated_at=?
            WHERE id=?
              AND status IN ('running','failed','paused')
              AND (
                    lease_token_hash IS NULL
                    OR lease_expires_at IS NULL
                    OR lease_expires_at<=?
              )
            RETURNING id, fence_token, current_step, attempt_no, lease_expires_at
            """,
            (
                clean_owner,
                digest,
                _time_param(expires),
                _time_param(now),
                _time_param(now),
                int(run_id),
                _time_param(now),
            ),
        ).fetchone()
        if row is not None:
            conn.commit()
            data = dict(row)
            fence = int(data.get("fence_token") or 0)
            return {
                "status": "acquired",
                "claim": WorkflowClaim(
                    run_id=int(data.get("id") or run_id),
                    owner_id=clean_owner,
                    lease_token=raw_token,
                    fence_token=fence,
                    current_step=int(data.get("current_step") or 0),
                    attempt_no=int(data.get("attempt_no") or 0),
                    lease_expires_at=str(data.get("lease_expires_at") or _iso(expires)),
                    recovered=fence > 1,
                ),
            }
        current = conn.execute(
            f"SELECT status, lease_owner, lease_expires_at, fence_token, current_step "
            f"FROM {_RUNS} WHERE id=?",
            (int(run_id),),
        ).fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if current is None:
        return {"status": "not_found", "reason": "workflow_run_not_found"}
    data = dict(current)
    if str(data.get("status") or "") == "completed":
        return {
            "status": "completed",
            "current_step": int(data.get("current_step") or 0),
            "fence_token": int(data.get("fence_token") or 0),
        }
    return {
        "status": "in_progress",
        "reason": "workflow_live_lease",
        "lease_owner": str(data.get("lease_owner") or ""),
        "lease_expires_at": str(data.get("lease_expires_at") or ""),
        "fence_token": int(data.get("fence_token") or 0),
        "current_step": int(data.get("current_step") or 0),
    }


def _claim_params(claim: WorkflowClaim, now: datetime) -> tuple[Any, ...]:
    return (
        int(claim.run_id),
        str(claim.owner_id),
        _token_hash(claim.lease_token),
        int(claim.fence_token),
        _time_param(now),
    )


def claim_is_live(claim: WorkflowClaim) -> bool:
    ensure_schema()
    now = _utcnow()
    row = get_conn().execute(
        f"SELECT id FROM {_RUNS} WHERE id=? AND lease_owner=? AND lease_token_hash=? "
        f"AND fence_token=? AND status='running' AND lease_expires_at>?",
        _claim_params(claim, now),
    ).fetchone()
    return row is not None


def renew_claim(claim: WorkflowClaim, *, lease_seconds: int = 300) -> bool:
    """Renew only a still-live claim; an expired/stolen token cannot resurrect."""

    ensure_schema()
    now = _utcnow()
    expires = now + timedelta(seconds=_lease_seconds(lease_seconds))
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET lease_expires_at=?, heartbeat_at=?, row_version=row_version+1, updated_at=?
            WHERE id=? AND lease_owner=? AND lease_token_hash=? AND fence_token=?
              AND status='running' AND lease_expires_at>?
            RETURNING id
            """,
            (
                _time_param(expires),
                _time_param(now),
                _time_param(now),
                *_claim_params(claim, now),
            ),
        ).fetchone()
        conn.commit()
        return row is not None
    except Exception:
        conn.rollback()
        raise


def begin_step(claim: WorkflowClaim, step_index: int, step_name: str) -> bool:
    """Open one logical step only while the exact run fence is live."""

    ensure_schema()
    index = int(step_index)
    if index < 0:
        raise ValueError("workflow step_index must be nonnegative")
    now = _utcnow()
    conn = get_conn()
    try:
        locked = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET row_version=row_version+1, updated_at=?
            WHERE id=? AND lease_owner=? AND lease_token_hash=? AND fence_token=?
              AND status='running' AND lease_expires_at>? AND current_step=?
            RETURNING id
            """,
            (
                _time_param(now),
                *_claim_params(claim, now),
                index,
            ),
        ).fetchone()
        if locked is None:
            conn.rollback()
            return False
        step = conn.execute(
            f"""
            INSERT INTO {_STEPS}
              (run_id, step_index, step_name, status, error, started_at, finished_at, fence_token)
            VALUES (?, ?, ?, 'running', '', ?, NULL, ?)
            ON CONFLICT (run_id, step_index) DO UPDATE SET
              step_name=excluded.step_name,
              status='running',
              output_json={"'{}'::jsonb" if is_postgres_runtime() else "'{}'"},
              error='',
              started_at=excluded.started_at,
              finished_at=NULL,
              fence_token=excluded.fence_token
            WHERE {_STEPS}.status<>'done'
            RETURNING id
            """,
            (
                int(claim.run_id),
                index,
                str(step_name or "")[:200],
                _time_param(now),
                int(claim.fence_token),
            ),
        ).fetchone()
        if step is None:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def commit_step(
    claim: WorkflowClaim,
    step_index: int,
    *,
    output: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    """Atomically persist output/checkpoint and advance the run under one fence."""

    ensure_schema()
    index = int(step_index)
    now = _utcnow()
    conn = get_conn()
    placeholder = _json_placeholder()
    try:
        step = conn.execute(
            f"""
            UPDATE {_STEPS}
            SET status='done', output_json={placeholder}, error='', finished_at=?
            WHERE run_id=? AND step_index=? AND fence_token=? AND status='running'
            RETURNING id
            """,
            (
                _json(output),
                _time_param(now),
                int(claim.run_id),
                index,
                int(claim.fence_token),
            ),
        ).fetchone()
        if step is None:
            conn.rollback()
            return False
        checkpoint = conn.execute(
            f"""
            INSERT INTO {_CHECKPOINTS} (run_id, step_index, state_json, fence_token, created_at)
            VALUES (?, ?, {placeholder}, ?, ?)
            ON CONFLICT (run_id, step_index) DO UPDATE SET
              state_json=excluded.state_json,
              fence_token=excluded.fence_token,
              created_at=excluded.created_at
            WHERE {_CHECKPOINTS}.fence_token=excluded.fence_token
            RETURNING id
            """,
            (
                int(claim.run_id),
                index,
                _json(state),
                int(claim.fence_token),
                _time_param(now),
            ),
        ).fetchone()
        if checkpoint is None:
            conn.rollback()
            return False
        advanced = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET current_step=?, row_version=row_version+1, last_error='', updated_at=?
            WHERE id=? AND lease_owner=? AND lease_token_hash=? AND fence_token=?
              AND status='running' AND lease_expires_at>? AND current_step=?
            RETURNING id
            """,
            (
                index + 1,
                _time_param(now),
                *_claim_params(claim, now),
                index,
            ),
        ).fetchone()
        if advanced is None:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def fail_step(claim: WorkflowClaim, step_index: int, error: str) -> bool:
    """Record a failure only for the exact live owner; stale workers are fenced."""

    ensure_schema()
    index = int(step_index)
    message = str(error or "")[:480]
    now = _utcnow()
    conn = get_conn()
    try:
        step = conn.execute(
            f"""
            UPDATE {_STEPS}
            SET status='failed', error=?, finished_at=?
            WHERE run_id=? AND step_index=? AND fence_token=? AND status='running'
            RETURNING id
            """,
            (
                message,
                _time_param(now),
                int(claim.run_id),
                index,
                int(claim.fence_token),
            ),
        ).fetchone()
        if step is None:
            conn.rollback()
            return False
        failed = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET status='failed', last_error=?, lease_owner=NULL, lease_token_hash=NULL,
                lease_expires_at=NULL, heartbeat_at=NULL,
                row_version=row_version+1, updated_at=?
            WHERE id=? AND lease_owner=? AND lease_token_hash=? AND fence_token=?
              AND status='running' AND lease_expires_at>? AND current_step=?
            RETURNING id
            """,
            (
                message,
                _time_param(now),
                *_claim_params(claim, now),
                index,
            ),
        ).fetchone()
        if failed is None:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def complete_run(claim: WorkflowClaim, *, expected_steps: int) -> bool:
    ensure_schema()
    now = _utcnow()
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET status='completed', lease_owner=NULL, lease_token_hash=NULL,
                lease_expires_at=NULL, heartbeat_at=NULL,
                row_version=row_version+1, updated_at=?
            WHERE id=? AND lease_owner=? AND lease_token_hash=? AND fence_token=?
              AND status='running' AND lease_expires_at>? AND current_step>=?
            RETURNING id
            """,
            (
                _time_param(now),
                *_claim_params(claim, now),
                max(0, int(expected_steps)),
            ),
        ).fetchone()
        conn.commit()
        return row is not None
    except Exception:
        conn.rollback()
        raise


def list_steps(run_id: int) -> list[dict[str, Any]]:
    ensure_schema()
    return [
        dict(row)
        for row in get_conn().execute(
            f"SELECT step_index, step_name, status, error, started_at, finished_at, fence_token "
            f"FROM {_STEPS} WHERE run_id=? ORDER BY step_index",
            (int(run_id),),
        ).fetchall()
    ]


def checkpoint_count(run_id: int) -> int:
    ensure_schema()
    row = get_conn().execute(
        f"SELECT COUNT(*) AS n FROM {_CHECKPOINTS} WHERE run_id=?",
        (int(run_id),),
    ).fetchone()
    return int(dict(row).get("n") or 0) if row else 0
