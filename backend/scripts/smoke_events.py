#!/usr/bin/env python3
"""
scripts/smoke_events.py — Phase 1 Party Engine smoke test

验证:
    1. Email normalization (gmail whitelist works)
    2. Party creation (UUID v4)
    3. Email stitch (same party_id returned for dotted gmail variants)
    4. Identity link dedupe (calling twice is idempotent)
    5. Event write (shop.purchase / via.session_started / creator.submission_created)
    6. Party activity touch (last_activity_at updates)
    7. View v_party_activity_14d returns rows

Usage:
    cd backend
    DATABASE_URL=postgresql://user:pass@host:5432/viltrox_v2 DB_RUNTIME_BACKEND=postgres \\
        python3 scripts/smoke_events.py

    (or on a machine with the viltrox service env loaded:)
    python3 scripts/smoke_events.py

Exits 0 on success, non-zero on first failure. Prints a stepwise log.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Make `app` importable when running from backend/
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# pylint: disable=wrong-import-position
from app.db.connection import _get_pg_pool, is_postgres_runtime  # noqa: E402
from app.services.party.email_normalize import (  # noqa: E402
    email_hashes,
    normalize_email,
)
from app.services.party.event_writer import (  # noqa: E402
    emit_creator_submission_created,
    emit_shop_purchase,
    emit_via_session_started,
    write_event,
)
from app.services.party.party_service import (  # noqa: E402
    add_identity_link,
    create_party,
    get_or_create_by_email,
    resolve_party_by_link,
)


# ─── pretty output ────────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}  ✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}  ✗ {msg}{RESET}")
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"{YELLOW}  ! {msg}{RESET}")


def step(msg: str) -> None:
    print(f"\n{DIM}──{RESET} {msg}")


# ─── checks ───────────────────────────────────────────────────────────

def test_normalize() -> None:
    step("1. Email normalization")
    cases = [
        ("Foo.Bar+x@Gmail.com", "foobar@gmail.com"),
        ("foo+y@googlemail.com", "foo@gmail.com"),
        ("Foo.Bar@Outlook.com", "foo.bar@outlook.com"),    # dots preserved
        ("user@example.com", "user@example.com"),
        ("USER@EXAMPLE.COM", "user@example.com"),
        ("", ""),
    ]
    for given, expected in cases:
        got = normalize_email(given)
        if got != expected:
            fail(f"normalize_email({given!r}) = {got!r}, expected {expected!r}")
    ok("gmail rules applied to @gmail.com / @googlemail.com only")
    ok("non-gmail addresses preserve dots and '+'")

    step("  hash helper")
    h = email_hashes("Foo.Bar+x@Gmail.com")
    assert h["raw_hash"] != h["normalized_hash"], "raw + normalized should differ"
    assert h["was_normalized"] is True
    assert h["raw_domain"] == "gmail.com"
    ok(f"raw_hash={h['raw_hash'][:12]}… normalized={h['normalized_hash'][:12]}…")


def test_create_party() -> str:
    step("2. create_party()")
    party_id = create_party(
        origin_source="smoke_test",
        origin_channel="cli",
        display_name="Smoke Test Party",
    )
    if not party_id:
        fail("create_party returned None — PG not available?")
    try:
        uuid.UUID(party_id)
    except ValueError:
        fail(f"party_id not a valid UUID: {party_id}")
    ok(f"created party {party_id}")
    return party_id


def test_email_stitch() -> str:
    step("3. Email stitch — dotted gmail variants stitch to same party")
    # First call creates a party
    p1 = get_or_create_by_email(
        "smoke.test+run1@gmail.com",
        origin_source="smoke_test",
    )
    if not p1:
        fail("get_or_create_by_email returned None")

    # Second call with different dot/plus variant — should return SAME party
    p2 = get_or_create_by_email(
        "smoketest+run2@gmail.com",
        origin_source="smoke_test",
    )
    if p1 != p2:
        fail(f"gmail variants created different parties: {p1} vs {p2}")
    ok(f"both 'smoke.test+run1@gmail.com' and 'smoketest+run2@gmail.com' → {p1}")

    # Non-gmail variant with dot — should NOT stitch
    p3 = get_or_create_by_email(
        "smoke.test@outlook.com",
        origin_source="smoke_test",
    )
    p4 = get_or_create_by_email(
        "smoketest@outlook.com",
        origin_source="smoke_test",
    )
    if p3 == p4:
        fail(f"outlook dotted addresses wrongly stitched: {p3}")
    ok(f"outlook addresses stayed separate: {p3[:8]}… vs {p4[:8]}…")

    return p1


def test_identity_link_dedup(party_id: str) -> None:
    step("4. add_identity_link() idempotency")
    first = add_identity_link(
        party_id=party_id,
        link_type="shopify_customer_id",
        link_value_hash="a" * 64,
        link_value_preview="test-customer",
        source="smoke_test",
    )
    second = add_identity_link(
        party_id=party_id,
        link_type="shopify_customer_id",
        link_value_hash="a" * 64,
        link_value_preview="test-customer",
        source="smoke_test",
    )
    if not first:
        fail("first add_identity_link returned False (should be True for new link)")
    if second:
        fail("second add_identity_link returned True (should be False / no-op)")
    ok("first call inserted, second call no-op (idempotent)")

    resolved = resolve_party_by_link("shopify_customer_id", "a" * 64)
    if resolved != party_id:
        fail(f"resolve_party_by_link returned {resolved}, expected {party_id}")
    ok(f"resolve_party_by_link() round-tripped the party_id")


def test_event_writes(party_id: str) -> None:
    step("5. Event writes (3 emitters)")

    e1 = emit_shop_purchase(
        party_id=party_id,
        order_id="SMOKE-001",
        total=299.99,
        currency="USD",
        line_items=[{"sku": "AF-50MM-SONY", "qty": 1}],
        ref_code="CREATOR-TEST",
        utm_source="ig",
        session_id="smoke-session-1",
    )
    if not e1:
        fail("emit_shop_purchase returned None")
    ok(f"shop.purchase emitted event_id={e1[:8]}…")

    e2 = emit_via_session_started(
        party_id=party_id,
        via_session_id="smoke-via-session-1",
        signed_device_id="smoke-device",
        client_fingerprint="smoke-fingerprint",
        entry_source="upload",
    )
    if not e2:
        fail("emit_via_session_started returned None")
    ok(f"via.session_started emitted event_id={e2[:8]}…")

    e3 = emit_creator_submission_created(
        party_id=party_id,
        submission_id=12345,
        platform="YouTube",
        url="https://youtube.com/watch?v=smoke",
        creator_code="V_000999",
        handle="smoketest",
    )
    if not e3:
        fail("emit_creator_submission_created returned None")
    ok(f"creator.submission_created emitted event_id={e3[:8]}…")

    # Also anonymous event (party_id=None)
    e4 = write_event(
        event_type="via.message_sent",
        event_source="via_runtime",
        party_id=None,
        payload={"text": "anonymous message"},
    )
    if not e4:
        fail("anonymous write_event returned None")
    ok(f"anonymous event accepted (party_id=None)")


def test_party_activity_touch(party_id: str) -> None:
    step("6. last_activity_at touched after events")
    pool = _get_pg_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_activity_at FROM parties WHERE party_id = %s",
                (party_id,),
            )
            row = cur.fetchone()
    if not row or not row[0]:
        fail("last_activity_at is NULL after emitting events")
    ok(f"last_activity_at = {row[0]}")


def test_view(party_id: str) -> None:
    step("7. v_party_activity_14d view returns this party")
    pool = _get_pg_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT events_14d, purchases_14d, via_messages_14d, submissions_14d
                FROM v_party_activity_14d
                WHERE party_id = %s
                """,
                (party_id,),
            )
            row = cur.fetchone()
    if not row:
        fail("view v_party_activity_14d did not return our test party")
    events_14d, purchases, via_msgs, subs = row
    ok(f"events_14d={events_14d}  purchases={purchases}  via_msgs={via_msgs}  submissions={subs}")
    if events_14d < 3:
        warn(f"expected at least 3 events in 14d window, got {events_14d}")


