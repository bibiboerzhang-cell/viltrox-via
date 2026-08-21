from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_my_kol as router


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT,
            email TEXT NOT NULL
        );
        CREATE TABLE staff (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL
        );
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            linked_main_kol_id INTEGER,
            display_name TEXT,
            platform TEXT,
            handle TEXT
        );
        CREATE TABLE vkpi_kol_claims (
            id INTEGER PRIMARY KEY,
            kol_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_pool_members (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            shared_by INTEGER,
            shared_via_group_id INTEGER,
            created_at TEXT
        );
        CREATE TABLE vkpi_collab_settings (
            kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            shared_goal TEXT,
            reminder_rule TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
        [
            (101, "Owner Name", "owner-secret@example.test"),
            (102, "recipient-name-secret@example.test", "recipient-secret@example.test"),
            (103, "Outsider Name", "outsider-secret@example.test"),
        ],
    )
    conn.executemany(
        "INSERT INTO staff (id, user_id) VALUES (?, ?)",
        [(11, 101), (12, 102), (13, 103)],
    )
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
            (id, linked_main_kol_id, display_name, platform, handle)
        VALUES (7, 70, 'Creator Seven', 'youtube', '@creator7')
        """
    )
    conn.execute(
        "INSERT INTO vkpi_kol_claims (id, kol_id, staff_id, status) VALUES (1, 70, 11, 'active')"
    )
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool_members
            (id, kol_pool_id, staff_id, shared_by, created_at)
        VALUES (1, 7, 12, 11, '2026-08-21T12:00:00Z')
        """
    )
    conn.commit()
    return conn


def _staff(staff_id: int, *, role: str = "member") -> dict:
    return {
        "id": staff_id,
        "staff_id": staff_id,
        "role": role,
        "permissions": {"vkpi": "read"},
    }


def _assert_no_email_pii(payload: dict) -> None:
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "@example.test" not in encoded
    for item in payload.get("items") or []:
        assert "email" not in item
        assert "to_email" not in item
        assert "from_email" not in item


def test_share_members_rejects_unrelated_reader_before_returning_member_pii(monkeypatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)

    with pytest.raises(HTTPException) as caught:
        router.my_kol_share_members_endpoint(7, staff=_staff(13))

    assert caught.value.status_code == 403


@pytest.mark.parametrize("staff", [_staff(11), _staff(99, role="manager")])
def test_share_members_allows_only_owner_or_manager_and_never_returns_email(monkeypatch, staff) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)

    payload = router.my_kol_share_members_endpoint(7, staff=staff)

    assert payload["count"] == 1
    assert payload["items"][0]["staff_id"] == 12
    assert payload["items"][0]["name"] == "Staff"
    _assert_no_email_pii(payload)


def test_shared_recipient_list_is_related_only_and_value_free_of_counterparty_email(monkeypatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)

    payload = router.my_kol_shares_list_endpoint(limit=200, staff=_staff(12))

    assert payload["scope_all"] is False
    assert payload["count"] == 1
    assert payload["items"][0]["to_staff_id"] == 12
    assert payload["items"][0]["from_name"] == "Owner Name"
    assert payload["items"][0]["to_name"] == "Staff"
    _assert_no_email_pii(payload)
