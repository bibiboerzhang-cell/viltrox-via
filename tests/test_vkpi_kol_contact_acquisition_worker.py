from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

import pytest

from app.domains.kol import contact_acquisition_queue, contact_system


def _queue_db(*, profile_url: str = "https://youtube.com/@creator") -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            profile_url TEXT,
            bio TEXT,
            viltrox_fit_score REAL,
            raw_platform_data TEXT
        );
        CREATE TABLE vkpi_kol_pool_contacts (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            contact_type TEXT,
            contact_value TEXT,
            channel TEXT,
            normalized_value TEXT,
            verification_status TEXT DEFAULT 'observed',
            verified_at TEXT,
            invalidated_at TEXT,
            revoked_at TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE vkpi_kol_contact_evidence (
            id INTEGER PRIMARY KEY,
            contact_id INTEGER NOT NULL,
            kol_pool_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            confidence REAL,
            is_public_declared INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE vkpi_kol_contact_suppressions (
            id INTEGER PRIMARY KEY,
            brand_scope TEXT NOT NULL,
            kol_pool_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            contact_fingerprint TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE vkpi_kol_contact_acquisition_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL,
            trigger_source TEXT NOT NULL DEFAULT 'reconcile',
            reason_code TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            contactability_score REAL,
            last_reconciled_at TEXT,
            next_attempt_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO vkpi_kol_pool VALUES (1, 'youtube', ?, '', NULL, '{}')",
        (profile_url,),
    )
    db.commit()
    return db

def test_missing_suppression_hmac_fails_closed_with_backoff_and_no_pii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _queue_db()
    db.execute(
        """
        INSERT INTO vkpi_kol_pool_contacts
          (id, kol_pool_id, contact_type, contact_value, channel,
           normalized_value, verification_status, verified_at)
        VALUES (77, 1, 'email', 'hmac-secret@example.com', 'email',
                'hmac-secret@example.com', 'verified_public_business',
                '2026-08-15T01:00:00Z')
        """
    )
    db.execute(
        """
        INSERT INTO vkpi_kol_contact_evidence
          (id, contact_id, kol_pool_id, source_type, confidence, is_public_declared)
        VALUES (1, 77, 1, 'youtube_about_declared', 0.95, 1)
        """
    )
    db.commit()
    import app.domains.kol.business_contact_extract as extractor
    import app.domains.kol.contact_suppression as suppression

    monkeypatch.delenv(suppression.SUPPRESSION_HMAC_ENV, raising=False)
    monkeypatch.setattr(extractor, "extract_contacts_multi_source", lambda *_a, **_kw: [])
    monkeypatch.setattr(
        contact_system,
        "refresh_contactability",
        lambda *_a, **_kw: {"written": True, "score": 55.0},
    )
    contact_acquisition_queue.enqueue_contact_acquisition(1, conn=db)

    result = contact_acquisition_queue.reconcile_contact_acquisition(
        1, brand_scope="organization:9", conn=db
    )
    queue_row = dict(
        db.execute(
            "SELECT status, attempt_count, next_attempt_at FROM vkpi_kol_contact_acquisition_queue"
        ).fetchone()
    )

    assert result["status"] == "error"
    assert result["reason_code"] == "eligibility_gate_unavailable"
    assert queue_row["attempt_count"] == 1
    assert queue_row["next_attempt_at"]
    serialized = json.dumps(result, sort_keys=True)
    assert "hmac-secret@example.com" not in serialized
    assert suppression.SUPPRESSION_HMAC_ENV not in serialized


def test_bounded_worker_only_seeds_and_reconciles_l0(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workers.contact_acquisition_worker as worker

    monkeypatch.setattr(
        contact_acquisition_queue,
        "seed_existing_contact_acquisition_queue",
        lambda **_kw: {"status": "seeded", "queued": 3},
    )
    monkeypatch.setattr(
        contact_acquisition_queue,
        "reconcile_pending_contact_acquisition",
        lambda **_kw: {"status": "completed", "processed": 2, "state_counts": {"ready": 1, "error": 1}},
    )

    result = worker.run_once(brand_scope="organization:9", limit=10)

    assert result == {
        "status": "completed",
        "seeded": 3,
        "processed": 2,
        "state_counts": {"ready": 1, "error": 1},
        "priority_tier_counts": {},
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def test_periodic_worker_disabled_and_schema_missing_are_safe_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers.contact_acquisition_worker as worker

    monkeypatch.delenv(worker.ENABLED_ENV, raising=False)
    monkeypatch.setattr(
        worker,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("disabled cycle must not open DB")),
    )
    disabled = worker.run_configured_cycle()
    assert disabled["status"] == "disabled"
    assert disabled["provider_calls"] is False

    monkeypatch.setenv(worker.ENABLED_ENV, "1")
    monkeypatch.setenv(worker.BRAND_SCOPE_ENV, "organization:9")
    monkeypatch.setattr(worker, "release_validation_active", lambda: False)
    missing_schema = worker.run_configured_cycle(conn=sqlite3.connect(":memory:"))
    assert missing_schema["status"] == "skipped"
    assert missing_schema["reason"] == "queue_schema_unavailable"
    assert missing_schema["provider_calls"] is False


def test_periodic_worker_has_bounded_cadence_and_invokes_one_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers.contact_acquisition_worker as worker

    monkeypatch.setenv(worker.CADENCE_ENV, "5")
    assert worker.cadence_seconds() == 30
    monkeypatch.setenv(worker.CADENCE_ENV, "99999")
    assert worker.cadence_seconds() == 3600

    stop = asyncio.Event()
    calls: list[int] = []

    def one_cycle() -> dict[str, Any]:
        calls.append(1)
        stop.set()
        return {"status": "completed", "processed": 0, "seeded": 0}

    asyncio.run(worker.periodic_cycle_loop(stop, cadence=1, cycle=one_cycle))
    assert calls == [1]


def test_configured_cycles_release_bounded_db_scope_across_four_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers.contact_acquisition_worker as worker

    monkeypatch.setenv(worker.ENABLED_ENV, "1")
    monkeypatch.setenv(worker.BRAND_SCOPE_ENV, "organization:9")
    monkeypatch.setattr(worker, "release_validation_active", lambda: False)
    monkeypatch.setattr(worker, "_schema_available", lambda _db: True)
    monkeypatch.setattr(
        worker,
        "run_once",
        lambda **_kw: time.sleep(0.01) or {
            "status": "completed",
            "seeded": 0,
            "processed": 0,
            "state_counts": {},
            "priority_tier_counts": {},
            "provider_calls": False,
            "website_crawls": False,
            "messages_sent": False,
        },
    )
    lock = threading.Lock()
    state = {"entered": 0, "exited": 0, "active": 0, "max_active": 0}

    @contextmanager
    def bounded_scope():
        with lock:
            state["entered"] += 1
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            yield object()
        finally:
            with lock:
                state["active"] -= 1
                state["exited"] += 1

    monkeypatch.setattr(worker, "db_connection_sync_reusing_scope", bounded_scope)
    monkeypatch.setattr(
        worker,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("yielded scope connection must be reused")),
    )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: worker.run_configured_cycle(), range(8)))

    assert all(result["status"] == "completed" for result in results)
    assert state["entered"] == state["exited"] == 8
    assert state["active"] == 0
    assert 1 <= state["max_active"] <= 4


def test_configured_cycle_releases_scope_when_cycle_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers.contact_acquisition_worker as worker

    monkeypatch.setenv(worker.ENABLED_ENV, "1")
    monkeypatch.setenv(worker.BRAND_SCOPE_ENV, "organization:9")
    monkeypatch.setattr(worker, "release_validation_active", lambda: False)
    monkeypatch.setattr(worker, "_schema_available", lambda _db: True)
    monkeypatch.setattr(
        worker,
        "run_once",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("lease-test")),
    )
    events: list[str] = []

    @contextmanager
    def bounded_scope():
        events.append("enter")
        try:
            yield object()
        finally:
            events.append("exit")

    monkeypatch.setattr(worker, "db_connection_sync_reusing_scope", bounded_scope)
    result = worker.run_configured_cycle()

    assert result["status"] == "error"
    assert result["reason"] == "cycle_failed"
    assert events == ["enter", "exit"]
