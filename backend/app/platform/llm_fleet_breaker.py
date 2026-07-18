"""PostgreSQL-backed fleet circuit breaker for exact LLM bindings.

The breaker is intentionally unavailable on SQLite.  Production callers must
fail closed when the shared store or schema is unavailable; silently falling
back to a process-local breaker would allow every worker to probe at once.
"""
from __future__ import annotations

import os
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import AI_BREAKER_FAILURE_THRESHOLD, AI_BREAKER_RECOVERY_TIMEOUT_SEC
from app.core.logging import get_logger
from app.db.connection import close_standalone_conn, is_postgres_runtime, open_standalone_conn


_TABLE = "vkpi_llm_fleet_breakers"
logger = get_logger(__name__)
_SUCCESS_STATUSES = frozenset(
    {
        "success",
        "empty_response",
        "invalid_response",
        "model_mismatch",
        "parse_failure",
        "validation_failure",
    }
)


class FleetBreakerError(RuntimeError):
    reason = "fleet_breaker_error"
    scope = "llm_fleet_breaker"


class FleetBreakerOpen(FleetBreakerError):
    reason = "fleet_breaker_open"

    def __init__(self, *, provider: str, model: str, retry_at: str = "") -> None:
        super().__init__(self.reason)
        self.provider = provider
        self.model = model
        self.retry_at = retry_at


class FleetBreakerUnavailable(FleetBreakerError):
    reason = "fleet_breaker_store_unavailable"


class StaleFleetBreakerPermit(FleetBreakerError):
    reason = "fleet_breaker_stale_fence"


@dataclass(frozen=True)
class FleetBreakerPermit:
    provider: str
    model: str
    state: str
    generation: int
    version: int
    owner: str = ""
    fence: int = 0


@dataclass(frozen=True)
class FleetBreakerMutation:
    accepted: bool
    state: str
    generation: int
    version: int


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _failure_threshold() -> int:
    return _bounded_int(
        os.environ.get("VKPI_LLM_FLEET_BREAKER_FAILURE_THRESHOLD"),
        default=int(AI_BREAKER_FAILURE_THRESHOLD),
        minimum=1,
        maximum=100,
    )


def _recovery_seconds() -> int:
    return _bounded_int(
        os.environ.get("VKPI_LLM_FLEET_BREAKER_RECOVERY_SECONDS"),
        default=int(AI_BREAKER_RECOVERY_TIMEOUT_SEC),
        minimum=1,
        maximum=86_400,
    )


def _half_open_lease_seconds() -> int:
    return _bounded_int(
        os.environ.get("VKPI_LLM_FLEET_BREAKER_HALF_OPEN_LEASE_SECONDS"),
        # 2026-07-18:provider HTTP 超时提到 90s(长文生成),half-open 租约
        # 必须 ≥ 2×超时(否则探测请求会在租约过期后才返回,重试被误判失败)。
        default=200,
        minimum=5,
        maximum=900,
    )


def _identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


def _normalise_binding(provider: str, model: str) -> tuple[str, str]:
    normalised_provider = str(provider or "").strip().lower()
    normalised_provider = {"gemini": "google", "claude": "anthropic"}.get(
        normalised_provider, normalised_provider
    )
    normalised_model = str(model or "").strip()
    if not normalised_provider or not normalised_model:
        raise ValueError("provider and model are required")
    if len(normalised_provider) > 64 or len(normalised_model) > 255:
        raise ValueError("provider or model is too long")
    return normalised_provider, normalised_model


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _now(value).isoformat(timespec="microseconds")


def _open_conn() -> Any:
    if not is_postgres_runtime():
        raise FleetBreakerUnavailable("shared PostgreSQL runtime is required")
    try:
        return open_standalone_conn()
    except Exception as exc:  # noqa: BLE001 - public boundary must be stable
        raise FleetBreakerUnavailable(type(exc).__name__) from exc


def _close(conn: Any) -> None:
    try:
        close_standalone_conn(conn)
    except Exception:
        logger.debug("llm.fleet_breaker.connection_close_failed", exc_info=True)


def _locked_row(conn: Any, provider: str, model: str) -> Any:
    conn.execute(
        f"""
        INSERT INTO {_TABLE} (provider, model_name)
        VALUES (?, ?)
        ON CONFLICT (provider, model_name) DO NOTHING
        """,
        (provider, model),
    )
    row = conn.execute(
        f"""
        SELECT provider, model_name, state, failure_streak, opened_until,
               generation, version, half_open_owner, half_open_fence,
               half_open_lease_expires_at
        FROM {_TABLE}
        WHERE provider=? AND model_name=?
        FOR UPDATE
        """,
        (provider, model),
    ).fetchone()
    if row is None:
        raise FleetBreakerUnavailable("breaker row unavailable")
    return row


