"""Atomic budget reservations for strict production LLM calls.

The existing budget preflight is useful operator telemetry, but a read followed
by a provider call is racy when several workers run at once.  This module owns
the production spend boundary: it locks the configured cap rows, includes all
open reservations, and persists one reservation before any provider network
I/O.  Successful calls settle exactly once; uncertain provider outcomes remain
open and therefore conservatively consume allowance until reconciled.

Migration 258 is the only production DDL owner.  A missing table fails closed.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists


logger = get_logger(__name__)

_OPEN_STATES = ("reserved", "provider_started", "unknown")
_SINGLE_CALL_SCOPE = "single_call"
_MONTHLY_SCOPE = "monthly_total"
_PROGRESS_METADATA_KEYS = frozenset(
    {
        "surface",
        "task_binding",
        "organization_id",
        "staff_id",
        "thread_id",
        "thread_uid",
        "search_session_id",
        "search_session_item_id",
        "kol_pool_id",
        "project_id",
        "target_type",
        "target_id",
        "platform",
        "handle",
        "batch_size",
        "aggregate_only",
        "claim_status",
        "parent_job_id",
        "phase",
        "subphase",
        "attempt_index",
        "attempt_total",
        "target_label",
        "execution_class",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _load_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(str(value or "[]"))
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item or "").strip()]


def _provider_scope(provider: str) -> str:
    key = str(provider or "").strip().lower()
    key = {"google": "gemini", "anthropic": "claude"}.get(key, key)
    return f"provider:{key}" if key else ""


def request_fingerprint(*, provider: str, model: str, purpose: str, prompt: str) -> str:
    """Hash request identity without storing prompt content."""

    prompt_hash = hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()
    return hashlib.sha256(
        _json(
            {
                "provider": str(provider or "").strip().lower(),
                "model": str(model or "").strip(),
                "purpose": str(purpose or "").strip(),
                "prompt_sha256": prompt_hash,
            }
        ).encode("utf-8")
    ).hexdigest()


def _progress_metadata(
    metadata: dict[str, Any] | None,
    *,
    staff: dict[str, Any] | None,
    triggered_by: Any,
) -> dict[str, Any]:
    """Keep only bounded correlation keys; never persist request content."""

    source = metadata if isinstance(metadata, dict) else {}
    safe: dict[str, Any] = {}
    for key in _PROGRESS_METADATA_KEYS:
        value = source.get(key)
        if value is None or isinstance(value, (dict, list, tuple, set)):
            continue
        if isinstance(value, str):
            clean = value.strip()[:160]
            if clean:
                safe[key] = clean
        elif isinstance(value, (bool, int, float)):
            safe[key] = value
    # Several staged call sites used the generic ``total`` key while the
    # progress API intentionally exposes ``attempt_total``. Normalize it once
    # at persistence so queued/running reservations can render "attempt x/y"
    # without widening the stored metadata contract.
    if "attempt_total" not in safe:
        attempt_total = source.get("total")
        if (
            not isinstance(attempt_total, bool)
            and isinstance(attempt_total, (int, float))
            and 0 < float(attempt_total) <= 10_000
        ):
            safe["attempt_total"] = int(attempt_total)
    if isinstance(staff, dict):
        for source_key, target_key in (
            ("id", "staff_id"),
            ("staff_id", "staff_id"),
            ("organization_id", "organization_id"),
        ):
            value = staff.get(source_key)
            if value not in (None, "") and target_key not in safe:
                safe[target_key] = str(value)[:80]
    if triggered_by not in (None, "") and "staff_id" not in safe:
        if isinstance(triggered_by, (str, int, float)):
            safe["staff_id"] = str(triggered_by)[:80]
    return safe


@dataclass(frozen=True, slots=True)
class LlmBudgetReservation:
    reservation_key: str
    provider: str
    model: str
    purpose: str
    request_hash: str
    provider_scope: str
    cost_scope: str
    cumulative_scopes: tuple[str, ...]
    estimated_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_key": self.reservation_key,
            "provider": self.provider,
            "model": self.model,
            "purpose": self.purpose,
            "request_hash": self.request_hash,
            "provider_scope": self.provider_scope,
            "cost_scope": self.cost_scope,
            "cumulative_scopes": list(self.cumulative_scopes),
            "estimated_cost_usd": self.estimated_cost_usd,
        }


class LlmBudgetBlocked(RuntimeError):
    def __init__(self, reason: str, *, estimated_cost_usd: float = 0.0, scope: str = "") -> None:
        super().__init__(reason)
        self.reason = str(reason or "budget_blocked")
        self.estimated_cost_usd = max(0.0, float(estimated_cost_usd or 0.0))
        self.scope = str(scope or "")

    def payload(self) -> dict[str, Any]:
        return {
            "status": "budget_blocked",
            "reason": self.reason,
            "scope": self.scope,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def _ensure_schema() -> None:
    if not table_exists("vkpi_llm_budget_reservations"):
        raise RuntimeError("migration 258 schema missing: vkpi_llm_budget_reservations")
    get_conn().execute(
        """
        SELECT reservation_key,provider,model_name,purpose,request_hash,
               provider_scope,cost_scope,cumulative_scopes_json,
               estimated_cost_usd,actual_cost_usd,state,metadata_json,
               reserved_at,provider_started_at,settled_at,updated_at
        FROM vkpi_llm_budget_reservations WHERE 1=0
        """
    )


def _open_reserved_for_scope(
    conn: Any,
    scope: str,
    *,
    provider_scope: str,
    cost_scope: str,
) -> float:
    if scope == _MONTHLY_SCOPE:
        sql = (
            "SELECT COALESCE(SUM(estimated_cost_usd),0) AS reserved "
            "FROM vkpi_llm_budget_reservations "
            "WHERE state IN ('reserved','provider_started','unknown')"
        )
        params: tuple[Any, ...] = ()
    elif scope == provider_scope:
        sql = (
            "SELECT COALESCE(SUM(estimated_cost_usd),0) AS reserved "
            "FROM vkpi_llm_budget_reservations "
            "WHERE state IN ('reserved','provider_started','unknown') AND provider_scope=?"
        )
        params = (provider_scope,)
    elif scope == cost_scope:
        sql = (
            "SELECT COALESCE(SUM(estimated_cost_usd),0) AS reserved "
            "FROM vkpi_llm_budget_reservations "
            "WHERE state IN ('reserved','provider_started','unknown') AND cost_scope=?"
        )
        params = (cost_scope,)
    else:
        return 0.0
    row = conn.execute(sql, params).fetchone()
    return float((dict(row).get("reserved") if row else 0.0) or 0.0)


_REAP_TTL_HOURS_DEFAULT = 24.0
_REAP_MIN_INTERVAL_SECONDS = 600.0
_last_reap_monotonic: float = 0.0


def reap_stale_reservations(*, ttl_hours: float | None = None) -> int:
    """超龄开放态预约(reserved/provider_started/unknown)→ 'expired',释放占用额度。

    2026-07-18 体检修:'unknown' 的 fail-closed 设计(结果不可证则继续占额度)
    没有任何回收器,81 笔僵尸预约永久占 $5.84,雷达 $1 日闸已被吃 16%——
    单调恶化直至相关 scope 永久 budget_blocked。真实 provider 调用分钟级落定,
    TTL 24h(VKPI_LLM_RESERVATION_TTL_HOURS 可调)远在安全边界外。
    'expired'(迁移 273)不被 _open_reserved_for_scope 计入;行保留可审计。
    """
    import os as _os

    try:
        hours = float(ttl_hours or _os.getenv("VKPI_LLM_RESERVATION_TTL_HOURS", "") or _REAP_TTL_HOURS_DEFAULT)
    except (TypeError, ValueError):
        hours = _REAP_TTL_HOURS_DEFAULT
    hours = max(1.0, hours)
    conn = get_conn()
    cursor = conn.execute(
        """
        UPDATE vkpi_llm_budget_reservations
        SET state='expired', updated_at=NOW()
        WHERE state IN ('reserved','provider_started','unknown')
          AND reserved_at < NOW() - (? * INTERVAL '1 hour')
        """,
        (hours,),
    )
    reaped = int(getattr(cursor, "rowcount", 0) or 0)
    conn.commit()
    if reaped:
        logger.warning(
            "vkpi.llm_reservations.reaped_stale",
            extra={"count": reaped, "ttl_hours": hours},
        )
    return reaped


def _maybe_reap_stale_reservations() -> None:
    """机会式触发(每进程节流 10 分钟),预约路径自愈,不依赖调度器活着。"""
    global _last_reap_monotonic
    import time as _time

    now = _time.monotonic()
    if now - _last_reap_monotonic < _REAP_MIN_INTERVAL_SECONDS:
        return
    _last_reap_monotonic = now
    try:
        reap_stale_reservations()
    except Exception:
        logger.debug("vkpi.llm_reservations.reap_failed", exc_info=True)


def reserve_llm_budget(
    *,
    provider: str,
    model: str,
    purpose: str,
    prompt: str,
    estimated_cost_usd: float,
    cost_scope: str = "",
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    triggered_by: Any = None,
) -> LlmBudgetReservation:
    """Atomically reserve configured core LLM allowance or fail closed."""

    provider_key = str(provider or "").strip().lower()
    model_name = str(model or "").strip()
    estimate = max(0.0, float(estimated_cost_usd or 0.0))
    provider_budget_scope = _provider_scope(provider_key)
    clean_cost_scope = str(cost_scope or "").strip().lower()
    if not provider_key or not model_name:
        raise LlmBudgetBlocked("invalid_exact_model_binding", estimated_cost_usd=estimate)
    if estimate <= 0:
        raise LlmBudgetBlocked("estimate_unavailable", estimated_cost_usd=estimate)
    if not provider_budget_scope:
        raise LlmBudgetBlocked("provider_budget_scope_unavailable", estimated_cost_usd=estimate)

    try:
        _ensure_schema()
    except Exception as exc:
        raise LlmBudgetBlocked(
            f"reservation_schema_unavailable:{type(exc).__name__}",
            estimated_cost_usd=estimate,
        ) from exc

    # 2026-07-18:预约前机会式回收僵尸预约(节流 10 分钟),防 unknown 泄漏
    # 单调吃满 scope 日闸。独立事务,失败不阻断预约主流程。
    _maybe_reap_stale_reservations()

    # Roll an expired cron window before acquiring the transaction's cap locks.
    # The helper owns its own commit, so it must run before the atomic section.
    if clean_cost_scope.startswith("cron:"):
        try:
            from app.domains.costs import budget_guard

            budget_guard.get_budget_status(clean_cost_scope, estimated_cost=estimate)
        except Exception as exc:
            raise LlmBudgetBlocked(
                f"budget_window_unavailable:{type(exc).__name__}",
                estimated_cost_usd=estimate,
                scope=clean_cost_scope,
            ) from exc

    request_hash = request_fingerprint(
        provider=provider_key,
        model=model_name,
        purpose=purpose,
        prompt=prompt,
    )
    reservation_key = f"llmres-{secrets.token_hex(16)}"
    core_scopes = (_MONTHLY_SCOPE, _SINGLE_CALL_SCOPE, provider_budget_scope)
    requested_scopes = list(dict.fromkeys([*core_scopes, clean_cost_scope]))
    requested_scopes = [scope for scope in requested_scopes if scope]
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    conn = get_conn()
    try:
        rows: dict[str, dict[str, Any]] = {}
        for scope in sorted(requested_scopes):
            row = conn.execute(
                "SELECT * FROM vkpi_provider_budget_caps WHERE scope=?" + lock,
                (scope,),
            ).fetchone()
            if row:
                rows[scope] = dict(row)

        missing_core = [scope for scope in core_scopes if scope not in rows]
        if missing_core:
            conn.rollback()
            raise LlmBudgetBlocked(
                "budget_scope_not_configured",
                estimated_cost_usd=estimate,
                scope=missing_core[0],
            )

        cumulative_scopes: list[str] = []
        for scope in requested_scopes:
            row = rows.get(scope)
            if row is None:
                # Purpose-specific scopes are optional; the three core caps are
                # mandatory and were checked above.
                continue
            cap = float(row.get("cap_usd") or 0.0)
            if scope == _SINGLE_CALL_SCOPE:
                if cap > 0 and estimate > cap:
                    conn.rollback()
                    raise LlmBudgetBlocked(
                        "hard_stop_or_projected_cap",
                        estimated_cost_usd=estimate,
                        scope=scope,
                    )
                continue
            current = float(row.get("current_spend") or 0.0)
            hard_stop = max(0.0, min(1.0, float(row.get("hard_stop_at") or 1.0)))
            open_reserved = _open_reserved_for_scope(
                conn,
                scope,
                provider_scope=provider_budget_scope,
                cost_scope=clean_cost_scope,
            )
            if cap > 0 and current + open_reserved + estimate >= cap * hard_stop:
                conn.rollback()
                raise LlmBudgetBlocked(
                    "hard_stop_or_projected_cap",
                    estimated_cost_usd=estimate,
                    scope=scope,
                )
            cumulative_scopes.append(scope)

        now = _utcnow()
        conn.execute(
            """
            INSERT INTO vkpi_llm_budget_reservations
              (reservation_key,provider,model_name,purpose,request_hash,
               provider_scope,cost_scope,cumulative_scopes_json,
               estimated_cost_usd,state,metadata_json,reserved_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,'reserved',?,?,?)
            """,
            (
                reservation_key,
                provider_key,
                model_name,
                str(purpose or ""),
                request_hash,
                provider_budget_scope,
                clean_cost_scope,
                _json(cumulative_scopes),
                estimate,
                _json(
                    {
                        "execution_class": "production",
                        "request_content_recorded": False,
                        **_progress_metadata(
                            metadata,
                            staff=staff,
                            triggered_by=triggered_by,
                        ),
                    }
                ),
                now,
                now,
            ),
        )
        conn.commit()
        return LlmBudgetReservation(
            reservation_key=reservation_key,
            provider=provider_key,
            model=model_name,
            purpose=str(purpose or ""),
            request_hash=request_hash,
            provider_scope=provider_budget_scope,
            cost_scope=clean_cost_scope,
            cumulative_scopes=tuple(cumulative_scopes),
            estimated_cost_usd=estimate,
        )
    except Exception:
        conn.rollback()
        raise


def mark_llm_provider_started(reservation_key: str) -> None:
    """Commit the point immediately before provider network I/O."""

    _ensure_schema()
    conn = get_conn()
    now = _utcnow()
    cursor = conn.execute(
        """
        UPDATE vkpi_llm_budget_reservations
        SET state='provider_started',provider_started_at=?,updated_at=?
        WHERE reservation_key=? AND state='reserved'
        """,
        (now, now, str(reservation_key or "")),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        conn.rollback()
        raise LlmBudgetBlocked("reservation_not_startable")
    conn.commit()


def mark_llm_provider_unknown(reservation_key: str) -> bool:
    """Retain an uncertain provider-start estimate until reconciliation."""

    _ensure_schema()
    conn = get_conn()
    cursor = conn.execute(
        """
        UPDATE vkpi_llm_budget_reservations
        SET state='unknown',updated_at=?
        WHERE reservation_key=? AND state IN ('provider_started','unknown')
        """,
        (_utcnow(), str(reservation_key or "")),
    )
    conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def release_llm_reservation(reservation_key: str) -> bool:
    """Release a reservation only when provider network I/O never started."""

    _ensure_schema()
    conn = get_conn()
    cursor = conn.execute(
        """
        UPDATE vkpi_llm_budget_reservations
        SET state='released',updated_at=?
        WHERE reservation_key=? AND state='reserved'
        """,
        (_utcnow(), str(reservation_key or "")),
    )
    conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def settle_llm_reservation(reservation_key: str, actual_cost_usd: float) -> dict[str, Any]:
    """Settle one confirmed provider response and increment caps once."""

    _ensure_schema()
    key = str(reservation_key or "").strip()
    actual = max(0.0, float(actual_cost_usd or 0.0))
    if not key:
        return {"settled": False, "reason": "no_reservation"}
    conn = get_conn()
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    try:
        row = conn.execute(
            "SELECT * FROM vkpi_llm_budget_reservations WHERE reservation_key=?" + lock,
            (key,),
        ).fetchone()
        if not row:
            conn.rollback()
            return {"settled": False, "reason": "missing_reservation"}
        data = dict(row)
        state = str(data.get("state") or "")
        if state == "settled":
            conn.rollback()
            return {
                "settled": False,
                "reason": "already_settled",
                "actual_cost_usd": float(data.get("actual_cost_usd") or 0.0),
            }
        if state != "provider_started":
            conn.rollback()
            return {"settled": False, "reason": "provider_outcome_not_confirmed"}

        scopes = list(dict.fromkeys(_load_json_list(data.get("cumulative_scopes_json"))))
        for scope in sorted(scopes):
            cap_row = conn.execute(
                "SELECT scope FROM vkpi_provider_budget_caps WHERE scope=?" + lock,
                (scope,),
            ).fetchone()
            if not cap_row:
                raise RuntimeError(f"reservation budget scope disappeared: {scope}")
        for scope in scopes:
            conn.execute(
                """
                UPDATE vkpi_provider_budget_caps
                SET current_spend=COALESCE(current_spend,0)+?
                WHERE scope=?
                """,
                (actual, scope),
            )
        now = _utcnow()
        cursor = conn.execute(
            """
            UPDATE vkpi_llm_budget_reservations
            SET state='settled',actual_cost_usd=?,settled_at=?,updated_at=?
            WHERE reservation_key=? AND state='provider_started'
            """,
            (actual, now, now, key),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            raise RuntimeError("reservation settlement lost its state fence")
        conn.commit()
        return {
            "settled": True,
            "actual_cost_usd": actual,
            "scopes_updated": scopes,
        }
    except Exception:
        conn.rollback()
        raise


__all__ = [
    "LlmBudgetBlocked",
    "LlmBudgetReservation",
    "mark_llm_provider_started",
    "mark_llm_provider_unknown",
    "release_llm_reservation",
    "request_fingerprint",
    "reserve_llm_budget",
    "settle_llm_reservation",
]
