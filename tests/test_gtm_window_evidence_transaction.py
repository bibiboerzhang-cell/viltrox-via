"""GTM observation windows and their evidence events commit atomically."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.market_brain import gtm_prediction_producer, gtm_windows


class _Cursor:
    def __init__(self, *, rowcount: int = 0):
        self.rowcount = rowcount


class _Connection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.updates: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        assert "UPDATE vkpi_gtm_outcomes" in sql
        self.updates.append((sql, params))
        return _Cursor(rowcount=1)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _install_one_due_row(monkeypatch: pytest.MonkeyPatch, conn: _Connection) -> None:
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(
        gtm_prediction_producer, "registered_observation_anchors",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        gtm_windows,
        "_rows",
        lambda *_args, **_kwargs: [
            {
                "id": 41,
                "action_inbox_id": 91,
                "kol_pool_id": 17,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "decision": "open",
            }
        ],
    )
    monkeypatch.setattr(
        gtm_windows,
        "_build_window_payload",
        lambda _conn, _row, *, horizon_days, label, now: {
            "schema": "vkpi_gtm_observation_window/v1",
            "status": "filled",
            "window": label,
            "window_start": "2026-01-01T00:00:00+00:00",
            "window_end": f"2026-01-{horizon_days + 1:02d}T00:00:00+00:00",
            "filled_at": now.isoformat(),
            "source": f"fixture:{label}",
            "metrics": {"observed": 1},
        },
    )


def test_window_update_and_events_commit_together(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Connection()
    _install_one_due_row(monkeypatch, conn)
    events: list[dict[str, Any]] = []

    def record_event(_conn: Any, event_type: str, **kwargs: Any) -> int:
        assert _conn is conn
        events.append({"event_type": event_type, **kwargs})
        return len(events)

    monkeypatch.setattr(gtm_windows.event_ledger, "insert_required", record_event)
    result = gtm_windows.refresh_gtm_windows(dry_run=False, limit=1)

    assert result["status"] == "ok"
    assert result["updated_rows"] == 1
    assert result["failed"] == 0
    assert len(conn.updates) == 1
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert [event["payload"]["evidence_field"] for event in events] == [
        "window_7d",
        "window_14d",
        "window_28d",
    ]
    assert all(event["event_type"] == "gtm_window_observed" for event in events)
    assert all(event["organization_id"] == 1 for event in events)


def test_event_failure_rolls_back_window_update(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Connection()
    _install_one_due_row(monkeypatch, conn)
    monkeypatch.setattr(
        gtm_windows.event_ledger,
        "insert_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event unavailable")),
    )

    result = gtm_windows.refresh_gtm_windows(dry_run=False, limit=1)

    assert result["status"] == "ok"
    assert result["updated_rows"] == 0
    assert result["failed"] == 1
    assert len(conn.updates) == 1
    assert conn.commits == 0
    assert conn.rollbacks == 1
