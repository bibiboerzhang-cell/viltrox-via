"""Cancellation preserves unknown provider outcome and never grants a retry."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from app.workers.apify_jobs_worker_execution import execute_claimed_job_impl


@pytest.mark.parametrize("finalization_fails", [False, True])
def test_cancelled_claim_is_unknown_and_original_cancellation_survives(finalization_fails):
    cancellation = asyncio.CancelledError("synthetic shutdown")
    calls = []

    def process(*_):
        raise cancellation

    def finalize(*args):
        calls.append(args)
        if finalization_fails:
            raise RuntimeError("synthetic persistence unavailable")
        return True

    namespace = {
        "release_validation_active": lambda: False,
        "_requeue_job": Mock(side_effect=AssertionError("cancellation cannot requeue paid work")),
        "db_connection_sync_scope": nullcontext,
        "acquire_provider_execution_claim": lambda *_args, **_kwargs: 17,
        "STALE_RECLAIM_SECONDS": 300,
        "apify_execution_context": lambda *_: nullcontext(),
        "_running_job_heartbeat": lambda *_: nullcontext(),
        "_process_claimed_job": process,
        "finalize_provider_execution_claim": finalize,
        "ApifyBudgetBlocked": type("BudgetBlocked", (Exception,), {}),
        "ApifyProviderReplayBlocked": type("ReplayBlocked", (Exception,), {}),
        "ApifyExecutionClaimBlocked": type("ClaimBlocked", (Exception,), {}),
        "logger": Mock(),
    }
    with pytest.raises(asyncio.CancelledError) as caught:
        execute_claimed_job_impl(object(), {"id": 12, "lease_owner": "test-owner"}, namespace)
    assert caught.value is cancellation
    assert calls == [("apify-job:12", 17, "unknown")]
    namespace["_requeue_job"].assert_not_called()
    assert namespace["logger"].warning.call_count == int(finalization_fails)


def test_cancellation_before_claim_receipt_never_guesses_a_fence():
    cancellation = asyncio.CancelledError("claim outcome unconfirmed")

    def acquire(*_args, **_kwargs):
        raise cancellation

    finalize, requeue = Mock(), Mock()
    namespace = {
        "release_validation_active": lambda: False,
        "_requeue_job": requeue, "db_connection_sync_scope": nullcontext,
        "acquire_provider_execution_claim": acquire, "STALE_RECLAIM_SECONDS": 300,
        "finalize_provider_execution_claim": finalize,
        "ApifyBudgetBlocked": type("BudgetBlocked", (Exception,), {}),
        "ApifyProviderReplayBlocked": type("ReplayBlocked", (Exception,), {}),
        "ApifyExecutionClaimBlocked": type("ClaimBlocked", (Exception,), {}),
        "logger": Mock(),
    }
    with pytest.raises(asyncio.CancelledError) as caught:
        execute_claimed_job_impl(object(), {"id": 12, "lease_owner": "test-owner"}, namespace)
    assert caught.value is cancellation
    finalize.assert_not_called()
    requeue.assert_not_called()
