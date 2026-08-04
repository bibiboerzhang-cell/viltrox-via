from __future__ import annotations

import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def test_local_staff_avatar_requires_a_real_contained_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import staff_avatars

    avatar_dir = tmp_path / "uploads" / "staff_avatars"
    avatar_dir.mkdir(parents=True)
    monkeypatch.setattr(staff_avatars, "STAFF_AVATAR_DIR", avatar_dir)

    present = avatar_dir / "user_1_deadbeef.png"
    present.write_bytes(b"not-an-image-but-a-real-file")

    assert staff_avatars.serialize_staff_avatar_url(
        "/uploads/staff_avatars/user_1_deadbeef.png"
    ) == "/uploads/staff_avatars/user_1_deadbeef.png"
    assert staff_avatars.serialize_staff_avatar_url(
        "/uploads/staff_avatars/user_1_missing.png"
    ) is None


@pytest.mark.parametrize(
    "value",
    [
        "/uploads/staff_avatars/../secret.png",
        "/uploads/staff_avatars/%2e%2e%2fsecret.png",
        "/uploads/staff_avatars/nested/avatar.png",
        "/uploads/staff_avatars/avatar.png?cache=1",
        "/uploads/staff_avatars/avatar.png#fragment",
        "/uploads/staff_avatars/avatar\\name.png",
        "/uploads/other/avatar.png",
        "http://images.example/avatar.png",
        "javascript:alert(1)",
    ],
)
def test_staff_avatar_rejects_unsafe_or_unsupported_urls(value: str) -> None:
    from app.core.staff_avatars import serialize_staff_avatar_url

    assert serialize_staff_avatar_url(value) is None


def test_staff_avatar_keeps_remote_https_reference() -> None:
    from app.core.staff_avatars import serialize_staff_avatar_url

    url = "https://images.example/avatar.png?v=2"
    assert serialize_staff_avatar_url(url) == url
    assert serialize_staff_avatar_url("https://user:password@images.example/avatar.png") is None


def test_cached_current_user_drops_missing_avatar(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import security

    monkeypatch.setattr(
        security,
        "cache_get",
        lambda _key: {
            "id": 1,
            "status": "active",
            "avatar_url": "/uploads/staff_avatars/user_1_missing.png",
            "avatar_required": False,
        },
    )

    user = security._load_user_for_auth(1, "auth:test")

    assert user["avatar_url"] is None
    assert user["avatar_required"] is True


def test_login_payload_drops_missing_avatar(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.auth import service

    monkeypatch.setattr(
        service,
        "staff_context_for_user",
        lambda _user: {"id": 1, "role": "admin", "permissions": {}, "is_owner": 1},
    )
    payload = service.build_login_payload(
        {
            "id": 1,
            "email": "owner@example.com",
            "name": "Owner",
            "creator_code": "owner",
            "status": "active",
            "role": "admin",
            "points_balance": 0,
            "points_pending": 0,
            "points_total": 0,
            "avatar_url": "/uploads/staff_avatars/user_1_missing.png",
            "bio": "",
            "signature": "",
        }
    )

    assert payload["user"]["avatar_url"] is None
    assert payload["user"]["avatar_required"] is True


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StaffConn:
    def execute(self, _sql, _params=()):
        return _Rows(
            [
                {
                    "id": 108,
                    "role": "employee",
                    "active": 1,
                    "user_id": 108,
                    "name": "Staff 108",
                    "email": "staff108@example.com",
                    "avatar_url": "/uploads/staff_avatars/user_108_missing.png",
                }
            ]
        )


def test_my_kol_staff_row_drops_missing_avatar() -> None:
    from app.domains.kol.my_kol_aggregate import _staff_row

    row = _staff_row(_StaffConn(), 108)

    assert row["avatar_url"] is None
