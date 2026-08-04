"""Fail-closed budget boundary for every paid Apify network start.

This module is intentionally below the domain crawlers.  Every paid Actor
start must pass through :func:`call_apify_actor`, which persists the provider
run id before waiting.  Opaque direct ``run-sync`` HTTP starts are disabled
because they cannot close the crash window between provider start and run-id
persistence.

Control-plane reads such as provider health and settled-cost reconciliation do
not start an Actor and therefore are not routed through this boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, TypeVar

from app.core.logging import get_logger
from app.platform.apify_budget_contracts import (
    APIFY_BUDGET_BLOCK_CODE,
    APIFY_BUDGET_SCOPE,
    ApifyBudgetBlocked,
    ApifyBudgetDecision,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    _canonical_hash,
    _execution_context,
    _iso,
    _parse_time,
    _positive_float,
    _utcnow,
    apify_execution_context,
    current_apify_execution_context,
)


logger = get_logger(__name__)

_ResultT = TypeVar("_ResultT")
_OPEN_RESERVATION_STATES = ("reserved", "provider_started", "unknown")
_ESTIMATE_HEADROOM = 1.25
_TERMINAL_APIFY_RUN_STATES = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
_PROVIDER_WAIT_SLICE_SECONDS = 30


def _reject_release_validation_provider_work() -> None:
    from app.core.release_validation import release_validation_active
    if release_validation_active():
        raise ApifyExecutionClaimBlocked("release validation fence blocks provider work")


def _ensure_reservation_schema() -> None:
    """Read-only schema proof; release migration 254 is the only DDL owner."""

    from app.db.connection import get_conn, is_postgres_runtime, table_exists

    conn = get_conn()
    required = {
        "vkpi_provider_execution_claims": (
            "task_id", "job_type", "lease_owner", "fence_token", "state",
            "lease_expires_at", "provider_run_id", "created_at", "updated_at", "completed_at",
        ),
        "vkpi_apify_budget_reservations": (
            "reservation_key", "task_id", "actor_id", "operation", "payload_hash",
            "execution_fence_token", "estimate_source", "estimated_cost_usd",
            "actual_cost_usd", "state", "apify_run_id", "metadata_json", "reserved_at",
            "provider_started_at", "settled_at", "updated_at",
        ),
    }
    for table, columns in required.items():
        if not table_exists(table):
            raise RuntimeError(f"migration 254 schema missing: {table}")
        conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE 1=0")
    if is_postgres_runtime():
        constraints = conn.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conname IN (
              'vkpi_provider_execution_claims_pkey',
              'vkpi_apify_budget_reservations_pkey',
              'uq_vkpi_apify_reservation_request',
              'fk_vkpi_apify_reservation_task'
            )
            """
        ).fetchall()
        names = {str(dict(row).get("conname") or "") for row in constraints}
        expected = {
            "vkpi_provider_execution_claims_pkey",
            "vkpi_apify_budget_reservations_pkey",
            "uq_vkpi_apify_reservation_request",
            "fk_vkpi_apify_reservation_task",
        }
        index = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND indexname='uq_vkpi_apify_reservation_run'"
        ).fetchone()
        if names != expected or not index:
            raise RuntimeError("migration 254 reservation uniqueness constraints are missing")


