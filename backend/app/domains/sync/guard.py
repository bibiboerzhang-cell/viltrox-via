"""Run tracking, guard, and interrupt helpers for daily V-KPI sync."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import close_standalone_conn, get_conn, open_standalone_conn

logger = get_logger(__name__)


ENRICHABLE_KOL_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook", "reddit", "x"}
SYNC_FAIL_FAST_EXIT_CODE = 75
SYNC_GUARD_BLOCKED_EXIT_CODE = 76
SYNC_FAILURE_RATE_THRESHOLD = 0.10
KOL_PROVIDER_ERROR_STOP_THRESHOLD = 3
LEGACY_KOL_REFRESH_GUARD_REASON = "legacy_kol_daily_refresh_disabled_until_tier_selector"
QUALIFIED_KOL_REFRESH_GUARD_REASON = "qualified_kol_refresh_requires_explicit_operator_enable"
TRACEBACK_MAX_CHARS = 4096
TRACEBACK_MAX_LINES = 50
INTERRUPT_RECORD_RETRY_DELAYS_SEC = (0.2, 0.5, 1.0, 2.0, 5.0)

# PostgreSQL SQLSTATE reference:
# https://www.postgresql.org/docs/current/errcodes-appendix.html
DB_LOST_SQLSTATES = {
    "57P01",  # admin_shutdown
    "57P02",  # crash_shutdown
    "57P03",  # cannot_connect_now
    "08000",  # connection_exception
    "08001",  # sqlclient_unable_to_establish_sqlconnection
    "08003",  # connection_does_not_exist
    "08004",  # sqlserver_rejected_establishment_of_sqlconnection
    "08006",  # connection_failure
    "08007",  # transaction_resolution_unknown
}
DB_LOST_MESSAGE_TOKENS = (
    "connection is closed",
    "server closed the connection",
    "terminating connection due to administrator command",
    "consuming input failed",
    "ssl connection has been closed",
    "connection has been closed",
)
try:  # requests is present in production, but keep sync importable without it.
    from requests import Timeout as RequestsTimeout
except Exception:  # pragma: no cover - only for minimal local test envs.
    RequestsTimeout = ()  # type: ignore[assignment]

PROVIDER_TIMEOUT_EXCEPTIONS = tuple(
    item
    for item in (
        TimeoutError,
        asyncio.TimeoutError,
        concurrent.futures.TimeoutError,
        RequestsTimeout,
    )
    if isinstance(item, type)
)


class SyncFailFast(RuntimeError):
    """Raised when daily sync must stop immediately and let systemd mark failure."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = SYNC_FAIL_FAST_EXIT_CODE,
        run_id: str = "",
        stage: str = "",
        summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = int(exit_code)
        self.run_id = run_id
        self.stage = stage
        self.summary = dict(summary or {})