def cleanup(party_ids: list[str]) -> None:
    step("cleanup (deleting smoke test parties + cascaded links/events)")
    pool = _get_pg_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for pid in party_ids:
                # events has ON DELETE SET NULL (preserves event), so clear them too
                cur.execute("DELETE FROM events WHERE party_id = %s", (pid,))
                cur.execute("DELETE FROM parties WHERE party_id = %s", (pid,))
        conn.commit()
    ok(f"deleted {len(party_ids)} test parties")


# ─── main ─────────────────────────────────────────────────────────────

def main() -> int:
    print(f"{DIM}Viltrox V-OS Middleware — Phase 1 Smoke Test{RESET}")
    print(f"{DIM}=============================================={RESET}")

    if not is_postgres_runtime():
        fail(
            "PG runtime not detected.\n"
            "    Set DATABASE_URL and DB_RUNTIME_BACKEND=postgres and ensure psycopg is installed."
        )

    ok("Postgres runtime detected")

    test_normalize()

    p_fresh = test_create_party()
    p_stitch = test_email_stitch()
    test_identity_link_dedup(p_fresh)
    test_event_writes(p_fresh)
    test_party_activity_touch(p_fresh)
    test_view(p_fresh)

    # Collect all smoke parties that may have been created
    pool = _get_pg_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT party_id FROM parties WHERE origin_source = 'smoke_test'"
            )
            to_clean = [str(r[0]) for r in cur.fetchall()]
    cleanup(to_clean)

    print(f"\n{GREEN}✓ Phase 1 smoke test PASSED{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
