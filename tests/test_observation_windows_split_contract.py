"""观察窗口拆分的兼容门面契约。"""
from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from typing import Any


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


def test_public_facade_signatures_do_not_expose_injected_dependencies() -> None:
    from app.domains.projects import observation_windows

    expected = {
        "list_windows": ["staff", "status", "project_id"],
        "scan_delivered_into_windows": ["staff", "days_overdue", "project_id"],
        "close_expired_windows": ["staff", "grace_days"],
        "scan_windows_for_content": ["staff", "max_windows", "min_scan_interval_minutes"],
        "scan_windows_backfill_matched_post": ["staff", "max_windows"],
    }
    for name, params in expected.items():
        signature = inspect.signature(getattr(observation_windows, name))
        assert list(signature.parameters) == params


def test_split_modules_stay_below_line_guard_limit() -> None:
    repo = Path(__file__).resolve().parents[1]
    paths = (
        repo / "backend/app/domains/projects/observation_windows.py",
        repo / "backend/app/domains/projects/observation_window_scans.py",
    )
    counts = {path.name: len(path.read_text().splitlines()) for path in paths}
    assert counts["observation_windows.py"] <= 800
    assert counts["observation_window_scans.py"] <= 800


def test_content_scan_uses_facade_callbacks_and_commits_once(monkeypatch) -> None:
    from app.domains.projects import observation_windows

    class Conn:
        def __init__(self) -> None:
            self.commits = 0

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
            if "FROM vkpi_project_content_observation_windows w" in sql:
                return _Cursor([{
                    "id": 11,
                    "project_id": 42,
                    "assignment_id": 7,
                    "kol_pool_id": 9,
                    "starts_at": datetime(2026, 8, 1),
                    "last_scan_at": None,
                }])
            if "FROM vkpi_kol_video_evidence e" in sql:
                return _Cursor([{
                    "id": 31,
                    "kol_pool_id": 9,
                    "content_url": "https://example.com/video",
                    "platform": "youtube",
                    "video_title": "test",
                    "posted_at": datetime(2026, 8, 2).date(),
                    "view_count": 10,
                    "like_count": 2,
                    "comment_count": 1,
                }])
            raise AssertionError(sql)

        def commit(self) -> None:
            self.commits += 1

    conn = Conn()
    recorded: list[dict[str, Any]] = []
    marked: list[tuple[int, bool]] = []
    monkeypatch.setattr(observation_windows, "get_conn", lambda: conn)
    monkeypatch.setattr(observation_windows, "table_exists", lambda _name: True)
    monkeypatch.setattr(observation_windows.scope, "project_filter", lambda _alias, _staff: ("", ()))
    monkeypatch.setattr(observation_windows.scope, "scope_context", lambda _staff: {"scope_mode": "owner"})
    monkeypatch.setattr(
        observation_windows,
        "record_content_candidate",
        lambda **kwargs: recorded.append(kwargs) or {"status": "created", "post": {"id": 71}},
    )
    monkeypatch.setattr(
        observation_windows,
        "_mark_window_scanned",
        lambda _conn, window_id, matched: marked.append((window_id, matched)),
    )

    result = observation_windows.scan_windows_for_content(min_scan_interval_minutes=0)

    assert result["created_posts"] == [71]
    assert recorded[0]["project_id"] == 42
    assert recorded[0]["post"]["evidence_id"] == 31
    assert marked == [(11, True)]
    assert conn.commits == 1


def test_delivered_scan_keeps_open_window_commit_boundary(monkeypatch) -> None:
    from app.domains.projects import observation_windows

    class Conn:
        def __init__(self) -> None:
            self.commits = 0

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
            if "WHERE 1=0" in sql:
                return _Cursor()
            if "FROM vkpi_shipments s" in sql:
                return _Cursor([{
                    "shipment_id": 3,
                    "project_id": 42,
                    "assignment_id": 7,
                    "delivered_at": datetime(2026, 7, 1),
                }])
            if "FROM vkpi_project_kol_assignments WHERE id=?" in sql:
                return _Cursor([{"kol_pool_id": 9}])
            raise AssertionError(sql)

        def commit(self) -> None:
            self.commits += 1

    conn = Conn()
    opened: list[dict[str, Any]] = []
    monkeypatch.setattr(observation_windows, "get_conn", lambda: conn)
    monkeypatch.setattr(observation_windows.scope, "project_filter", lambda _alias, _staff: ("", ()))
    monkeypatch.setattr(observation_windows.scope, "scope_context", lambda _staff: {"scope_mode": "owner"})
    monkeypatch.setattr(
        observation_windows,
        "open_window_for_delivered",
        lambda **kwargs: opened.append(kwargs) or {"status": "created", "window": {"id": 91}},
    )

    result = observation_windows.scan_delivered_into_windows(days_overdue=7)

    assert result["created"] == [91]
    assert opened == [{
        "project_id": 42,
        "assignment_id": 7,
        "kol_pool_id": 9,
        "delivered_at": datetime(2026, 7, 1),
        "staff": None,
    }]
    assert conn.commits == 0


def test_close_expired_windows_keeps_two_updates_in_one_commit(monkeypatch) -> None:
    from app.domains.projects import observation_windows

    class Conn:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.commits = 0

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
            self.statements.append(sql)
            assert params == (3,)
            return _Cursor(rowcount=2 if "status='closed'" in sql else 4)

        def commit(self) -> None:
            self.commits += 1

    conn = Conn()
    monkeypatch.setattr(observation_windows, "get_conn", lambda: conn)

    result = observation_windows.close_expired_windows(grace_days=3)

    assert result == {"status": "ok", "closed": 2, "content_missing": 4, "grace_days": 3}
    assert len(conn.statements) == 2
    assert conn.commits == 1


def test_backfill_uses_facade_matcher_without_overwriting(monkeypatch) -> None:
    from app.domains.projects import observation_windows

    class Conn:
        def __init__(self) -> None:
            self.commits = 0

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
            if "FROM vkpi_project_content_observation_windows w" in sql:
                return _Cursor([{"id": 12, "project_id": 42, "assignment_id": 7, "kol_pool_id": 9}])
            if "UPDATE vkpi_project_content_observation_windows" in sql:
                assert "matched_content_post_id IS NULL" in sql
                assert params[0] == 88
                return _Cursor(rowcount=1)
            raise AssertionError(sql)

        def commit(self) -> None:
            self.commits += 1

    conn = Conn()
    matched: list[dict[str, Any]] = []
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(observation_windows, "get_conn", lambda: conn)
    monkeypatch.setattr(observation_windows, "table_exists", lambda _name: True)
    monkeypatch.setattr(observation_windows.scope, "project_filter", lambda _alias, _staff: ("", ()))
    monkeypatch.setattr(observation_windows.scope, "scope_context", lambda _staff: {"scope_mode": "owner"})
    monkeypatch.setattr(
        observation_windows,
        "_find_post_for_window",
        lambda _conn, **kwargs: matched.append(kwargs) or 88,
    )
    monkeypatch.setattr(
        observation_windows,
        "_emit_event",
        lambda event_type, **kwargs: events.append((event_type, kwargs)),
    )

    result = observation_windows.scan_windows_backfill_matched_post()

    assert result["backfilled_windows"] == [12]
    assert matched == [{"project_id": 42, "assignment_id": 7, "kol_pool_id": 9}]
    assert events[0][0] == "observation.window_backfilled"
    assert conn.commits == 1