class SyncGuardBlocked(RuntimeError):
    """Raised before provider calls when the previous sync requires manual ack."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = SYNC_GUARD_BLOCKED_EXIT_CODE,
        blocking_run_id: str = "",
        summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = int(exit_code)
        self.blocking_run_id = blocking_run_id
        self.summary = dict(summary or {})


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _platform_filter(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item.strip().lower() for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return set()


def _tier_filter(value: Any) -> set[str]:
    if isinstance(value, str):
        tiers = {item.strip().lower() for item in value.split(",") if item.strip()}
    elif isinstance(value, list):
        tiers = {str(item).strip().lower() for item in value if str(item).strip()}
    else:
        tiers = {"hot"}
    tiers = tiers & {"hot", "warm", "cold"}
    return tiers or {"hot"}


def _kol_refresh_selector(payload: dict[str, Any]) -> str:
    raw = str(payload.get("kol_refresh_selector") or payload.get("refresh_selector") or "legacy").strip().lower()
    if raw in {"qualified", "tier", "tiered", "refresh_tier"}:
        return "qualified"
    return "legacy"


def _system_staff() -> dict[str, Any]:
    return {
        "id": 0,
        "staff_id": 0,
        "user_id": 0,
        "role": "admin",
        "is_owner": 1,
        "email": "",
    }


def _status_ok(status: Any) -> bool:
    return str(status or "").strip().lower() in {"ok", "synced", "success"}


def _row_label(row: dict[str, Any]) -> str:
    platform = str(row.get("platform") or "-")
    handle = str(row.get("account_handle") or row.get("handle") or row.get("display_name") or "-")
    return f"{platform}:{handle}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        parsed = json.loads(str(value))
        return parsed if parsed is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _unwrap_db_exception(exc: BaseException) -> BaseException:
    orig = getattr(exc, "orig", None)
    if isinstance(orig, BaseException):
        return orig
    return exc


def _sqlstate(exc: BaseException) -> str:
    try:
        value = getattr(exc, "sqlstate", None)
        if value:
            return str(value)
        diag = getattr(exc, "diag", None)
        diag_value = getattr(diag, "sqlstate", None) if diag is not None else None
        return str(diag_value or "")
    except Exception:
        return ""


def _is_db_connection_lost(exc: BaseException) -> bool:
    raw = _unwrap_db_exception(exc)
    if _sqlstate(raw) in DB_LOST_SQLSTATES:
        return True
    message = str(raw).lower()
    return any(token in message for token in DB_LOST_MESSAGE_TOKENS)


def _classify_sync_error(exc: BaseException) -> tuple[str, str]:
    raw = _unwrap_db_exception(exc)
    sqlstate = _sqlstate(raw)
    message = str(raw).lower()
    if sqlstate in {"57P01", "57P02"} or "terminating connection due to administrator command" in message:
        return "db_connection_lost", "admin_shutdown"
    if _is_db_connection_lost(raw):
        if "ssl connection has been closed" in message:
            return "db_connection_lost", "ssl_connection_closed"
        if "server closed the connection" in message:
            return "db_connection_lost", "server_closed_connection"
        if "consuming input failed" in message:
            return "db_connection_lost", "consuming_input_failed"
        return "db_connection_lost", "connection_closed"
    if isinstance(raw, PROVIDER_TIMEOUT_EXCEPTIONS) or "timeout" in message or "timed out" in message:
        return "provider_timeout", "provider_timeout"
    if isinstance(raw, (KeyError, TypeError, ValueError, AttributeError)):
        return "data_field_missing", "data_field_missing"
    return "other", "other"


def _traceback_text(exc: BaseException) -> str:
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    text = "".join(lines[-TRACEBACK_MAX_LINES:])
    if len(text) > TRACEBACK_MAX_CHARS:
        return text[-TRACEBACK_MAX_CHARS:]
    return text


def _new_run_id(job_name: str = "daily_incremental_sync", stage: str = "kol_pool_light") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{job_name}_{stage}_{stamp}_{uuid.uuid4().hex[:8]}"


def _write_sync_run(sql: str, params: tuple[Any, ...]) -> None:
    conn = None
    try:
        conn = open_standalone_conn()
        conn.execute(sql, params)
        conn.commit()
    finally:
        close_standalone_conn(conn)


def start_sync_run(
    *,
    run_id: str,
    job_name: str,
    stage: str,
    total_targets: int,
    payload: dict[str, Any],
) -> None:
    now = _utcnow()
    _write_sync_run(
        """
        INSERT INTO vkpi_sync_runs
          (run_id, job_name, stage, started_at, status, total_targets, last_success_index, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id) DO UPDATE SET
          status=excluded.status,
          total_targets=excluded.total_targets,
          payload_json=excluded.payload_json,
          updated_at=excluded.updated_at
        """,
        (run_id, job_name, stage, now, "running", int(total_targets or 0), 0, _json(payload), now),
    )


def finish_sync_run(
    *,
    run_id: str,
    status: str,
    last_success_index: int,
    summary: dict[str, Any],
    reason: str | None = None,
    error_type: str | None = None,
) -> None:
    now = _utcnow()
    _write_sync_run(
        """
        UPDATE vkpi_sync_runs
        SET finished_at=?,
            status=?,
            last_success_index=?,
            reason=?,
            error_type=?,
            summary_json=?,
            updated_at=?
        WHERE run_id=?
        """,
        (now, status, int(last_success_index or 0), reason, error_type, _json(summary), now, run_id),
    )


def _sync_health_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Classify sync summary health for alerts and next-run guard decisions."""
    official = summary.get("official") if isinstance(summary.get("official"), dict) else {}
    kol = summary.get("kol_pool_light") if isinstance(summary.get("kol_pool_light"), dict) else summary
    official_requested = _int(official.get("requested"))
    official_failed = _int(official.get("failed"))
    kol_requested = _int(kol.get("requested"))
    kol_errors = _int(kol.get("errors"))
    total_requested = official_requested + kol_requested
    total_errors = official_failed + kol_errors
    failure_rate = (float(total_errors) / float(total_requested)) if total_requested > 0 else 0.0
    threshold_exceeded = total_requested > 0 and failure_rate > SYNC_FAILURE_RATE_THRESHOLD
    return {
        "official_requested": official_requested,
        "official_failed": official_failed,
        "kol_requested": kol_requested,
        "kol_errors": kol_errors,
        "total_requested": total_requested,
        "total_errors": total_errors,
        "failure_rate": round(failure_rate, 6),
        "failure_rate_threshold": SYNC_FAILURE_RATE_THRESHOLD,
        "has_errors": total_errors > 0,
        "blocked_next_run": bool(threshold_exceeded),
        "block_reason": "failure_rate_threshold_exceeded" if threshold_exceeded else "",
    }


