"""Real-Postgres concurrency / race-condition tests (PG4).

These drive the production domain functions from multiple *real* Postgres
connections in parallel (threads → thread-local pooled connections) to prove
the claim / state-transition / verdict write paths hold their invariants under
a genuine race — no lost updates, no double-write, no double-claim.

Every test is marked ``pg`` and is skipped (never failed) when no live database
is reachable. Each test creates its own uniquely-keyed scratch rows against the
real tables, drives the race, asserts, and deletes its rows in a ``finally``
using a dedicated autocommit admin connection. Nothing leaks; re-runnable.

Determinism: where the racy window is a Python-level *read-then-write* gap
(check-then-insert, select-then-insert), we monkeypatch the single function the
product code happens to call *inside* that gap with a ``threading.Barrier`` so
both threads are forced to sit in the window simultaneously — turning a
probabilistic race into a deterministic one. Where the product code closes the
window atomically (``UPDATE ... WHERE status=? RETURNING`` with a rowcount
guard, or ``FOR UPDATE SKIP LOCKED``) the Postgres row lock serializes the
threads on its own and only a start-barrier is used to encourage overlap.

The barriers are the *only* thing that changes; the SQL executed is 100% the
production path.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

import pytest

pytestmark = pytest.mark.pg


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------
@pytest.fixture()
def require_pg_runtime() -> None:
    """Skip (not fail) unless the app runtime itself is Postgres.

    The domain functions call ``get_conn()``, which returns a *pooled Postgres*
    connection only when ``is_postgres_runtime()`` is True (DATABASE_URL set +
    DB_RUNTIME_BACKEND=postgres). Without the runtime env the same call would
    silently build a throw-away SQLite connection and the whole test would be a
    false negative — so we refuse to run rather than test the wrong backend.
    """
    from app.db.connection import is_postgres_runtime

    if not is_postgres_runtime():
        pytest.skip(
            "app runtime is not postgres (source scripts/runtime_env.sh so "
            "DATABASE_URL + DB_RUNTIME_BACKEND=postgres are exported)"
        )


def _admin(pg_dsn: str) -> Any:
    """A dedicated raw autocommit psycopg connection for setup/verify/cleanup.

    Kept entirely separate from the pooled connections the domain code uses, so
    our reads always see whatever the racing threads have committed.
    """
    import psycopg

    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    conn.autocommit = True
    return conn


def _release_thread_conn() -> None:
    """Return the calling thread's pooled connection to the pool.

    ``get_conn()`` caches one connection per thread in ``_db_local``; worker
    threads are short-lived so we hand it back explicitly to avoid slowly
    draining the pool across many test iterations.
    """
    import app.db.connection as dbconn

    conn = getattr(dbconn._db_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        dbconn._db_local.conn = None


def _run_parallel(fns: list[Callable[[], Any]], *, join_timeout: float = 30.0):
    """Run each callable in its own thread; return (results, errors) by index.

    Each worker returns its pooled connection to the pool on the way out.
    """
    n = len(fns)
    results: list[Any] = [None] * n
    errors: list[BaseException | None] = [None] * n

    def _wrap(idx: int, fn: Callable[[], Any]) -> None:
        try:
            results[idx] = fn()
        except BaseException as exc:  # noqa: BLE001 — we want the raw failure recorded
            errors[idx] = exc
        finally:
            _release_thread_conn()

    threads = [threading.Thread(target=_wrap, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=join_timeout)
    assert not any(t.is_alive() for t in threads), "worker thread deadlocked / did not finish"
    return results, errors


def _barrier_wrap(orig: Callable[..., Any], barrier: threading.Barrier) -> Callable[..., Any]:
    """Wrap ``orig`` so every call parks on ``barrier`` before delegating.

    Placed on the one function the product code calls *between* its critical
    read and its write, this pins both racing threads inside the window at once.
    A broken/timed-out barrier just falls through (no hang, no false pass).
    """

    def _patched(*args: Any, **kwargs: Any) -> Any:
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        return orig(*args, **kwargs)

    return _patched


# ===========================================================================
# (1) reply_queue.mark — two people mark the same row → exactly one wins
# ===========================================================================
def test_reply_queue_mark_concurrent_exactly_one_success(
    require_pg_runtime: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent ``mark()`` on one pending row must yield exactly one
    success and one ``status_conflict`` — never two successes (lost update)."""
    from app.domains.comments import reply_queue

    admin = _admin(pg_dsn)
    ext = "pgtest-mark-" + uuid.uuid4().hex
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO vkpi_reply_queue "
            "(platform, comment_external_id, comment_text, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'pending', NOW(), NOW()) RETURNING id",
            ("pgtest", ext, "how much is this lens?"),
        )
        rid = int(cur.fetchone()[0])

    try:
        # _now_iso() is the one call mark() makes *between* its status SELECT and
        # its guarded UPDATE — park both threads there so both read 'pending'.
        barrier = threading.Barrier(2)
        monkeypatch.setattr(reply_queue, "_now_iso", _barrier_wrap(reply_queue._now_iso, barrier))

        results, errors = _run_parallel(
            [lambda: reply_queue.mark(rid, "replied"), lambda: reply_queue.mark(rid, "dismissed")]
        )
        assert errors == [None, None], f"unexpected exception: {errors}"

        oks = [r for r in results if isinstance(r, dict) and r.get("ok")]
        conflicts = [r for r in results if isinstance(r, dict) and not r.get("ok")]
        with admin.cursor() as cur:
            cur.execute("SELECT status FROM vkpi_reply_queue WHERE id=%s", (rid,))
            final = cur.fetchone()[0]

        assert len(oks) == 1, f"expected exactly ONE success, got results={results}"
        assert len(conflicts) == 1 and conflicts[0].get("reason") == "status_conflict", results
        # The persisted status must equal the winner's target — the loser must
        # NOT have overwritten it.
        assert final == oks[0]["status"], f"db status {final!r} != winner {oks[0]!r}"
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM vkpi_reply_queue WHERE id=%s", (rid,))
        admin.close()


