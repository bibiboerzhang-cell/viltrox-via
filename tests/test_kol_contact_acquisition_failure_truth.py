from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app.domains.kol import contact_acquisition_queue


def _queue_db() -> sqlite3.Connection:
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
            verification_status TEXT,
            verified_at TEXT,
            invalidated_at TEXT,
            revoked_at TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
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
        INSERT INTO vkpi_kol_pool
          (id, platform, profile_url, bio, viltrox_fit_score, raw_platform_data)
        VALUES (1, 'youtube', 'https://youtube.com/@creator', '', NULL, '{}');
        """
    )
    db.commit()
    contact_acquisition_queue.enqueue_contact_acquisition(1, conn=db)
    return db


def _force_extractor_failure(monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
    import app.domains.kol.business_contact_extract as extractor

    monkeypatch.setattr(
        extractor,
        "extract_contacts_multi_source",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError(secret)),
    )


def test_rollback_failure_is_value_free_and_not_durable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _queue_db()
    secret = "dummy-contact@example.invalid"

    class RollbackFailingConnection:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return db.execute(*args, **kwargs)

        def commit(self) -> None:
            db.commit()

        def rollback(self) -> None:
            raise RuntimeError(secret)

    _force_extractor_failure(monkeypatch, secret)
    queue_updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        contact_acquisition_queue,
        "_queue_update",
        lambda *_a, **kwargs: queue_updates.append(kwargs),
    )
    result = contact_acquisition_queue.reconcile_contact_acquisition(
        1,
        brand_scope="organization:9",
        conn=RollbackFailingConnection(),
    )

    assert result["status"] == "error"
    assert result["durable_state_written"] is False
    assert queue_updates == []
    assert secret not in caplog.text


def test_pending_cycle_aborts_when_error_state_was_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _queue_db()
    monkeypatch.setattr(
        contact_acquisition_queue,
        "reconcile_contact_acquisition",
        lambda *_a, **_kw: {"status": "error", "durable_state_written": False},
    )

    with pytest.raises(RuntimeError, match="durable state unavailable"):
        contact_acquisition_queue.reconcile_pending_contact_acquisition(
            brand_scope="organization:9",
            limit=5,
            conn=db,
        )


def test_error_state_failure_logs_only_exception_type(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _queue_db()
    secret = "dummy-contact@example.invalid"
    _force_extractor_failure(monkeypatch, secret)
    monkeypatch.setattr(
        contact_acquisition_queue,
        "_queue_update",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError(secret)),
    )
    result = contact_acquisition_queue.reconcile_contact_acquisition(
        1,
        brand_scope="organization:9",
        conn=db,
    )

    assert result["status"] == "error"
    assert result["durable_state_written"] is False
    assert secret not in caplog.text