def _row_after_ack(row: dict[str, Any], ack: dict[str, Any] | None) -> bool:
    if not ack:
        return True
    target_run_id = str(ack.get("target_run_id") or "")
    if target_run_id and target_run_id == str(row.get("run_id") or ""):
        return False
    ack_at = str(ack.get("acknowledged_at") or "")
    started_at = str(row.get("started_at") or "")
    return bool(started_at and ack_at and started_at > ack_at)


def _latest_sync_ack(scope: str = "daily_incremental_sync") -> dict[str, Any] | None:
    try:
        row = get_conn().execute(
            """
            SELECT *
            FROM vkpi_sync_acknowledgements
            WHERE scope=?
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY acknowledged_at DESC
            LIMIT 1
            """,
            (scope, _utcnow()),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        logger.debug("daily sync guard ack lookup unavailable", exc_info=True)
        return None


def _blocking_sync_run(scope: str = "daily_incremental_sync") -> dict[str, Any] | None:
    ack = _latest_sync_ack(scope)
    try:
        rows = get_conn().execute(
            """
            SELECT run_id, job_name, stage, started_at, finished_at, status, reason,
                   error_type, error_class, error_message, summary_json
            FROM vkpi_sync_runs
            WHERE job_name=?
            ORDER BY started_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 30
            """,
            (scope,),
        ).fetchall()
    except Exception:
        logger.debug("daily sync guard run lookup unavailable", exc_info=True)
        return None
    for raw_row in rows:
        row = dict(raw_row)
        if not _row_after_ack(row, ack):
            continue
        status = str(row.get("status") or "").strip().lower()
        error_type = str(row.get("error_type") or "").strip().lower()
        summary = _load_json(row.get("summary_json"), {})
        health = _sync_health_from_summary(summary if isinstance(summary, dict) else {})
        blocked = status in {"interrupted", "failed"} or error_type == "db_connection_lost" or bool(health.get("blocked_next_run"))
        if blocked:
            return {
                "run_id": row.get("run_id"),
                "stage": row.get("stage"),
                "status": status,
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "reason": row.get("reason") or health.get("block_reason") or "sync_guard_blocked",
                "error_type": error_type,
                "error_class": row.get("error_class"),
                "error_message": row.get("error_message"),
                "health": health,
                "ack_required": True,
                "ack_scope": scope,
            }
    return None


def check_daily_sync_guard(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    if _bool(payload.get("dry_run")) or _bool(payload.get("skip_sync_guard")):
        return {"allowed": True, "skipped": True}
    blocking = _blocking_sync_run("daily_incremental_sync")
    if not blocking:
        return {"allowed": True}
    blocking = {**blocking, "ack_required": True, "ack_scope": "daily_incremental_sync"}
    raise SyncGuardBlocked(
        f"daily sync blocked; manual ack required for {blocking.get('run_id')}",
        blocking_run_id=str(blocking.get("run_id") or ""),
        summary=blocking,
    )


def ack_daily_sync_guard(
    *,
    reason: str,
    acknowledged_by: str = "cli",
    scope: str = "daily_incremental_sync",
    target_run_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("ack reason is required")
    now = _utcnow()
    ack_id = f"sync_ack_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    get_conn().execute(
        """
        INSERT INTO vkpi_sync_acknowledgements
          (ack_id, scope, target_run_id, reason, acknowledged_by, acknowledged_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ack_id, scope, target_run_id or None, clean_reason, str(acknowledged_by or "cli"), now, _json(metadata or {})),
    )
    get_conn().commit()
    return {
        "ack_id": ack_id,
        "scope": scope,
        "target_run_id": target_run_id,
        "reason": clean_reason,
        "acknowledged_by": acknowledged_by or "cli",
        "acknowledged_at": now,
    }


def _upsert_sync_health_alert(*, run_id: str, health: dict[str, Any], summary: dict[str, Any]) -> None:
    if not health.get("has_errors") and not health.get("blocked_next_run"):
        return
    try:
        from app.domains import alerts

        severity = "danger" if health.get("blocked_next_run") else "warning"
        title = "Daily sync requires acknowledgement" if health.get("blocked_next_run") else "Daily sync completed with provider errors"
        body = (
            f"official_failed={health.get('official_failed')} "
            f"kol_errors={health.get('kol_errors')} "
            f"failure_rate={health.get('failure_rate')}"
        )
        alerts.upsert_alert(
            alert_key=f"sync.daily.{run_id}",
            title=title,
            body=body,
            severity=severity,
            target_type="vkpi_sync_run",
            target_id=None,
            rule_key="sync.daily_failure_rate" if health.get("blocked_next_run") else "sync.daily_errors",
            metadata_json=_json({"run_id": run_id, "health": health, "summary": summary}),
        )
    except Exception:
        logger.warning("daily sync health alert write failed", exc_info=True)


def record_daily_sync_summary(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    summary = dict(result or {})
    health = _sync_health_from_summary(summary)
    summary["health"] = health
    now = _utcnow()
    status = "failed" if health.get("blocked_next_run") else "completed"
    reason = str(health.get("block_reason") or ("sync_errors_present" if health.get("has_errors") else ""))
    _write_sync_run(
        """
        INSERT INTO vkpi_sync_runs
          (run_id, job_name, stage, started_at, finished_at, status, total_targets, last_success_index,
           reason, error_type, summary_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id) DO UPDATE SET
          finished_at=excluded.finished_at,
          status=excluded.status,
          total_targets=excluded.total_targets,
          last_success_index=excluded.last_success_index,
          reason=excluded.reason,
          error_type=excluded.error_type,
          summary_json=excluded.summary_json,
          updated_at=excluded.updated_at
        """,
        (
            f"{run_id}_summary",
            "daily_incremental_sync",
            "daily_summary",
            str(result.get("started_at") or now),
            now,
            status,
            int(health.get("total_requested") or 0),
            int(health.get("total_requested") or 0),
            reason or None,
            "other" if health.get("has_errors") else None,
            _json(summary),
            now,
        ),
    )
    _upsert_sync_health_alert(run_id=run_id, health=health, summary=summary)
    return health


def _emit_interrupt_stderr(event: dict[str, Any]) -> None:
    sys.stderr.write(_json({"event": "vkpi_sync_interrupt_record_failed", "at": _utcnow(), **event}) + "\n")
    sys.stderr.flush()


def record_sync_interrupt(
    *,
    run_id: str,
    job_name: str,
    stage: str,
    total_targets: int,
    last_success_index: int,
    interrupted_at_index: int,
    interrupted_kol_pool_id: int,
    reason: str,
    error_type: str,
    error_class: str,
    error_message: str,
    traceback_text: str,
    payload: dict[str, Any],
    summary: dict[str, Any],
) -> bool:
    now = _utcnow()
    sql = """
        INSERT INTO vkpi_sync_runs
          (run_id, job_name, stage, started_at, finished_at, status, total_targets, last_success_index,
           interrupted_at_index, interrupted_kol_pool_id, reason, error_type, error_class, error_message,
           traceback_text, payload_json, summary_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id) DO UPDATE SET
          finished_at=excluded.finished_at,
          status=excluded.status,
          total_targets=excluded.total_targets,
          last_success_index=excluded.last_success_index,
          interrupted_at_index=excluded.interrupted_at_index,
          interrupted_kol_pool_id=excluded.interrupted_kol_pool_id,
          reason=excluded.reason,
          error_type=excluded.error_type,
          error_class=excluded.error_class,
          error_message=excluded.error_message,
          traceback_text=excluded.traceback_text,
          payload_json=excluded.payload_json,
          summary_json=excluded.summary_json,
          updated_at=excluded.updated_at
    """
    params = (
        run_id,
        job_name,
        stage,
        now,
        now,
        "interrupted",
        int(total_targets or 0),
        int(last_success_index or 0),
        int(interrupted_at_index or 0),
        int(interrupted_kol_pool_id or 0),
        reason,
        error_type,
        error_class,
        error_message[:1000],
        traceback_text,
        _json(payload),
        _json(summary),
        now,
    )
    started = time.monotonic()
    last_exc: BaseException | None = None
    for attempt, delay in enumerate((0.0, *INTERRUPT_RECORD_RETRY_DELAYS_SEC), start=1):
        if delay:
            time.sleep(delay)
        try:
            _write_sync_run(sql, params)
            return True
        except Exception as exc:
            last_exc = exc
            if time.monotonic() - started >= 10.0:
                break
            logger.warning("daily sync interrupt record attempt %s failed: %s", attempt, exc)
    _emit_interrupt_stderr({
        "run_id": run_id,
        "job_name": job_name,
        "stage": stage,
        "interrupted_at_index": interrupted_at_index,
        "interrupted_kol_pool_id": interrupted_kol_pool_id,
        "reason": reason,
        "error_type": error_type,
        "error_class": error_class,
        "record_error": f"{type(last_exc).__name__}: {str(last_exc)[:500]}" if last_exc else "unknown",
    })
    return False