# ===========================================================================
# (2a) reply_queue.draft_reply — two *different* staff → exactly one drafts
# ===========================================================================
def test_reply_queue_draft_distinct_staff_single_claim(
    require_pg_runtime: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two staff drafting the same pending row concurrently: exactly one claims
    and burns the LLM once; the other gets ``claimed_by_other`` and burns
    nothing."""
    from app.domains.comments import reply_queue
    from app.domains.costs import budget_guard
    from app.platform import llm_gateway

    invoke_calls = _CountingInvoke()
    monkeypatch.setattr(llm_gateway, "invoke", invoke_calls)
    monkeypatch.setattr(budget_guard, "check_budget", lambda *a, **k: True)

    admin = _admin(pg_dsn)
    ext = "pgtest-draftA-" + uuid.uuid4().hex
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO vkpi_reply_queue "
            "(platform, comment_external_id, comment_text, intent_tag, lang, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'price', 'en', 'pending', NOW(), NOW()) RETURNING id",
            ("pgtest", ext, "where can i buy this?"),
        )
        rid = int(cur.fetchone()[0])

    try:
        start = threading.Barrier(2)

        def draft_as(staff_id: int) -> Callable[[], Any]:
            def _f() -> Any:
                start.wait(timeout=10)
                return reply_queue.draft_reply(rid, staff={"id": staff_id})

            return _f

        results, errors = _run_parallel([draft_as(7676), draft_as(7677)])
        assert errors == [None, None], f"unexpected exception: {errors}"

        oks = [r for r in results if isinstance(r, dict) and r.get("ok")]
        losers = [r for r in results if isinstance(r, dict) and not r.get("ok")]

        assert len(oks) == 1, f"expected exactly ONE draft, got {results}"
        assert len(losers) == 1 and losers[0].get("reason") == "claimed_by_other", results
        assert invoke_calls.count == 1, (
            f"expected exactly ONE LLM invocation across two racers, got {invoke_calls.count}"
        )
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM vkpi_reply_queue WHERE id=%s", (rid,))
        admin.close()


# ===========================================================================
# (2b) reply_queue.draft_reply — SAME staff twice concurrently
#      Probes whether the claim gate dedupes a same-actor double-submit.
# ===========================================================================
def test_reply_queue_draft_same_staff_double_burn(
    require_pg_runtime: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same staff double-submits draft on one row (double-click / retry).

    The claim gate admits ``claimed_by IS NULL OR claimed_by = <me>``, so the
    second concurrent request from the *same* actor re-passes the gate. This
    asserts the intended invariant (LLM invoked at most once per comment). If it
    fails with count==2 the gate does not protect against same-actor concurrent
    double-burn — reported as a bug, not silently tolerated."""
    from app.domains.comments import reply_queue
    from app.domains.costs import budget_guard
    from app.platform import llm_gateway

    invoke_calls = _CountingInvoke()
    monkeypatch.setattr(llm_gateway, "invoke", invoke_calls)
    monkeypatch.setattr(budget_guard, "check_budget", lambda *a, **k: True)

    admin = _admin(pg_dsn)
    ext = "pgtest-draftB-" + uuid.uuid4().hex
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO vkpi_reply_queue "
            "(platform, comment_external_id, comment_text, intent_tag, lang, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'price', 'en', 'pending', NOW(), NOW()) RETURNING id",
            ("pgtest", ext, "what is the price?"),
        )
        rid = int(cur.fetchone()[0])

    try:
        start = threading.Barrier(2)

        def draft() -> Any:
            start.wait(timeout=10)
            return reply_queue.draft_reply(rid, staff={"id": 7676})

        results, errors = _run_parallel([draft, draft])
        assert errors == [None, None], f"unexpected exception: {errors}"
        oks = [r for r in results if isinstance(r, dict) and r.get("ok")]

        # Invariant the claim gate is supposed to enforce ("绝不并发重复烧 LLM").
        assert invoke_calls.count <= 1, (
            f"same-staff concurrent draft double-burned the LLM: invoked "
            f"{invoke_calls.count} times, drafted={len(oks)}x — the claim gate "
            f"admits re-entry by the same actor so a double-click / retry issues "
            f"two LLM calls (and two 'ok' drafts) for one comment."
        )
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM vkpi_reply_queue WHERE id=%s", (rid,))
        admin.close()