def _permit(row: Any) -> FleetBreakerPermit:
    return FleetBreakerPermit(
        provider=str(row["provider"]),
        model=str(row["model_name"]),
        state=str(row["state"]),
        generation=int(row["generation"]),
        version=int(row["version"]),
        owner=str(row["half_open_owner"] or ""),
        fence=int(row["half_open_fence"] or 0),
    )


def _mutation(row: Any, *, accepted: bool = True) -> FleetBreakerMutation:
    return FleetBreakerMutation(
        accepted=accepted,
        state=str(row["state"]),
        generation=int(row["generation"]),
        version=int(row["version"]),
    )


def _read_locked(conn: Any, provider: str, model: str) -> Any:
    row = conn.execute(
        f"""
        SELECT provider, model_name, state, failure_streak, opened_until,
               generation, version, half_open_owner, half_open_fence,
               half_open_lease_expires_at
        FROM {_TABLE}
        WHERE provider=? AND model_name=?
        FOR UPDATE
        """,
        (provider, model),
    ).fetchone()
    if row is None:
        raise FleetBreakerUnavailable("breaker row missing")
    return row


def acquire_fleet_breaker_permit(
    provider: str,
    model: str,
    *,
    owner: str | None = None,
    now: datetime | None = None,
) -> FleetBreakerPermit:
    provider, model = _normalise_binding(provider, model)
    current = _now(now)
    conn = _open_conn()
    try:
        row = _locked_row(conn, provider, model)
        state = str(row["state"])
        if state == "closed":
            permit = _permit(row)
            conn.commit()
            return permit

        if state == "open":
            opened_until = _parse_timestamp(row["opened_until"])
            if opened_until is not None and opened_until > current:
                conn.commit()
                raise FleetBreakerOpen(
                    provider=provider,
                    model=model,
                    retry_at=_iso(opened_until),
                )

        if state == "half_open":
            lease_expires_at = _parse_timestamp(row["half_open_lease_expires_at"])
            if lease_expires_at is not None and lease_expires_at > current:
                conn.commit()
                raise FleetBreakerOpen(
                    provider=provider,
                    model=model,
                    retry_at=_iso(lease_expires_at),
                )

        probe_owner = str(owner or _identity())
        lease_expires_at = current + timedelta(seconds=_half_open_lease_seconds())
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET state='half_open',
                opened_until=NULL,
                half_open_owner=?,
                half_open_fence=half_open_fence + 1,
                half_open_lease_expires_at=?,
                version=version + 1,
                updated_at=NOW()
            WHERE provider=? AND model_name=?
            """,
            (probe_owner, _iso(lease_expires_at), provider, model),
        )
        permit = _permit(_read_locked(conn, provider, model))
        conn.commit()
        return permit
    except FleetBreakerOpen:
        raise
    except Exception as exc:  # noqa: BLE001 - store failures are fail-closed
        try:
            conn.rollback()
        except Exception:
            logger.debug("llm.fleet_breaker.acquire_rollback_failed", exc_info=True)
        if isinstance(exc, FleetBreakerError):
            raise
        raise FleetBreakerUnavailable(type(exc).__name__) from exc
    finally:
        _close(conn)


def renew_fleet_breaker_permit(
    permit: FleetBreakerPermit,
    *,
    now: datetime | None = None,
) -> bool:
    """Renew only the exact live half-open owner without changing its fence.

    A renewal is a lease maintenance write, not a state transition, so it must
    not increment ``version``.  A takeover changes owner/fence/version and
    therefore makes this conditional update affect zero rows.
    """

    if permit.state != "half_open":
        return True
    provider, model = _normalise_binding(permit.provider, permit.model)
    expires_at = _now(now) + timedelta(seconds=_half_open_lease_seconds())
    conn = _open_conn()
    try:
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET half_open_lease_expires_at=?, updated_at=NOW()
            WHERE provider=? AND model_name=? AND state='half_open'
              AND generation=? AND version=? AND half_open_owner=?
              AND half_open_fence=?
            """,
            (
                _iso(expires_at),
                provider,
                model,
                int(permit.generation),
                int(permit.version),
                str(permit.owner),
                int(permit.fence),
            ),
        )
        accepted = int(getattr(cursor, "rowcount", 0) or 0) == 1
        conn.commit()
        return accepted
    except Exception as exc:  # noqa: BLE001 - loss of the shared lease is fatal
        try:
            conn.rollback()
        except Exception:
            logger.debug("llm.fleet_breaker.renew_rollback_failed", exc_info=True)
        if isinstance(exc, FleetBreakerError):
            raise
        raise FleetBreakerUnavailable(type(exc).__name__) from exc
    finally:
        _close(conn)