def acquire_provider_execution_claim(
    task_id: str,
    lease_owner: str,
    *,
    job_type: str = "",
    lease_seconds: int = 900,
) -> int:
    """Acquire/renew one durable task fence without touching a provider."""

    _reject_release_validation_provider_work()
    from app.db.connection import get_conn, is_postgres_runtime

    clean_task = str(task_id or "").strip()
    clean_owner = str(lease_owner or "").strip()
    if not clean_task or not clean_owner:
        raise ValueError("task_id and lease_owner are required")
    _ensure_reservation_schema()
    conn = get_conn()
    now = _utcnow()
    expires = now + timedelta(seconds=max(30, min(3600, int(lease_seconds or 900))))
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    try:
        inserted = conn.execute(
            """
            INSERT INTO vkpi_provider_execution_claims
              (task_id,job_type,lease_owner,fence_token,state,lease_expires_at,created_at,updated_at)
            VALUES (?,?,?,1,'active',?,?,?)
            ON CONFLICT(task_id) DO NOTHING
            """,
            (clean_task, str(job_type or ""), clean_owner, _iso(expires), _iso(now), _iso(now)),
        )
        if int(getattr(inserted, "rowcount", 0) or 0) == 1:
            conn.commit()
            return 1
        row = conn.execute(
            "SELECT * FROM vkpi_provider_execution_claims WHERE task_id=?" + lock,
            (clean_task,),
        ).fetchone()
        if row:
            data = dict(row)
            current_expiry = _parse_time(data.get("lease_expires_at"))
            state = str(data.get("state") or "")
            current_owner = str(data.get("lease_owner") or "")
            if state == "active" and current_expiry and current_expiry > now and current_owner != clean_owner:
                raise ApifyExecutionClaimBlocked("task already has a live provider execution lease")
            fence = int(data.get("fence_token") or 0) + 1
            conn.execute(
                """
                UPDATE vkpi_provider_execution_claims
                SET job_type=?, lease_owner=?, fence_token=?, state='active',
                    lease_expires_at=?, provider_run_id=NULL, updated_at=?, completed_at=NULL
                WHERE task_id=?
                """,
                (str(job_type or ""), clean_owner, fence, _iso(expires), _iso(now), clean_task),
            )
        else:
            raise RuntimeError("provider execution claim disappeared after conflict")
        conn.commit()
        return fence
    except Exception:
        conn.rollback()
        raise


def finalize_provider_execution_claim(task_id: str, fence_token: int, state: str) -> bool:
    from app.db.connection import get_conn

    final_state = str(state or "").strip().lower()
    if final_state not in {"completed", "failed", "blocked", "unknown"}:
        raise ValueError("invalid provider claim terminal state")
    _ensure_reservation_schema()
    conn = get_conn()
    now = _iso(_utcnow())
    cursor = conn.execute(
        """
        UPDATE vkpi_provider_execution_claims
        SET state=?, updated_at=?, completed_at=?
        WHERE task_id=? AND fence_token=? AND state='active'
        """,
        (final_state, now, now, str(task_id or ""), int(fence_token or 0)),
    )
    conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def renew_provider_execution_claim(
    task_id: str,
    fence_token: int,
    lease_owner: str,
    *,
    lease_seconds: int = 900,
) -> bool:
    """Extend one still-live fence; an expired/stolen claim can never resurrect."""

    from app.db.connection import get_conn

    clean_task = str(task_id or "").strip()
    clean_owner = str(lease_owner or "").strip()
    if not clean_task or not clean_owner or int(fence_token or 0) <= 0:
        raise ValueError("task_id, lease_owner and positive fence_token are required")
    _ensure_reservation_schema()
    now = _utcnow()
    expires = now + timedelta(seconds=max(30, min(3600, int(lease_seconds or 900))))
    conn = get_conn()
    cursor = conn.execute(
        """
        UPDATE vkpi_provider_execution_claims
        SET lease_expires_at=?, updated_at=?
        WHERE task_id=? AND fence_token=? AND lease_owner=? AND state='active'
          AND lease_expires_at>?
        """,
        (
            _iso(expires),
            _iso(now),
            clean_task,
            int(fence_token),
            clean_owner,
            _iso(now),
        ),
    )
    conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def _input_units(value: Any) -> int:
    """Conservative unit count from common Actor input limits and URL lists."""

    limit_keys = {
        "maxitems", "max_posts", "maxposts", "maxcomments", "max_comments",
        "resultslimit", "results_limit", "maxresults", "limit", "maxvideos",
        "max_videos", "maxprofiles", "max_profiles",
    }
    list_keys = {"starturls", "urls", "directurls", "queries", "profiles", "handles"}
    found: list[int] = []

    def walk(item: Any, key: str = "") -> None:
        normalized = key.replace("-", "").lower()
        if normalized in limit_keys:
            parsed = _positive_float(item)
            if parsed is not None:
                found.append(max(1, int(math.ceil(parsed))))
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                walk(child, str(child_key))
        elif isinstance(item, (list, tuple, set)):
            if normalized in list_keys:
                found.append(max(1, len(item)))
            for child in item:
                walk(child)

    walk(value)
    return max(found or [1])