# ===========================================================================
# (3a) action inbox — concurrent approve vs dismiss from 'suggested'
# ===========================================================================
def test_action_inbox_approve_dismiss_race(
    require_pg_runtime: None, pg_dsn: str
) -> None:
    """One suggested action, two simultaneous transitions (approve + dismiss).
    Exactly one must win; the loser gets ``illegal_state_transition`` and the
    persisted status equals the winner's target."""
    from app.domains.actions import inbox

    owner = {"id": 40, "is_owner": 1, "role": "admin"}
    admin = _admin(pg_dsn)
    dedupe = "pgtest-act-" + uuid.uuid4().hex
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO vkpi_action_inbox (dedupe_key, category, title, status, created_at, updated_at) "
            "VALUES (%s, 'test_probe', 'concurrency probe', 'suggested', NOW(), NOW()) RETURNING id",
            (dedupe,),
        )
        aid = int(cur.fetchone()[0])

    try:
        start = threading.Barrier(2)

        def approve() -> Any:
            start.wait(timeout=10)
            return inbox.approve_action(aid, staff=owner, reason="race")

        def dismiss() -> Any:
            start.wait(timeout=10)
            return inbox.dismiss_action(aid, staff=owner)

        results, errors = _run_parallel([approve, dismiss])
        assert errors == [None, None], f"unexpected exception: {errors}"

        oks = [r for r in results if isinstance(r, dict) and r.get("ok")]
        losers = [r for r in results if isinstance(r, dict) and not r.get("ok")]
        with admin.cursor() as cur:
            cur.execute("SELECT status FROM vkpi_action_inbox WHERE id=%s", (aid,))
            final = cur.fetchone()[0]

        assert len(oks) == 1, f"expected exactly ONE winning transition, got {results}"
        assert len(losers) == 1 and losers[0].get("reason") == "illegal_state_transition", results
        assert final == oks[0]["status"], f"db status {final!r} != winner {oks[0]!r}"
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM vkpi_action_execution_ledger WHERE action_id=%s", (aid,))
            cur.execute("DELETE FROM vkpi_action_inbox WHERE id=%s", (aid,))
        admin.close()