class FleetBreakerSession:
    """Own one permit and keep a half-open probe live during provider I/O."""

    def __init__(self, permit: FleetBreakerPermit) -> None:
        self.permit = permit
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None
        if permit.state == "half_open":
            self._thread = threading.Thread(
                target=self._heartbeat,
                name=f"llm-breaker-{permit.provider}-{permit.fence}",
                daemon=True,
            )
            self._thread.start()

    def _heartbeat(self) -> None:
        interval = max(1.0, min(30.0, float(_half_open_lease_seconds()) / 3.0))
        while not self._stop.wait(interval):
            try:
                if not renew_fleet_breaker_permit(self.permit):
                    self._lost.set()
                    return
            except Exception:
                logger.error(
                    "llm.fleet_breaker.heartbeat_failed",
                    extra={
                        "provider": self.permit.provider,
                        "model": self.permit.model,
                        "fence": self.permit.fence,
                    },
                    exc_info=True,
                )
                self._lost.set()
                return

    def _finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def complete(self, outcome: Any) -> FleetBreakerMutation:
        try:
            if self._lost.is_set():
                raise StaleFleetBreakerPermit("half-open heartbeat lost")
            return complete_fleet_breaker_permit(self.permit, outcome)
        finally:
            self._finish()

    def abandon(self) -> FleetBreakerMutation:
        try:
            if self._lost.is_set():
                raise StaleFleetBreakerPermit("half-open heartbeat lost")
            return abandon_fleet_breaker_permit(self.permit)
        finally:
            self._finish()


def begin_fleet_breaker_session(provider: str, model: str) -> FleetBreakerSession:
    return FleetBreakerSession(acquire_fleet_breaker_permit(provider, model))


def _assert_half_open_fence(
    row: Any,
    permit: FleetBreakerPermit,
    *,
    now: datetime | None = None,
) -> None:
    lease_expires_at = _parse_timestamp(row["half_open_lease_expires_at"])
    if (
        str(row["state"]) != "half_open"
        or int(row["generation"]) != int(permit.generation)
        or int(row["version"]) != int(permit.version)
        or str(row["half_open_owner"] or "") != str(permit.owner)
        or int(row["half_open_fence"] or 0) != int(permit.fence)
        or lease_expires_at is None
        or lease_expires_at <= _now(now)
    ):
        raise StaleFleetBreakerPermit("half-open permit is stale")


def record_fleet_breaker_success(permit: FleetBreakerPermit) -> FleetBreakerMutation:
    provider, model = _normalise_binding(permit.provider, permit.model)
    conn = _open_conn()
    try:
        row = _read_locked(conn, provider, model)
        if permit.state == "half_open":
            _assert_half_open_fence(row, permit)
            conn.execute(
                f"""
                UPDATE {_TABLE}
                SET state='closed', failure_streak=0, opened_until=NULL,
                    generation=generation + 1, version=version + 1,
                    half_open_owner=NULL, half_open_lease_expires_at=NULL,
                    last_success_at=NOW(), updated_at=NOW()
                WHERE provider=? AND model_name=?
                """,
                (provider, model),
            )
            result = _mutation(_read_locked(conn, provider, model))
        elif str(row["state"]) == "closed" and int(row["generation"]) == permit.generation:
            conn.execute(
                f"""
                UPDATE {_TABLE}
                SET failure_streak=0, version=version + 1,
                    last_success_at=NOW(), updated_at=NOW()
                WHERE provider=? AND model_name=?
                """,
                (provider, model),
            )
            result = _mutation(_read_locked(conn, provider, model))
        else:
            result = _mutation(row, accepted=False)
        conn.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            logger.debug("llm.fleet_breaker.success_rollback_failed", exc_info=True)
        if isinstance(exc, FleetBreakerError):
            raise
        raise FleetBreakerUnavailable(type(exc).__name__) from exc
    finally:
        _close(conn)


