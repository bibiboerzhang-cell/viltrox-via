"""Claimed-job execution boundary extracted from the Apify worker entrypoint.

The caller supplies its live namespace so existing operational monkeypatches
and unit tests continue to control the exact same provider and DB boundaries.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import psycopg
from app.workers.apify_jobs_worker_locks import WorkerConnectionRetired, retire_worker_connection, worker_lock_cleanup_failed


class SharedProviderRateDeferred(RuntimeError):
    """No provider was called; shared admission state requires a later retry."""

    def __init__(self, reason: str, retry_delay_seconds: float = 30.0) -> None:
        super().__init__(reason)
        self.retry_delay_seconds = max(0.1, float(retry_delay_seconds))


def shared_provider_rate_deferral(exc: BaseException) -> SharedProviderRateDeferred | None:
    """Cleanup/DB errors must not erase a pre-provider admission deferral."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, SharedProviderRateDeferred):
            return exc
        cause = exc.__cause__ or exc.__context__
        if cause is None:
            break
        exc = cause
    return None


def _provider_calls_performed_truth(raw_value: Any) -> bool | None:
    value: Any = raw_value
    if isinstance(raw_value, (str, bytes, bytearray)):
        try:
            value = json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(value, Mapping):
        return None
    truth = value.get("provider_calls_performed")
    return truth if isinstance(truth, bool) else None


def execute_claimed_job_impl(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    namespace: Mapping[str, Any],
) -> str:
    active = namespace["release_validation_active"]
    requeue = namespace["_requeue_job"]
    job_id = int(job["id"])
    # The marker may appear after the row claim. This is the final check before
    # a durable provider lease and therefore before billed/external work.
    if active():
        requeue(
            conn,
            job_id,
            "release validation fence activated after database claim",
        )
        return "queued"

    lease_owner = str(job.get("lease_owner") or "").strip()
    provider_task_id = f"apify-job:{job_id}"
    fence = 0
    retire_connection = False
    deferred = None
    with namespace["db_connection_sync_scope"]():
        try:
            fence = namespace["acquire_provider_execution_claim"](
                provider_task_id,
                lease_owner,
                job_type=str(job.get("job_type") or ""),
                lease_seconds=namespace["STALE_RECLAIM_SECONDS"],
            )
            with namespace["apify_execution_context"](provider_task_id, fence):
                with namespace["_running_job_heartbeat"](
                    job_id,
                    lease_owner,
                    provider_task_id,
                    fence,
                ):
                    namespace["_process_claimed_job"](conn, job)
            with conn.cursor(row_factory=namespace["dict_row"]) as cur:
                cur.execute(
                    "SELECT status, last_error, payload FROM apify_jobs WHERE id=%s",
                    (job_id,),
                )
                status_row = cur.fetchone() or {}
                status = str(status_row.get("status") or "").lower()
            if status in {"done", "blocked", "failed", "triage"}:
                namespace["_sync_search_session_job"](
                    conn,
                    job_id,
                    raw_status=status,
                    reason=str(status_row.get("last_error") or ""),
                )
            if status == "done":
                provider_claim_state = "completed"
            elif status == "blocked":
                provider_truths = {
                    truth
                    for raw_value in (
                        status_row.get("payload"),
                        status_row.get("last_error"),
                    )
                    if isinstance(
                        (truth := _provider_calls_performed_truth(raw_value)),
                        bool,
                    )
                }
                # A true signal wins over a conflicting/stale false signal.
                provider_claim_state = (
                    "blocked" if provider_truths == {False} else "failed"
                )
            else:
                # A generic blocked status does not prove that external I/O
                # was avoided.  Post-provider fences and legacy rows remain
                # provider failures unless the persisted payload says false.
                provider_claim_state = "failed"
            namespace["finalize_provider_execution_claim"](
                provider_task_id,
                fence,
                provider_claim_state,
            )
            return status
        except namespace["ApifyBudgetBlocked"]:
            if fence:
                namespace["finalize_provider_execution_claim"](
                    provider_task_id, fence, "blocked"
                )
            raise
        except namespace["ApifyProviderReplayBlocked"]:
            if fence:
                namespace["finalize_provider_execution_claim"](
                    provider_task_id, fence, "unknown"
                )
            raise
        except namespace["ApifyExecutionClaimBlocked"]:
            # A live owner is execution state, not a provider failure.
            raise
        except Exception as exc:
            retire_connection = worker_lock_cleanup_failed(exc)
            deferred = shared_provider_rate_deferral(exc)
            if deferred is not None:
                # The admission exception is raised before dispatch. It may
                # be wrapped by a slot-unlock error on the same broken DB.
                if deferred is not exc:
                    namespace["logger"].warning(
                        "provider admission deferred after cleanup failure | id=%s error=%s",
                        job_id, type(exc).__name__, exc_info=True,
                    )
                try:
                    if fence:
                        namespace["finalize_provider_execution_claim"](
                            provider_task_id, fence, "blocked"
                        )
                    requeue(
                        conn, job_id, str(deferred),
                        retry_delay_seconds=deferred.retry_delay_seconds,
                    )
                except Exception as persistence_error:
                    # Let the outer failure boundary retry persistence without
                    # turning this no-call event into a spent provider attempt.
                    raise deferred from persistence_error
                return "queued"
            if fence:
                namespace["finalize_provider_execution_claim"](
                    provider_task_id, fence, "failed"
                )
            raise
        finally:
            # Keep the still-usable session available for the bounded requeue
            # above, but never let an uncertain unlock return it to the loop.
            # This also runs when finalization/requeue persistence fails.
            if retire_connection:
                try:
                    retire_worker_connection(conn)
                except WorkerConnectionRetired as close_error:
                    if deferred is not None:
                        raise close_error from deferred
                    raise


__all__ = ["SharedProviderRateDeferred", "execute_claimed_job_impl", "shared_provider_rate_deferral"]
