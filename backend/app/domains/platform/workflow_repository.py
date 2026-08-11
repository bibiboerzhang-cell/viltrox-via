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
_EVENTS = "vkpi_event_ledger"
_COMPLETION_EVENT = "workflow_completed"
_COMPLETION_SOURCE = "workflow_engine"
_COMPLETION_INDEX = "uq_vkpi_workflow_completed_event"
_COMPLETION_TRIGGER = "trg_vkpi_workflow_completed_event_immutable"
_COMPLETED_RUN_TRIGGER = "trg_vkpi_completed_workflow_run_immutable"
_MIN_LEASE_SECONDS = 30
_MAX_LEASE_SECONDS = 3600


class WorkflowSchemaUnavailable(RuntimeError):
    """Workflow fencing or completion-evidence schema is incomplete."""


class WorkflowCompletionEvidenceError(RuntimeError):
    """A completed run lacks one exact, immutable terminal event."""

    def __init__(self, reason: str = "workflow_completion_evidence_required") -> None:
        self.reason = str(reason or "workflow_completion_evidence_required")
        super().__init__(self.reason)


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
    if not all(table_exists(name) for name in (_RUNS, _STEPS, _CHECKPOINTS, _EVENTS)):
        return False
    conn = get_conn()
    try:
        conn.execute(
            f"SELECT lease_owner, lease_token_hash, fence_token, lease_expires_at, "
            f"heartbeat_at, attempt_no, row_version FROM {_RUNS} WHERE 1=0"
        )
        conn.execute(f"SELECT fence_token FROM {_STEPS} WHERE 1=0")
        conn.execute(f"SELECT fence_token FROM {_CHECKPOINTS} WHERE 1=0")
        conn.execute(
            f"SELECT organization_id, event_type, entity_type, entity_id, actor_type, "
            f"actor_id, source, payload_json, trace_id, confidence, provenance_json "
            f"FROM {_EVENTS} WHERE 1=0"
        )
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
                "'uq_vkpi_workflow_checkpoint_once','uq_vkpi_workflow_completed_event')"
            ).fetchall()
            present_indexes = {
                str(dict(row).get("indexname") or "") for row in index_rows
            }
            trigger_rows = conn.execute(
                "SELECT trigger_name FROM information_schema.triggers "
                "WHERE event_object_schema=current_schema() AND trigger_name IN (?,?)",
                (_COMPLETION_TRIGGER, _COMPLETED_RUN_TRIGGER),
            ).fetchall()
            present_triggers = {
                str(dict(row).get("trigger_name") or "") for row in trigger_rows
            }
            return (
                present_constraints == expected_constraints
                and present_indexes == {
                    "uq_vkpi_workflow_step_once",
                    "uq_vkpi_workflow_checkpoint_once",
                    _COMPLETION_INDEX,
                }
                and present_triggers == {_COMPLETION_TRIGGER, _COMPLETED_RUN_TRIGGER}
            )
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
        completion_indexes = {
            str(dict(row).get("name") or "")
            for row in conn.execute(f"PRAGMA index_list({_EVENTS})").fetchall()
            if bool(dict(row).get("unique"))
        }
        if _COMPLETION_INDEX not in completion_indexes:
            return False
        return True
    except Exception:
        conn.rollback()
        return False