# ===========================================================================
# (3b) action inbox — concurrent mark-done must not double-write the ledger
# ===========================================================================
def test_action_inbox_mark_done_no_double_ledger(
    require_pg_runtime: None, pg_dsn: str
) -> None:
    """Two concurrent ``mark_done_action`` on one approved action: exactly one
    transitions it to executed and writes exactly one manual-execution ledger
    row (never two)."""
    from app.domains.actions import inbox

    owner = {"id": 40, "is_owner": 1, "role": "admin"}
    admin = _admin(pg_dsn)
    dedupe = "pgtest-md-" + uuid.uuid4().hex
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO vkpi_action_inbox (dedupe_key, category, title, status, created_at, updated_at) "
            "VALUES (%s, 'test_probe', 'markdone probe', 'approved', NOW(), NOW()) RETURNING id",
            (dedupe,),
        )
        aid = int(cur.fetchone()[0])

    try:
        start = threading.Barrier(2)

        def mark_done() -> Any:
            start.wait(timeout=10)
            return inbox.mark_done_action(aid, staff=owner, note="race")

        results, errors = _run_parallel([mark_done, mark_done])
        assert errors == [None, None], f"unexpected exception: {errors}"

        oks = [r for r in results if isinstance(r, dict) and r.get("ok")]
        with admin.cursor() as cur:
            cur.execute("SELECT status FROM vkpi_action_inbox WHERE id=%s", (aid,))
            final = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM vkpi_action_execution_ledger "
                "WHERE action_id=%s AND endpoint='manual:mark-done'",
                (aid,),
            )
            ledger_n = int(cur.fetchone()[0])

        assert len(oks) == 1, f"expected exactly ONE mark-done to succeed, got {results}"
        assert final == "executed", f"db status {final!r} != 'executed'"
        assert ledger_n == 1, f"expected exactly ONE ledger row, found {ledger_n} (double-write)"
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM vkpi_action_execution_ledger WHERE action_id=%s", (aid,))
            cur.execute("DELETE FROM vkpi_action_inbox WHERE id=%s", (aid,))
        admin.close()


