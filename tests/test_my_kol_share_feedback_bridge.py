"""MY KOL 勾选成员写口插桩(波 C·C2,L 车道清单):共享成功 commit 后 best-effort 桥进推荐反馈。

- 调用一次 ``recommendations.actions.record_pool_action_feedback(pid, "favorite", staff=staff,
  payload={"pool_action": "member"})``;
- 桥抛任何异常只记日志,主写已 commit、响应不变;
- 主写失败(归属校验 403 / 参数 400)时桥不得被调用。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_my_kol as router
from app.domains.recommendations import actions as rec_actions


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE staff (id INTEGER PRIMARY KEY, user_id INTEGER, active INTEGER NOT NULL,
            suspended_at TEXT);
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, linked_main_kol_id INTEGER);
        CREATE TABLE vkpi_kol_claims (id INTEGER PRIMARY KEY, kol_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL, status TEXT NOT NULL);
        CREATE TABLE vkpi_kol_pool_favorites (id INTEGER PRIMARY KEY, kol_pool_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL, UNIQUE (kol_pool_id, staff_id));
        CREATE TABLE vkpi_kol_pool_members (id INTEGER PRIMARY KEY, kol_pool_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL, shared_by INTEGER, created_at TEXT,
            UNIQUE (kol_pool_id, staff_id));
        INSERT INTO users (id, status) VALUES
            (111, 'approved'), (112, 'active'), (113, 'approved'),
            (114, 'pending'), (115, 'approved'), (116, 'approved');
        INSERT INTO staff (id, user_id, active, suspended_at) VALUES
            (11, 111, 1, NULL), (12, 112, 1, NULL), (13, 113, 1, NULL),
            (14, 114, 1, NULL), (15, 115, 0, NULL), (16, 116, 1, '2026-08-24');
        INSERT INTO vkpi_kol_pool (id, linked_main_kol_id) VALUES (7, 70), (8, NULL);
        INSERT INTO vkpi_kol_claims (id, kol_id, staff_id, status) VALUES (1, 70, 11, 'active');
        INSERT INTO vkpi_kol_pool_favorites (id, kol_pool_id, staff_id) VALUES (1, 8, 13);
        """
    )
    conn.commit()
    return conn


def _staff(staff_id: int, *, role: str = "member") -> dict:
    return {"id": staff_id, "staff_id": staff_id, "user_id": 100 + staff_id, "role": role, "permissions": {"vkpi": "write"}}


def _member_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool_members").fetchone()["n"])


def test_share_calls_feedback_bridge_once_with_member_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        rec_actions,
        "record_pool_action_feedback",
        lambda pid, action, **kwargs: calls.append((pid, action, kwargs)) or {"linked": False, "reason": "no_recommendation"},
    )
    staff = _staff(11)

    payload = router.my_kol_share_endpoint(7, body={"staff_id": 12}, staff=staff)

    assert payload == {"status": "shared", "kol_pool_id": 7, "staff_id": 12, "shared_by": 11}
    assert _member_count(conn) == 1
    assert calls == [(7, "favorite", {"staff": staff, "payload": {"pool_action": "member"}})]


def test_share_response_survives_feedback_bridge_failure(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)

    def boom(*_args: Any, **_kwargs: Any) -> dict:
        raise RuntimeError("recommendation corpus offline")

    monkeypatch.setattr(rec_actions, "record_pool_action_feedback", boom)

    with caplog.at_level("WARNING"):
        payload = router.my_kol_share_endpoint(7, body={"staff_id": 12}, staff=_staff(11))

    assert payload["status"] == "shared"
    assert _member_count(conn) == 1                                   # 主写已 commit,不被桥回滚
    assert any("feedback_bridge_failed" in record.getMessage() for record in caplog.records)


def test_share_rejection_never_reaches_feedback_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)
    monkeypatch.setattr(
        rec_actions,
        "record_pool_action_feedback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("bridge must not run on a rejected write")),
    )

    with pytest.raises(HTTPException) as caught:
        router.my_kol_share_endpoint(7, body={"staff_id": 12}, staff=_staff(13))   # not the claim owner
    assert caught.value.status_code == 403
    assert caught.value.detail == "my_kol_share_write_forbidden"
    with pytest.raises(HTTPException) as caught:
        router.my_kol_share_endpoint(7, body={}, staff=_staff(11))                 # missing staff_id
    assert caught.value.status_code == 400
    assert _member_count(conn) == 0


def test_favorite_owner_without_claim_can_share(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)
    monkeypatch.setattr(router, "_record_member_feedback", lambda *_args, **_kwargs: None)

    payload = router.my_kol_share_endpoint(8, body={"staff_id": 12}, staff=_staff(13))

    assert payload["status"] == "shared"
    assert payload["shared_by"] == 13
    assert _member_count(conn) == 1


def test_manager_can_share_without_favorite_or_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)
    monkeypatch.setattr(router, "_record_member_feedback", lambda *_args, **_kwargs: None)

    payload = router.my_kol_share_endpoint(8, body={"staff_id": 12}, staff=_staff(19, role="manager"))

    assert payload["status"] == "shared"
    assert payload["shared_by"] == 19


def test_manager_cannot_share_a_missing_pool_row(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)

    with pytest.raises(HTTPException) as caught:
        router.my_kol_share_endpoint(999, body={"staff_id": 12}, staff=_staff(19, role="manager"))

    assert caught.value.status_code == 404
    assert caught.value.detail == "kol_pool_not_found"
    assert _member_count(conn) == 0


def test_shared_only_member_cannot_reshare(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO vkpi_kol_pool_members (kol_pool_id, staff_id, shared_by) VALUES (?, ?, ?)",
        (7, 13, 11),
    )
    conn.commit()
    monkeypatch.setattr(router, "get_conn", lambda: conn)

    with pytest.raises(HTTPException) as caught:
        router.my_kol_share_endpoint(7, body={"staff_id": 12}, staff=_staff(13))

    assert caught.value.status_code == 403
    assert caught.value.detail == "my_kol_share_write_forbidden"
    assert _member_count(conn) == 1


@pytest.mark.parametrize(
    ("target_staff_id", "expected_status", "expected_detail"),
    [
        (11, 422, "share_recipient_self"),
        (14, 422, "share_recipient_pending"),
        (15, 422, "share_recipient_inactive"),
        (16, 422, "share_recipient_inactive"),
        (999, 404, "share_recipient_not_found"),
    ],
)
def test_share_rejects_ineligible_recipient_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    target_staff_id: int,
    expected_status: int,
    expected_detail: str,
) -> None:
    conn = _conn()
    monkeypatch.setattr(router, "get_conn", lambda: conn)
    monkeypatch.setattr(
        router,
        "_record_member_feedback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rejected share must not record feedback")),
    )

    with pytest.raises(HTTPException) as caught:
        router.my_kol_share_endpoint(7, body={"staff_id": target_staff_id}, staff=_staff(11))

    assert caught.value.status_code == expected_status
    assert caught.value.detail == expected_detail
    assert _member_count(conn) == 0