def estimate_apify_call_cost(
    *,
    actor_id: str,
    operation: str,
    run_input: Any,
    explicit: float | int | str | None = None,
) -> tuple[float, str]:
    """Return max(priced input, actor/operation P95, configured floor).

    There is intentionally no generic $0.001 fallback: an unknown Actor with
    no observed history or configured floor is blocked before provider start.
    """

    from app.db.connection import get_conn
    from app.domains.costs.budget_guard import _apify_actor_pricing

    actor = str(actor_id or "").strip().replace("~", "/").lower()
    candidates: list[tuple[float, str]] = []
    explicit_value = _positive_float(explicit)
    if explicit_value is not None:
        candidates.append((explicit_value, "caller_floor"))
    configured_unknown = _positive_float(os.getenv("VKPI_APIFY_UNKNOWN_CALL_ESTIMATE_USD", ""))
    if configured_unknown is not None:
        candidates.append((configured_unknown, "configured_unknown_floor"))
    pricing = _apify_actor_pricing(actor)
    if pricing:
        units = _input_units(run_input)
        priced = max(0.0, float(pricing.get("per_run") or 0.0))
        priced += max(0.0, float(pricing.get("per_1000_items") or 0.0)) * units / 1000.0
        if priced > 0:
            candidates.append((priced * _ESTIMATE_HEADROOM, "actor_pricing_input_headroom"))
    try:
        _ensure_reservation_schema()
        cutoff = _iso(_utcnow() - timedelta(days=30))
        rows = get_conn().execute(
            """
            SELECT actual_cost_usd FROM vkpi_apify_budget_reservations
            WHERE actor_id=? AND operation=? AND state='settled'
              AND actual_cost_usd IS NOT NULL AND settled_at>=?
            ORDER BY actual_cost_usd ASC LIMIT 200
            """,
            (actor, str(operation or ""), cutoff),
        ).fetchall()
        history = sorted(
            float(dict(row).get("actual_cost_usd") or 0.0)
            for row in rows
            if float(dict(row).get("actual_cost_usd") or 0.0) > 0
        )
        if history:
            index = max(0, min(len(history) - 1, math.ceil(len(history) * 0.95) - 1))
            candidates.append((history[index] * _ESTIMATE_HEADROOM, "actor_operation_p95_headroom"))
    except Exception:
        logger.warning("apify reservation history unavailable", exc_info=True)
    if not candidates:
        raise ValueError("estimate_unavailable")
    cost, source = max(candidates, key=lambda item: item[0])
    return round(max(cost, 0.000001), 6), source


def apify_budget_decision(
    *,
    operation: str = "",
    actor_id: str = "",
    platform: str = "",
    source: str = "",
    estimated_cost_usd: float | int | str | None = None,
    run_input: Any = None,
) -> ApifyBudgetDecision:
    """Read-only diagnostic decision; provider starts use atomic reservation."""

    try:
        estimate, estimate_source = estimate_apify_call_cost(
            actor_id=actor_id,
            operation=operation,
            run_input=run_input,
            explicit=estimated_cost_usd,
        )
    except Exception:
        estimate, estimate_source = 0.0, "estimate_unavailable"
    reason = "estimate_unavailable" if estimate <= 0 else "allowed"
    allowed = False
    try:
        from app.domains.costs.budget_guard import check_budget

        allowed = estimate > 0 and bool(
            check_budget(
                APIFY_BUDGET_SCOPE,
                estimate,
                require_configured=True,
            )
        )
        if not allowed:
            reason = "hard_stop_or_projected_cap"
    except Exception as exc:
        # A missing table, unavailable database, or broken guard is not a
        # license to spend.  Provider starts resume only after the guard can
        # prove an explicit allowance.
        reason = f"budget_guard_unavailable:{type(exc).__name__}"
        logger.error(
            "apify budget preflight failed closed | operation=%s actor=%s source=%s error=%s",
            operation,
            actor_id,
            source,
            type(exc).__name__,
        )
    normalized_actor = str(actor_id or "").replace("~", "/")
    fingerprint_body = {
        "scope": APIFY_BUDGET_SCOPE,
        "source": str(source or ""),
        "platform": str(platform or ""),
        "actor_id": normalized_actor,
        "operation": str(operation or ""),
    }
    request_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ApifyBudgetDecision(
        allowed=allowed,
        scope=APIFY_BUDGET_SCOPE,
        estimated_cost_usd=estimate,
        reason=reason,
        operation=str(operation or ""),
        actor_id=normalized_actor,
        platform=str(platform or ""),
        source=str(source or ""),
        request_fingerprint=request_fingerprint,
        estimate_source=estimate_source,
    )