# ===========================================================================
# (4) KOL claim — two staff claim the same KOL concurrently → not double-claim
# ===========================================================================
def test_kol_claim_concurrent_no_double_claim(
    require_pg_runtime: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two staff claim one unclaimed KOL simultaneously.

    Invariant: at most ONE active claim row survives. ``claim()`` guards with a
    check-then-insert (``SELECT ... status='active'`` then ``INSERT``), which is
    not atomic; the partial unique index ``idx_vkpi_kol_claims_one_active`` is
    the real backstop. We also record *how* the loser fails — a graceful
    ``ValueError('kol already claimed')`` would be correct; a raw psycopg
    IntegrityError leaking out (poisoned txn → 500) is a defect worth flagging.
    """
    from app.domains.kol import claim_lifecycle

    admin = _admin(pg_dsn)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO kols (channel_name, platform, created_at, updated_at) "
            "VALUES (%s, 'youtube', NOW(), NOW()) RETURNING id",
            ("pgtest-kol-" + uuid.uuid4().hex,),
        )
        kol_id = int(cur.fetchone()[0])

    try:
        # utcnow() is called once inside claim() between the "already active?"
        # SELECT and the INSERT — park both threads there so both pass the check
        # and then race the INSERT into the partial unique index.
        barrier = threading.Barrier(2)
        monkeypatch.setattr(
            claim_lifecycle, "utcnow", _barrier_wrap(claim_lifecycle.utcnow, barrier)
        )

        def claim_as(staff_id: int) -> Callable[[], Any]:
            def _f() -> Any:
                return claim_lifecycle.claim(kol_id, staff={"id": staff_id})

            return _f

        results, errors = _run_parallel([claim_as(7676), claim_as(7677)])

        successes = [r for r in results if isinstance(r, dict) and r.get("claim")]
        raised = [e for e in errors if e is not None]

        with admin.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM vkpi_kol_claims WHERE kol_id=%s AND status='active'",
                (kol_id,),
            )
            active_n = int(cur.fetchone()[0])

        # PRIMARY invariant — the DB partial unique index must prevent 2 claims.
        assert active_n == 1, f"double-claim: {active_n} active claim rows for one KOL"
        assert len(successes) == 1, f"expected exactly ONE successful claim, got {results}"

        # SECONDARY finding — surface how the loser failed for the report.
        if raised:
            loser = raised[0]
            graceful = isinstance(loser, ValueError) and "already claimed" in str(loser).lower()
            assert graceful, (
                "concurrent claim loser did NOT get the graceful "
                "ValueError('kol already claimed'); it leaked a raw DB error "
                f"({type(loser).__module__}.{type(loser).__name__}: {loser}). "
                "The SELECT-then-INSERT guard is not concurrency-safe; only the "
                "partial unique index saves data integrity, while the caller sees "
                "an unhandled IntegrityError (poisoned transaction / HTTP 500)."
            )
    finally:
        with admin.cursor() as cur:
            cur.execute(
                "DELETE FROM vkpi_business_audit_logs WHERE target_type='kol' AND target_id=%s",
                (str(kol_id),),
            )
            cur.execute("DELETE FROM vkpi_kol_claims WHERE kol_id=%s", (kol_id,))
            cur.execute("DELETE FROM kols WHERE id=%s", (kol_id,))
        admin.close()


# ===========================================================================
# (5) bet verdict — two record_verdict on the same bet → exactly one finalized
# ===========================================================================
def test_bet_verdict_concurrent_single_finalize(
    require_pg_runtime: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two humans finalize the same bet (via inbox_id) concurrently.

    ``record_verdict(inbox_id=…)`` does ``SELECT outcomes WHERE action_inbox_id``
    then, finding none open, ``INSERT`` a finalized outcome — with no unique
    constraint on ``action_inbox_id``. Invariant: exactly ONE finalized outcome
    row per bet. If two survive (one 'validated', one 'failed') the ledger is
    doubly and contradictorily finalized — a double-write race."""
    from app.domains.market_brain import verdict_flow

    admin = _admin(pg_dsn)
    dedupe = "pgtest-bet-" + uuid.uuid4().hex
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO vkpi_action_inbox "
            "(dedupe_key, category, title, status, payload_json, created_at, updated_at) "
            "VALUES (%s, 'gtm_bet', 'bet probe', 'approved', '{}'::jsonb, NOW(), NOW()) RETURNING id",
            (dedupe,),
        )
        bet_id = int(cur.fetchone()[0])

    try:
        # _bet_context() is the only call between the "any existing outcome?"
        # SELECT and the INSERT — park both threads there. Neither has inserted
        # yet, so both are guaranteed to have seen zero outcomes → both insert.
        barrier = threading.Barrier(2)
        monkeypatch.setattr(
            verdict_flow, "_bet_context", _barrier_wrap(verdict_flow._bet_context, barrier)
        )

        def verdict(decision: str) -> Callable[[], Any]:
            def _f() -> Any:
                return verdict_flow.record_verdict(
                    inbox_id=bet_id, decision=decision, lesson="race", staff={"id": 7676}
                )

            return _f

        results, errors = _run_parallel([verdict("validated"), verdict("failed")])
        assert errors == [None, None], f"unexpected exception: {errors}"

        with admin.cursor() as cur:
            cur.execute(
                "SELECT id, decision FROM vkpi_gtm_outcomes WHERE action_inbox_id=%s ORDER BY id",
                (bet_id,),
            )
            rows = cur.fetchall()

        finalized = [r for r in results if isinstance(r, dict) and r.get("finalized")]
        assert len(rows) == 1, (
            f"double-finalize: {len(rows)} outcome rows for one bet "
            f"({[(r[0], r[1]) for r in rows]}); record_verdict(inbox_id) does a "
            f"non-atomic SELECT-then-INSERT with no unique constraint on "
            f"action_inbox_id, so two concurrent verdicts each insert a finalized "
            f"row. finalized_results={len(finalized)}"
        )
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM vkpi_gtm_outcomes WHERE action_inbox_id=%s", (bet_id,))
            cur.execute(
                "DELETE FROM vkpi_action_inbox WHERE dedupe_key IN (%s, %s)",
                (dedupe, f"gtm_verdict:{bet_id}"),
            )
        admin.close()