def record_fleet_breaker_failure(
    permit: FleetBreakerPermit,
    failure_class: str,
    *,
    now: datetime | None = None,
) -> FleetBreakerMutation:
    provider, model = _normalise_binding(permit.provider, permit.model)
    failure_class = str(failure_class or "provider_failure").strip().lower()[:80]
    current = _now(now)
    conn = _open_conn()
    try:
        row = _read_locked(conn, provider, model)
        if permit.state == "half_open":
            _assert_half_open_fence(row, permit, now=current)
            next_streak = max(_failure_threshold(), int(row["failure_streak"]) + 1)
            conn.execute(
                f"""
                UPDATE {_TABLE}
                SET state='open', failure_streak=?, opened_until=?,
                    generation=generation + 1, version=version + 1,
                    half_open_owner=NULL, half_open_lease_expires_at=NULL,
                    last_failure_class=?, last_failure_at=NOW(), updated_at=NOW()
                WHERE provider=? AND model_name=?
                """,
                (
                    next_streak,
                    _iso(current + timedelta(seconds=_recovery_seconds())),
                    failure_class,
                    provider,
                    model,
                ),
            )
            result = _mutation(_read_locked(conn, provider, model))
        elif str(row["state"]) == "closed" and int(row["generation"]) == permit.generation:
            next_streak = int(row["failure_streak"]) + 1
            should_open = next_streak >= _failure_threshold()
            conn.execute(
                f"""
                UPDATE {_TABLE}
                SET state=?, failure_streak=?, opened_until=?,
                    generation=generation + ?, version=version + 1,
                    last_failure_class=?, last_failure_at=NOW(), updated_at=NOW()
                WHERE provider=? AND model_name=?
                """,
                (
                    "open" if should_open else "closed",
                    next_streak,
                    _iso(current + timedelta(seconds=_recovery_seconds())) if should_open else None,
                    1 if should_open else 0,
                    failure_class,
                    provider,
                    model,
                ),
            )
            result = _mutation(_read_locked(conn, provider, model))
        else:
            result = _mutation(row, accepted=False)
        conn.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            logger.debug("llm.fleet_breaker.failure_rollback_failed", exc_info=True)
        if isinstance(exc, FleetBreakerError):
            raise
        raise FleetBreakerUnavailable(type(exc).__name__) from exc
    finally:
        _close(conn)


def abandon_fleet_breaker_permit(
    permit: FleetBreakerPermit,
    *,
    now: datetime | None = None,
) -> FleetBreakerMutation:
    """Release an unused half-open probe by reopening it with a new generation."""

    if permit.state != "half_open":
        return FleetBreakerMutation(False, permit.state, permit.generation, permit.version)
    provider, model = _normalise_binding(permit.provider, permit.model)
    current = _now(now)
    conn = _open_conn()
    try:
        row = _read_locked(conn, provider, model)
        _assert_half_open_fence(row, permit, now=current)
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET state='open', opened_until=?, generation=generation + 1,
                version=version + 1, half_open_owner=NULL,
                half_open_lease_expires_at=NULL, updated_at=NOW()
            WHERE provider=? AND model_name=?
            """,
            (
                _iso(current + timedelta(seconds=_recovery_seconds())),
                provider,
                model,
            ),
        )
        result = _mutation(_read_locked(conn, provider, model))
        conn.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            logger.debug("llm.fleet_breaker.abandon_rollback_failed", exc_info=True)
        if isinstance(exc, FleetBreakerError):
            raise
        raise FleetBreakerUnavailable(type(exc).__name__) from exc
    finally:
        _close(conn)


def classify_fleet_breaker_failure(outcome: Any) -> str | None:
    """Return the stable fleet failure class for provider results/exceptions."""

    if isinstance(outcome, BaseException):
        if isinstance(outcome, TimeoutError) or "timeout" in type(outcome).__name__.lower():
            return "timeout"
        response = getattr(outcome, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code == 429:
            return "provider_429"
        if isinstance(status_code, int) and status_code >= 500:
            return "provider_5xx"
        return "transport_error"

    if isinstance(outcome, dict):
        status = str(outcome.get("status") or "").strip().lower()
        status_code = outcome.get("status_code")
        if not isinstance(status_code, int):
            error = str(outcome.get("error") or "").strip().lower()
            if error.startswith("http_") and error[5:8].isdigit():
                status_code = int(error[5:8])
        if status_code == 429:
            return "provider_429"
        if status_code in {401, 403}:
            return "provider_auth_error"
        if isinstance(status_code, int) and status_code >= 500:
            return "provider_5xx"
        if isinstance(status_code, int) and 400 <= status_code < 500:
            # Invalid request/payload errors are not fleet health failures.
            return None
    else:
        status = str(outcome or "").strip().lower()

    if status in {"provider_429", "http_429", "rate_limited", "rate_limit"}:
        return "provider_429"
    if status == "provider_5xx" or status.startswith("http_5"):
        return "provider_5xx"
    if status in {"timeout", "provider_timeout"}:
        return "timeout"
    if status in {"transport_error", "provider_exception"}:
        return "transport_error"
    if status == "provider_http_error":
        # When an adapter cannot provide a status code, fail safe after the
        # configured threshold instead of treating a rejected call as health.
        return "provider_failure"
    if status and status not in _SUCCESS_STATUSES:
        return "provider_failure"
    return None


def complete_fleet_breaker_permit(
    permit: FleetBreakerPermit,
    outcome: Any,
) -> FleetBreakerMutation:
    failure_class = classify_fleet_breaker_failure(outcome)
    if failure_class:
        return record_fleet_breaker_failure(permit, failure_class)
    return record_fleet_breaker_success(permit)
