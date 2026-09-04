"""Claimed-job execution boundary extracted from the Apify worker entrypoint.

The caller supplies its live namespace so existing operational monkeypatches
and unit tests continue to control the exact same provider and DB boundaries.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import psycopg


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
        except Exception:
            if fence:
                namespace["finalize_provider_execution_claim"](
                    provider_task_id, fence, "failed"
                )
            raise


__all__ = ["execute_claimed_job_impl"]