def _decision(
    *,
    allowed: bool,
    estimate: float,
    estimate_source: str,
    reason: str,
    operation: str,
    actor_id: str,
    platform: str,
    source: str,
    reservation_key: str,
    payload_hash: str,
    reservation_state: str = "",
    apify_run_id: str = "",
) -> ApifyBudgetDecision:
    normalized_actor = str(actor_id or "").replace("~", "/").strip().lower()
    fingerprint = _canonical_hash(
        {
            "scope": APIFY_BUDGET_SCOPE,
            "source": str(source or ""),
            "platform": str(platform or ""),
            "actor_id": normalized_actor,
            "operation": str(operation or ""),
            "payload_hash": payload_hash,
        }
    )
    return ApifyBudgetDecision(
        allowed=allowed,
        scope=APIFY_BUDGET_SCOPE,
        estimated_cost_usd=float(estimate or 0.0),
        reason=reason,
        operation=str(operation or ""),
        actor_id=normalized_actor,
        platform=str(platform or ""),
        source=str(source or ""),
        request_fingerprint=fingerprint,
        reservation_key=reservation_key,
        payload_hash=payload_hash,
        estimate_source=estimate_source,
        reservation_state=reservation_state,
        apify_run_id=apify_run_id,
    )


def _validate_execution_fence(conn: Any, task_id: str, fence_token: int) -> None:
    from app.db.connection import is_postgres_runtime

    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    row = conn.execute(
        "SELECT state,fence_token,lease_expires_at FROM vkpi_provider_execution_claims WHERE task_id=?" + lock,
        (task_id,),
    ).fetchone()
    data = dict(row) if row else {}
    expiry = _parse_time(data.get("lease_expires_at"))
    if (
        not row
        or str(data.get("state") or "") != "active"
        or int(data.get("fence_token") or 0) != int(fence_token or 0)
        or expiry is None
        or expiry <= _utcnow()
    ):
        raise ApifyExecutionClaimBlocked("provider execution fence is stale")


