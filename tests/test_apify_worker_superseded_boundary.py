"""A superseded search exits without touching the replacement attempt."""
from contextlib import contextmanager, nullcontext
from unittest.mock import Mock

import pytest

from app.domains.kol.search_execution_fence import SearchExecutionSuperseded
from app.workers.apify_jobs_worker_execution import execute_claimed_job_impl


def boundary(failure):
    events = []

    @contextmanager
    def scope():
        events.append("scope-enter")
        try:
            yield
        finally:
            events.append("scope-exit")

    @contextmanager
    def heartbeat(*_args):
        events.append("heartbeat-enter")
        try:
            yield
        finally:
            events.append("heartbeat-exit")

    def process(*_args):
        events.append("process")
        raise failure

    conn = Mock()
    conn.cursor.side_effect = AssertionError("old execution must not read the replacement job")
    namespace = {
        "release_validation_active": lambda: False,
        "_requeue_job": Mock(), "db_connection_sync_scope": scope,
        "acquire_provider_execution_claim": Mock(return_value=17),
        "STALE_RECLAIM_SECONDS": 300,
        "apify_execution_context": lambda *_args: nullcontext(),
        "_running_job_heartbeat": heartbeat,
        "_process_claimed_job": process,
        "_sync_search_session_job": Mock(),
        "finalize_provider_execution_claim": Mock(),
        "ApifyBudgetBlocked": type("BudgetBlocked", (Exception,), {}),
        "ApifyProviderReplayBlocked": type("ReplayBlocked", (Exception,), {}),
        "ApifyExecutionClaimBlocked": type("ClaimBlocked", (Exception,), {}),
        "logger": Mock(),
    }
    job = {"id": 12, "lease_owner": "old-owner", "job_type": "smart_search_profile_advance"}
    return conn, job, namespace, events


def test_typed_superseded_stops_before_job_read_sync_finalize_or_requeue():
    conn, job, namespace, events = boundary(SearchExecutionSuperseded("search_execution_not_current"))
    assert execute_claimed_job_impl(conn, job, namespace) == "superseded"
    conn.cursor.assert_not_called()
    namespace["_sync_search_session_job"].assert_not_called()
    namespace["finalize_provider_execution_claim"].assert_not_called()
    namespace["_requeue_job"].assert_not_called()
    assert events == ["scope-enter", "heartbeat-enter", "process", "heartbeat-exit", "scope-exit"]


@pytest.mark.parametrize("message", ["ordinary failure", "search_execution_not_current"])
def test_plain_value_error_keeps_existing_failure_flow_even_with_same_message(message):
    failure = ValueError(message)
    conn, job, namespace, events = boundary(failure)
    with pytest.raises(ValueError) as caught:
        execute_claimed_job_impl(conn, job, namespace)
    assert caught.value is failure
    namespace["finalize_provider_execution_claim"].assert_called_once_with("apify-job:12", 17, "failed")
    namespace["_sync_search_session_job"].assert_not_called()
    namespace["_requeue_job"].assert_not_called()
    conn.cursor.assert_not_called()
    assert events[-2:] == ["heartbeat-exit", "scope-exit"]