# ===========================================================================
# (6a) worker claim — FOR UPDATE SKIP LOCKED skips a row held by another conn
# ===========================================================================
def test_worker_claim_skip_locked_holds_lock(
    require_pg_runtime: None, pg_dsn: str
) -> None:
    """The worker's real claim SELECT, scoped to one queued job: a second
    connection running the same SELECT while the first holds the row lock must
    skip it (return nothing) rather than block or double-claim."""
    import psycopg

    from app.workers import apify_jobs_worker

    assert "FOR UPDATE SKIP LOCKED" in apify_jobs_worker.CLAIM_SELECT_SQL

    admin = _admin(pg_dsn)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at) "
            "VALUES ('pgtest_concurrency_probe', %s, 'queued', NOW(), NOW()) RETURNING id",
            ("{}",),
        )
        job_id = int(cur.fetchone()[0])

    # Scope the production claim SELECT to our one job so no real queued work is
    # ever touched by this test.
    scoped_sql = apify_jobs_worker.CLAIM_SELECT_SQL.replace(
        "WHERE status = 'queued'", f"WHERE id = {job_id} AND status = 'queued'"
    )

    c1 = psycopg.connect(pg_dsn, connect_timeout=5)
    c2 = psycopg.connect(pg_dsn, connect_timeout=5)
    c1.autocommit = False
    c2.autocommit = False
    try:
        cur1 = c1.cursor()
        cur1.execute(scoped_sql)
        row1 = cur1.fetchone()  # first conn locks the row

        cur2 = c2.cursor()
        cur2.execute(scoped_sql)
        row2 = cur2.fetchone()  # second conn must skip the locked row

        assert row1 is not None and int(row1[0]) == job_id, f"first claim failed: {row1}"
        assert row2 is None, f"second conn did NOT skip the locked row: {row2}"
    finally:
        c1.rollback()
        c2.rollback()
        c1.close()
        c2.close()
        with admin.cursor() as cur:
            cur.execute("DELETE FROM apify_jobs WHERE id=%s", (job_id,))
        admin.close()


# ===========================================================================
# (6b) worker _claim_job — two concurrent claims of one job → exactly one wins
# ===========================================================================
def test_worker_claim_job_no_double_claim(
    require_pg_runtime: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the real ``_claim_job`` from two connections concurrently, scoped
    to one synthetic job. Exactly one gets the job (status → running, lease
    stamped); the other returns None."""
    import psycopg

    from app.workers import apify_jobs_worker

    admin = _admin(pg_dsn)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at) "
            "VALUES ('pgtest_concurrency_probe', %s, 'queued', NOW(), NOW()) RETURNING id",
            ("{}",),
        )
        job_id = int(cur.fetchone()[0])

    # Scope the module-level claim SELECT to our job only, so _claim_job can
    # never grab a real queued job.
    scoped_sql = apify_jobs_worker.CLAIM_SELECT_SQL.replace(
        "WHERE status = 'queued'", f"WHERE id = {job_id} AND status = 'queued'"
    )
    monkeypatch.setattr(apify_jobs_worker, "CLAIM_SELECT_SQL", scoped_sql)

    conns: list[Any] = []
    try:
        start = threading.Barrier(2)

        def claim() -> Any:
            conn = psycopg.connect(pg_dsn, connect_timeout=5)
            conns.append(conn)
            start.wait(timeout=10)
            return apify_jobs_worker._claim_job(conn)

        results, errors = _run_parallel([claim, claim])
        assert errors == [None, None], f"unexpected exception: {errors}"

        claimed = [r for r in results if isinstance(r, dict) and r]
        nulls = [r for r in results if r is None]
        with admin.cursor() as cur:
            cur.execute("SELECT status, lease_owner FROM apify_jobs WHERE id=%s", (job_id,))
            status, lease_owner = cur.fetchone()

        assert len(claimed) == 1, f"expected exactly ONE claim, got {results}"
        assert len(nulls) == 1, f"expected the other claim to return None, got {results}"
        assert int(claimed[0]["id"]) == job_id
        assert status == "running", f"claimed job status {status!r} != 'running'"
        assert lease_owner, "claimed job has no lease_owner stamped"
    finally:
        for conn in conns:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        with admin.cursor() as cur:
            cur.execute("DELETE FROM apify_jobs WHERE id=%s", (job_id,))
        admin.close()


# ---------------------------------------------------------------------------
# small helper — thread-safe LLM-invocation counter used by the draft tests
# ---------------------------------------------------------------------------
class _CountingInvoke:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.count += 1
        return {"text": "TEST DRAFT REPLY", "provider": "test"}