def _reserve_apify_budget(
    *,
    operation: str,
    actor_id: str,
    platform: str,
    source: str,
    run_input: Any,
    estimated_cost_usd: float | int | str | None,
) -> ApifyBudgetDecision:
    """Serialize cap check + reservation under the provider budget row lock."""

    from app.db.connection import get_conn, is_postgres_runtime

    actor = str(actor_id or "").strip().replace("~", "/").lower()
    payload_hash = _canonical_hash(run_input)
    context = _execution_context.get()
    if context is None:
        return _decision(
            allowed=False,
            estimate=0.0,
            estimate_source="durable_execution_context_required",
            reason="durable_execution_context_required",
            operation=operation,
            actor_id=actor,
            platform=platform,
            source=source,
            reservation_key="",
            payload_hash=payload_hash,
        )
    task_id = context[0]
    fence_token = context[1]
    reservation_key = _canonical_hash(
        {"task_id": task_id, "actor_id": actor, "operation": operation, "payload_hash": payload_hash}
    )
    try:
        estimate, estimate_source = estimate_apify_call_cost(
            actor_id=actor,
            operation=operation,
            run_input=run_input,
            explicit=estimated_cost_usd,
        )
    except Exception:
        return _decision(
            allowed=False,
            estimate=0.0,
            estimate_source="estimate_unavailable",
            reason="estimate_unavailable",
            operation=operation,
            actor_id=actor,
            platform=platform,
            source=source,
            reservation_key=reservation_key,
            payload_hash=payload_hash,
        )

    try:
        _ensure_reservation_schema()
    except Exception as exc:
        logger.error("apify reservation schema unavailable: %s", type(exc).__name__)
        return _decision(
            allowed=False, estimate=estimate, estimate_source=estimate_source,
            reason=f"reservation_schema_unavailable:{type(exc).__name__}", operation=operation,
            actor_id=actor, platform=platform, source=source,
            reservation_key=reservation_key, payload_hash=payload_hash,
        )
    conn = get_conn()
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    try:
        _validate_execution_fence(conn, task_id, int(fence_token or 0))

        # This lock serializes every reservation against current spend and all
        # still-open reservations.  It also serializes the request uniqueness
        # check, so two consumers cannot both insert/start the same paid work.
        provider_row = conn.execute(
            "SELECT * FROM vkpi_provider_budget_caps WHERE scope=?" + lock,
            (APIFY_BUDGET_SCOPE,),
        ).fetchone()
        if not provider_row:
            conn.rollback()
            return _decision(
                allowed=False, estimate=estimate, estimate_source=estimate_source,
                reason="budget_scope_not_configured", operation=operation, actor_id=actor,
                platform=platform, source=source, reservation_key=reservation_key,
                payload_hash=payload_hash,
            )

        existing = conn.execute(
            """
            SELECT * FROM vkpi_apify_budget_reservations
            WHERE task_id=? AND actor_id=? AND operation=? AND payload_hash=?
            """ + lock,
            (task_id, actor, str(operation or ""), payload_hash),
        ).fetchone()
        if existing:
            data = dict(existing)
            state = str(data.get("state") or "")
            run_id = str(data.get("apify_run_id") or "")
            key = str(data.get("reservation_key") or reservation_key)
            if state == "reserved":
                conn.execute(
                    """
                    UPDATE vkpi_apify_budget_reservations
                    SET execution_fence_token=?, updated_at=? WHERE reservation_key=?
                    """,
                    (int(fence_token), _iso(_utcnow()), key),
                )
                conn.commit()
                return _decision(
                    allowed=True, estimate=float(data.get("estimated_cost_usd") or estimate),
                    estimate_source=str(data.get("estimate_source") or estimate_source), reason="reserved",
                    operation=operation, actor_id=actor, platform=platform, source=source,
                    reservation_key=key, payload_hash=payload_hash, reservation_state=state,
                )
            if state == "provider_started" and run_id:
                conn.execute(
                    "UPDATE vkpi_apify_budget_reservations SET execution_fence_token=?,updated_at=? WHERE reservation_key=?",
                    (int(fence_token), _iso(_utcnow()), key),
                )
                conn.commit()
                return _decision(
                    allowed=True, estimate=float(data.get("estimated_cost_usd") or estimate),
                    estimate_source=str(data.get("estimate_source") or estimate_source), reason="resume_same_run",
                    operation=operation, actor_id=actor, platform=platform, source=source,
                    reservation_key=key, payload_hash=payload_hash, reservation_state=state,
                    apify_run_id=run_id,
                )
            conn.rollback()
            raise ApifyProviderReplayBlocked(
                "provider_start_unknown" if state in {"provider_started", "unknown"} else f"reservation_{state}",
                reservation_key=key,
                run_id=run_id,
            )

        open_row = conn.execute(
            """
            SELECT COALESCE(SUM(estimated_cost_usd),0) AS reserved
            FROM vkpi_apify_budget_reservations
            WHERE state IN ('reserved','provider_started','unknown')
            """
        ).fetchone()
        open_reserved = float((dict(open_row).get("reserved") if open_row else 0.0) or 0.0)
        scopes = [dict(provider_row)]
        monthly = conn.execute(
            "SELECT * FROM vkpi_provider_budget_caps WHERE scope='monthly_total'" + lock
        ).fetchone()
        if monthly:
            scopes.append(dict(monthly))
        denied_scope = ""
        for scope_row in scopes:
            cap = float(scope_row.get("cap_usd") or 0.0)
            current = float(scope_row.get("current_spend") or 0.0)
            hard_stop = max(0.0, min(1.0, float(scope_row.get("hard_stop_at") or 1.0)))
            if cap > 0 and current + open_reserved + estimate >= cap * hard_stop:
                denied_scope = str(scope_row.get("scope") or APIFY_BUDGET_SCOPE)
                break
        if denied_scope:
            conn.rollback()
            return _decision(
                allowed=False, estimate=estimate, estimate_source=estimate_source,
                reason=f"hard_stop_or_projected_cap:{denied_scope}", operation=operation,
                actor_id=actor, platform=platform, source=source,
                reservation_key=reservation_key, payload_hash=payload_hash,
            )
        now = _iso(_utcnow())
        conn.execute(
            """
            INSERT INTO vkpi_apify_budget_reservations
              (reservation_key,task_id,actor_id,operation,payload_hash,execution_fence_token,
               estimate_source,estimated_cost_usd,state,metadata_json,reserved_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,'reserved','{}',?,?)
            """,
            (
                reservation_key, task_id, actor, str(operation or ""), payload_hash,
                int(fence_token), estimate_source, estimate, now, now,
            ),
        )
        conn.commit()
        return _decision(
            allowed=True, estimate=estimate, estimate_source=estimate_source, reason="reserved",
            operation=operation, actor_id=actor, platform=platform, source=source,
            reservation_key=reservation_key, payload_hash=payload_hash, reservation_state="reserved",
        )
    except Exception:
        conn.rollback()
        raise