def ensure_schema() -> None:
    if not schema_ready():
        raise WorkflowSchemaUnavailable(
            "workflow migrations 265 and 281 are not fully applied"
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


def _completion_contract(row: dict[str, Any]) -> dict[str, Any]:
    run_id = int(row.get("id") or 0)
    organization_id = int(row.get("organization_id") or 0)
    workflow_name = str(row.get("workflow_name") or "").strip()
    entity_type = str(row.get("entity_type") or "").strip()
    entity_id = str(row.get("entity_id") or "").strip()
    trace_id = str(row.get("trace_id") or "").strip()
    fence_token = int(row.get("fence_token") or 0)
    steps = int(row.get("current_step") or 0)
    if (
        run_id <= 0
        or organization_id <= 0
        or not workflow_name
        or not trace_id
        or fence_token <= 0
        or steps < 0
        or str(row.get("status") or "") != "completed"
    ):
        raise WorkflowCompletionEvidenceError("workflow_completion_run_contract_invalid")
    payload = {
        "workflow": workflow_name,
        "steps": steps,
        "current_step": steps,
        "fence_token": fence_token,
        "entity_type": entity_type,
        "entity_id": entity_id,
    }
    provenance = {
        "evidence_verification": "server_bound_fenced_workflow_completion",
        "server_bound_run_id": run_id,
        "server_bound_entity_type": entity_type,
        "server_bound_entity_id": entity_id,
        "server_bound_current_step": steps,
        "fence_token": fence_token,
    }
    return {
        "run_id": run_id,
        "organization_id": organization_id,
        "trace_id": trace_id,
        "payload": payload,
        "provenance": provenance,
    }


def _completion_events(conn: Any, contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT id, organization_id, event_type, entity_type, entity_id, actor_type, "
        f"actor_id, source, payload_json, trace_id, confidence, provenance_json "
        f"FROM {_EVENTS} WHERE organization_id=? AND event_type=? "
        f"AND entity_type='workflow' AND entity_id=? AND source=? ORDER BY id",
        (
            int(contract["organization_id"]),
            _COMPLETION_EVENT,
            str(contract["run_id"]),
            _COMPLETION_SOURCE,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _completion_event_matches(event: dict[str, Any], contract: dict[str, Any]) -> bool:
    payload = _decode_json(event.get("payload_json"))
    provenance = _decode_json(event.get("provenance_json"))
    return bool(
        int(event.get("organization_id") or 0) == int(contract["organization_id"])
        and str(event.get("event_type") or "") == _COMPLETION_EVENT
        and str(event.get("entity_type") or "") == "workflow"
        and str(event.get("entity_id") or "") == str(contract["run_id"])
        and str(event.get("actor_type") or "") == "system"
        and str(event.get("actor_id") or "") == ""
        and str(event.get("source") or "") == _COMPLETION_SOURCE
        and str(event.get("trace_id") or "") == str(contract["trace_id"])
        and event.get("confidence") is None
        and payload == contract["payload"]
        and provenance == contract["provenance"]
    )


def _insert_completion_event(conn: Any, contract: dict[str, Any]) -> int:
    from app.domains.platform import event_ledger

    return event_ledger.insert_required(
        conn,
        _COMPLETION_EVENT,
        entity_type="workflow",
        entity_id=int(contract["run_id"]),
        actor_type="system",
        actor_id="",
        source=_COMPLETION_SOURCE,
        payload=dict(contract["payload"]),
        trace_id=str(contract["trace_id"]),
        provenance=dict(contract["provenance"]),
        organization_id=int(contract["organization_id"]),
    )


def _mark_completion_failed(
    conn: Any,
    claim: WorkflowClaim,
    *,
    expected_steps: int,
) -> None:
    """Release a still-live claim after terminal evidence failed to commit."""

    now = _utcnow()
    try:
        failed = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET status='failed', last_error='workflow_completion_evidence_required',
                lease_owner=NULL, lease_token_hash=NULL, lease_expires_at=NULL,
                heartbeat_at=NULL, row_version=row_version+1, updated_at=?
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
        if failed is None:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()


def ensure_completed_event(run_id: int) -> dict[str, Any]:
    """Verify or repair one completed run's exact terminal event.

    This path never changes workflow business state.  It exists only for rows
    completed by the old post-commit best-effort emitter or for an ambiguous
    commit response.  A conflicting or duplicate event blocks replay instead
    of being overwritten or silently accepted.
    """

    ensure_schema()
    conn = get_conn()
    try:
        if not is_postgres_runtime() and not bool(getattr(conn, "in_transaction", False)):
            conn.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if is_postgres_runtime() else ""
        raw = conn.execute(
            f"SELECT id, organization_id, workflow_name, status, current_step, "
            f"entity_type, entity_id, trace_id, fence_token FROM {_RUNS} WHERE id=?{lock}",
            (int(run_id),),
        ).fetchone()
        if raw is None or str(dict(raw).get("status") or "") != "completed":
            raise WorkflowCompletionEvidenceError("workflow_completed_run_required")
        contract = _completion_contract(dict(raw))
        events = _completion_events(conn, contract)
        if len(events) > 1:
            raise WorkflowCompletionEvidenceError("workflow_completion_event_duplicate")
        if events:
            if not _completion_event_matches(events[0], contract):
                raise WorkflowCompletionEvidenceError("workflow_completion_event_conflict")
            conn.commit()
            return {"event_id": int(events[0]["id"]), "repaired": False}
        event_id = _insert_completion_event(conn, contract)
        conn.commit()
        return {"event_id": event_id, "repaired": True}
    except WorkflowCompletionEvidenceError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        # A commit response can be lost after both rows became durable.  One
        # exact readback is safe; anything else remains blocked and retryable.
        try:
            raw = conn.execute(
                f"SELECT id, organization_id, workflow_name, status, current_step, "
                f"entity_type, entity_id, trace_id, fence_token FROM {_RUNS} WHERE id=?",
                (int(run_id),),
            ).fetchone()
            if raw is not None:
                contract = _completion_contract(dict(raw))
                events = _completion_events(conn, contract)
                if len(events) == 1 and _completion_event_matches(events[0], contract):
                    conn.commit()
                    return {"event_id": int(events[0]["id"]), "repaired": False}
            conn.rollback()
        except Exception:
            conn.rollback()
        raise WorkflowCompletionEvidenceError(
            "workflow_completion_evidence_required"
        ) from exc


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
            RETURNING id, organization_id, workflow_name, status, current_step,
                      entity_type, entity_id, trace_id, fence_token
            """,
            (
                _time_param(now),
                *_claim_params(claim, now),
                max(0, int(expected_steps)),
            ),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        contract = _completion_contract(dict(row))
        _insert_completion_event(conn, contract)
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        _mark_completion_failed(conn, claim, expected_steps=expected_steps)
        if isinstance(exc, WorkflowCompletionEvidenceError):
            raise
        raise WorkflowCompletionEvidenceError(
            "workflow_completion_evidence_required"
        ) from exc


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