def _mark_provider_started(decision: ApifyBudgetDecision) -> None:
    from app.db.connection import get_conn

    conn = get_conn()
    context = _execution_context.get()
    fence_token = context[1] if context else None
    if context:
        _validate_execution_fence(conn, context[0], int(fence_token or 0))
    now = _iso(_utcnow())
    cursor = conn.execute(
        """
        UPDATE vkpi_apify_budget_reservations
        SET state='provider_started',provider_started_at=?,updated_at=?
        WHERE reservation_key=? AND state='reserved'
          AND ((execution_fence_token IS NULL AND ? IS NULL) OR execution_fence_token=?)
        """,
        (now, now, decision.reservation_key, fence_token, fence_token),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        conn.rollback()
        raise ApifyProviderReplayBlocked(
            "reservation_not_startable", reservation_key=decision.reservation_key
        )
    conn.commit()


def _mark_provider_outcome(
    decision: ApifyBudgetDecision,
    *,
    run_id: str = "",
    unknown: bool = False,
) -> None:
    from app.db.connection import get_conn

    conn = get_conn()
    context = _execution_context.get()
    fence_token = context[1] if context else None
    if context:
        _validate_execution_fence(conn, context[0], int(fence_token or 0))
    state = "unknown" if unknown else "provider_started"
    conn.execute(
        """
        UPDATE vkpi_apify_budget_reservations
        SET state=?,apify_run_id=COALESCE(NULLIF(?,''),apify_run_id),updated_at=?
        WHERE reservation_key=? AND state IN ('provider_started','unknown')
          AND ((execution_fence_token IS NULL AND ? IS NULL) OR execution_fence_token=?)
        """,
        (
            state,
            str(run_id or ""),
            _iso(_utcnow()),
            decision.reservation_key,
            fence_token,
            fence_token,
        ),
    )
    if context and run_id:
        conn.execute(
            """
            UPDATE vkpi_provider_execution_claims
            SET provider_run_id=?,updated_at=?
            WHERE task_id=? AND fence_token=? AND state='active'
            """,
            (str(run_id), _iso(_utcnow()), context[0], int(fence_token or 0)),
        )
    conn.commit()


def settle_apify_reservation(reservation_key: str, actual_cost_usd: float) -> dict[str, Any]:
    """Settle one reservation and increment cumulative scopes exactly once."""

    from app.db.connection import get_conn, is_postgres_runtime

    key = str(reservation_key or "").strip()
    actual = max(0.0, float(actual_cost_usd or 0.0))
    if not key:
        return {"settled": False, "reason": "no_reservation"}
    _ensure_reservation_schema()
    conn = get_conn()
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    try:
        row = conn.execute(
            "SELECT * FROM vkpi_apify_budget_reservations WHERE reservation_key=?" + lock,
            (key,),
        ).fetchone()
        if not row:
            conn.rollback()
            return {"settled": False, "reason": "missing_reservation"}
        data = dict(row)
        if str(data.get("state") or "") == "settled":
            conn.rollback()
            return {"settled": False, "reason": "already_settled", "actual_cost_usd": float(data.get("actual_cost_usd") or 0.0)}
        if str(data.get("state") or "") != "provider_started" or not str(data.get("apify_run_id") or ""):
            conn.rollback()
            return {"settled": False, "reason": "provider_outcome_not_confirmed"}
        for scope in (APIFY_BUDGET_SCOPE, "monthly_total"):
            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET current_spend=COALESCE(current_spend,0)+? WHERE scope=?",
                (actual, scope),
            )
        now = _iso(_utcnow())
        conn.execute(
            """
            UPDATE vkpi_apify_budget_reservations
            SET state='settled',actual_cost_usd=?,settled_at=?,updated_at=?
            WHERE reservation_key=?
            """,
            (actual, now, now, key),
        )
        conn.commit()
        return {"settled": True, "actual_cost_usd": actual}
    except Exception:
        conn.rollback()
        raise


def _record_apify_denial(decision: ApifyBudgetDecision) -> None:
    """Persist a zero-cost denial without request content or credentials."""

    try:
        from app.domains.costs.budget_guard import record_cost

        record_cost(
            scope=decision.scope,
            ai_provider="apify",
            model_name=decision.actor_id,
            cost_usd=0.0,
            metadata={
                "event": "provider_budget_denied",
                "code": APIFY_BUDGET_BLOCK_CODE,
                "reason": decision.reason,
                "source": decision.source,
                "platform": decision.platform,
                "actor_id": decision.actor_id,
                "operation": decision.operation,
                "estimated_cost_usd": decision.estimated_cost_usd,
                "request_fingerprint": decision.request_fingerprint,
                "request_content_recorded": False,
            },
        )
    except Exception as exc:
        # The provider remains blocked even if the audit sink is unavailable.
        logger.error(
            "apify budget denial ledger unavailable | fingerprint=%s error=%s",
            decision.request_fingerprint[:12],
            type(exc).__name__,
        )


def require_apify_budget(
    *,
    operation: str = "",
    actor_id: str = "",
    platform: str = "",
    source: str = "",
    estimated_cost_usd: float | int | str | None = None,
    run_input: Any = None,
) -> ApifyBudgetDecision:
    """Atomically reserve allowance or raise before provider network I/O."""

    decision = _reserve_apify_budget(
        operation=operation,
        actor_id=actor_id,
        platform=platform,
        source=source,
        estimated_cost_usd=estimated_cost_usd,
        run_input=run_input,
    )
    if decision.allowed:
        return decision
    _record_apify_denial(decision)
    logger.warning(
        "apify provider start blocked | scope=%s reason=%s operation=%s actor=%s source=%s",
        decision.scope,
        decision.reason,
        decision.operation,
        decision.actor_id,
        decision.source,
    )
    raise ApifyBudgetBlocked(decision)


def run_apify_network(
    operation_fn: Callable[[], _ResultT],
    *,
    operation: str = "",
    actor_id: str = "",
    platform: str = "",
    source: str = "",
    estimated_cost_usd: float | int | str | None = None,
    run_input: Any = None,
) -> _ResultT:
    """Reject opaque paid closures that cannot persist a run id pre-wait.

    Kept as a compatibility symbol so an unconverted adapter fails closed
    before invoking ``operation_fn``.  Production code must use
    :func:`call_apify_actor` and the Actor start/run control-plane endpoints.
    """

    del operation_fn, operation, actor_id, platform, source, estimated_cost_usd, run_input
    raise ApifyProviderReplayBlocked(
        "direct_provider_start_without_early_run_id_is_forbidden"
    )


def _provider_run_id(result: Any) -> str:
    if not isinstance(result, Mapping):
        return ""
    direct = str(result.get("id") or "").strip()
    if direct:
        return direct
    data = result.get("data")
    return str(data.get("id") or "").strip() if isinstance(data, Mapping) else ""


def _attach_reservation(result: _ResultT, decision: ApifyBudgetDecision) -> _ResultT:
    if not isinstance(result, dict):
        return result
    enriched = dict(result)
    enriched["_vkpi_budget_reservation_key"] = decision.reservation_key
    enriched["_vkpi_budget_estimated_cost_usd"] = decision.estimated_cost_usd
    enriched["_vkpi_budget_estimate_source"] = decision.estimate_source
    return enriched  # type: ignore[return-value]


def _renew_current_execution_claim(*, lease_seconds: int = 900) -> None:
    context = _execution_context.get()
    if context is None:
        raise ApifyExecutionClaimBlocked("durable provider execution context is missing")
    from app.db.connection import get_conn

    row = get_conn().execute(
        """
        SELECT lease_owner FROM vkpi_provider_execution_claims
        WHERE task_id=? AND fence_token=? AND state='active'
        """,
        (context[0], int(context[1])),
    ).fetchone()
    owner = str((dict(row).get("lease_owner") if row else "") or "")
    if not owner or not renew_provider_execution_claim(
        context[0], int(context[1]), owner, lease_seconds=lease_seconds
    ):
        raise ApifyExecutionClaimBlocked("provider execution lease could not be renewed")


def _wait_for_apify_run(
    client: Any,
    run_id: str,
    *,
    wait_secs: int | None,
) -> Any:
    """Poll a known run in bounded slices, renewing the durable DB fence."""

    run_client = client.run(run_id)
    deadline = time.monotonic() + max(1, int(wait_secs)) if wait_secs is not None else None
    latest: Any = None
    while True:
        _renew_current_execution_claim()
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if remaining is not None and remaining <= 0:
            return latest if latest is not None else run_client.get()
        slice_seconds = _PROVIDER_WAIT_SLICE_SECONDS
        if remaining is not None:
            slice_seconds = max(1, min(slice_seconds, int(math.ceil(remaining))))
        latest = run_client.wait_for_finish(wait_secs=slice_seconds)
        _renew_current_execution_claim()
        status = str((latest or {}).get("status") or "").strip().upper() if isinstance(latest, Mapping) else ""
        if status in _TERMINAL_APIFY_RUN_STATES:
            return latest


def call_apify_actor(
    client: Any,
    actor_id: str,
    *,
    platform: str = "",
    operation: str = "actor_call",
    source: str = "",
    estimated_cost_usd: float | int | str | None = None,
    **call_kwargs: Any,
) -> Any:
    """The only supported SDK Actor-start boundary in application code."""

    _reject_release_validation_provider_work()
    decision = require_apify_budget(
        operation=operation,
        actor_id=actor_id,
        platform=platform,
        source=source,
        estimated_cost_usd=estimated_cost_usd,
        run_input=call_kwargs.get("run_input"),
    )
    wait_secs_raw = call_kwargs.pop("wait_secs", None)
    wait_secs = int(wait_secs_raw) if wait_secs_raw is not None else None
    if decision.reason == "resume_same_run" and decision.apify_run_id:
        try:
            result = _wait_for_apify_run(
                client,
                decision.apify_run_id,
                wait_secs=wait_secs,
            )
        except Exception:
            # The run identity is known.  Preserve it for another control-plane
            # resume; never fall through to actor.call() and start a second run.
            raise
        run_id = _provider_run_id(result) or decision.apify_run_id
        _mark_provider_outcome(decision, run_id=run_id, unknown=False)
        return _attach_reservation(result, decision)
    _mark_provider_started(decision)
    try:
        started = client.actor(actor_id).start(**call_kwargs)
    except Exception:
        _mark_provider_outcome(decision, unknown=True)
        raise
    run_id = _provider_run_id(started)
    if not run_id:
        _mark_provider_outcome(decision, unknown=True)
        raise ApifyProviderReplayBlocked(
            "provider_start_returned_without_run_id",
            reservation_key=decision.reservation_key,
        )
    # Persist the run identity before the first long wait.  A crash after this
    # commit can only resume this exact run; it can never start a replacement.
    _mark_provider_outcome(decision, run_id=run_id, unknown=False)
    try:
        result = _wait_for_apify_run(client, run_id, wait_secs=wait_secs)
    except Exception:
        # The run id is durable, so keep provider_started (not unknown) and let
        # a later claim resume it through the control-plane run endpoint.
        raise
    if result is None:
        result = started
    run_id = _provider_run_id(result) or run_id
    _mark_provider_outcome(decision, run_id=run_id, unknown=False)
    return _attach_reservation(result, decision)


def is_apify_budget_blocked(value: Any) -> bool:
    return isinstance(value, ApifyBudgetBlocked)


__all__ = [
    "APIFY_BUDGET_BLOCK_CODE",
    "APIFY_BUDGET_SCOPE",
    "ApifyBudgetBlocked",
    "ApifyBudgetDecision",
    "ApifyExecutionClaimBlocked",
    "ApifyProviderReplayBlocked",
    "acquire_provider_execution_claim",
    "apify_execution_context",
    "current_apify_execution_context",
    "apify_budget_decision",
    "call_apify_actor",
    "is_apify_budget_blocked",
    "finalize_provider_execution_claim",
    "renew_provider_execution_claim",
    "require_apify_budget",
    "run_apify_network",
    "settle_apify_reservation",
]
