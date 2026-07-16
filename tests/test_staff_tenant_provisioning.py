from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture()
def tenant_staff_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Initialize the complete SQLite bootstrap against a private database."""

    from app.db import connection as db_connection
    from app.db.migrations import init_db

    db_path = (tmp_path / "staff-tenant.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db
    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    monkeypatch.setenv("ADMIN_PASSWORD", "vkpi-hermetic-tenant-bootstrap-only")
    init_db()
    conn = db_connection.get_conn()
    actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
    assert actual_path == db_path
    try:
        yield conn
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url


def _admin_identity(conn) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT u.id AS user_id, s.id AS staff_id
        FROM users AS u
        JOIN staff AS s ON s.user_id = u.id
        WHERE u.email='admin@viltrox.com'
        """
    ).fetchone()
    assert row is not None
    return int(row["user_id"]), int(row["staff_id"])


def _invite_body(email: str, *, role: str = "employee") -> dict[str, object]:
    return {
        "email": email,
        "full_name": "Tenant Invite Test",
        "role": role,
        "permissions": {"vkpi": "write"},
    }


def test_sqlite_greenfield_admin_is_owner_of_org_one(tenant_staff_db) -> None:
    conn = tenant_staff_db
    admin_user_id, admin_staff_id = _admin_identity(conn)
    admin = conn.execute(
        "SELECT role, status FROM users WHERE id=?",
        (admin_user_id,),
    ).fetchone()
    staff = conn.execute(
        "SELECT role, active, is_owner, email_domain_verified FROM staff WHERE id=?",
        (admin_staff_id,),
    ).fetchone()
    membership = conn.execute(
        "SELECT organization_id, role FROM organization_members WHERE staff_id=?",
        (admin_staff_id,),
    ).fetchone()
    org = conn.execute("SELECT slug FROM organizations WHERE id=1").fetchone()

    assert dict(admin) == {"role": "admin", "status": "approved"}
    assert dict(staff) == {
        "role": "admin",
        "active": 1,
        "is_owner": 1,
        "email_domain_verified": 1,
    }
    assert dict(membership) == {"organization_id": 1, "role": "owner"}
    assert dict(org) == {"slug": "viltrox"}


def test_invite_inherits_inviter_unique_organization_membership(tenant_staff_db) -> None:
    from app.services.system import staff as staff_svc

    conn = tenant_staff_db
    admin_user_id, _ = _admin_identity(conn)
    email = f"tenant-inherit-{uuid.uuid4().hex}@viltrox.com"
    created = staff_svc.create_activation_link(
        _invite_body(email, role="readonly"),
        inviter_id=admin_user_id,
    )
    membership = conn.execute(
        "SELECT organization_id, role FROM organization_members WHERE staff_id=?",
        (int(created["staff_id"]),),
    ).fetchone()

    assert dict(membership) == {"organization_id": 1, "role": "viewer"}


def test_invite_without_inviter_membership_fails_before_user_write(tenant_staff_db) -> None:
    from app.services.system import staff as staff_svc

    conn = tenant_staff_db
    admin_user_id, admin_staff_id = _admin_identity(conn)
    conn.execute("DELETE FROM organization_members WHERE staff_id=?", (admin_staff_id,))
    conn.commit()
    email = f"tenant-missing-{uuid.uuid4().hex}@viltrox.com"

    with pytest.raises(ValueError, match="exactly one organization membership"):
        staff_svc.create_activation_link(_invite_body(email), inviter_id=admin_user_id)

    assert conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone() is None


def test_invite_with_ambiguous_inviter_membership_fails_before_user_write(tenant_staff_db) -> None:
    from app.services.system import staff as staff_svc

    conn = tenant_staff_db
    admin_user_id, admin_staff_id = _admin_identity(conn)
    now = "2026-07-13T00:00:00Z"
    conn.execute(
        """
        INSERT INTO organizations (
            id, name, slug, industry, brand_profile_json, scoring_template,
            status, created_at, updated_at
        ) VALUES (2, 'Other', 'other', '', '{}', '', 'active', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO organization_members (organization_id, staff_id, role, created_at)
        VALUES (2, ?, 'owner', ?)
        """,
        (admin_staff_id, now),
    )
    conn.commit()
    email = f"tenant-ambiguous-{uuid.uuid4().hex}@viltrox.com"

    with pytest.raises(ValueError, match="exactly one organization membership"):
        staff_svc.create_activation_link(_invite_body(email), inviter_id=admin_user_id)

    assert conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone() is None


def test_reinvite_cannot_cross_existing_invitee_tenant(tenant_staff_db) -> None:
    from app.services.system import staff as staff_svc

    conn = tenant_staff_db
    admin_user_id, _ = _admin_identity(conn)
    now = "2026-07-13T00:00:00Z"
    email = f"tenant-cross-{uuid.uuid4().hex}@viltrox.com"
    conn.execute(
        """
        INSERT INTO organizations (
            id, name, slug, industry, brand_profile_json, scoring_template,
            status, created_at, updated_at
        ) VALUES (2, 'Other', 'other', '', '{}', '', 'active', ?, ?)
        """,
        (now, now),
    )
    user_cursor = conn.execute(
        """
        INSERT INTO users (created_at, email, password_hash, name, status, role)
        VALUES (?, ?, 'hash', 'Cross Tenant', 'active', 'creator')
        """,
        (now, email),
    )
    user_id = int(user_cursor.lastrowid or 0)
    staff_cursor = conn.execute(
        """
        INSERT INTO staff (
            user_id, role, permissions_json, mfa_enabled, active, invited_by,
            invited_at, is_owner, email_domain_verified
        ) VALUES (?, 'readonly', '{}', 0, 1, ?, ?, 0, 1)
        """,
        (user_id, admin_user_id, now),
    )
    staff_id = int(staff_cursor.lastrowid or 0)
    conn.execute(
        """
        INSERT INTO organization_members (organization_id, staff_id, role, created_at)
        VALUES (2, ?, 'viewer', ?)
        """,
        (staff_id, now),
    )
    conn.commit()

    with pytest.raises(ValueError, match="different organization"):
        staff_svc.create_activation_link(
            _invite_body(email, role="employee"),
            inviter_id=admin_user_id,
        )

    membership = conn.execute(
        "SELECT organization_id, role FROM organization_members WHERE staff_id=?",
        (staff_id,),
    ).fetchone()
    staff = conn.execute("SELECT role FROM staff WHERE id=?", (staff_id,)).fetchone()
    assert dict(membership) == {"organization_id": 2, "role": "viewer"}
    assert dict(staff) == {"role": "readonly"}


def _pending_staff_snapshot(conn, staff_id: int) -> tuple[dict[str, object], int]:
    staff = conn.execute(
        "SELECT user_id, invited_by, invited_at FROM staff WHERE id=?",
        (staff_id,),
    ).fetchone()
    assert staff is not None
    user_id = int(staff["user_id"])
    token_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM email_tokens WHERE user_id=? AND type='staff_invite'",
            (user_id,),
        ).fetchone()[0]
    )
    return dict(staff), token_count


def _make_pending_staff(conn, staff_svc, admin_user_id: int) -> int:
    created = staff_svc.create_activation_link(
        _invite_body(f"existing-link-{uuid.uuid4().hex}@viltrox.com"),
        inviter_id=admin_user_id,
    )
    return int(created["staff_id"])


def test_existing_activation_link_missing_inviter_membership_has_zero_writes(
    tenant_staff_db,
) -> None:
    from app.services.system import staff as staff_svc

    conn = tenant_staff_db
    admin_user_id, admin_staff_id = _admin_identity(conn)
    staff_id = _make_pending_staff(conn, staff_svc, admin_user_id)
    before = _pending_staff_snapshot(conn, staff_id)
    conn.execute("DELETE FROM organization_members WHERE staff_id=?", (admin_staff_id,))
    conn.commit()

    with pytest.raises(ValueError, match="exactly one organization membership"):
        staff_svc.create_existing_activation_link(staff_id, inviter_id=admin_user_id)

    assert _pending_staff_snapshot(conn, staff_id) == before


def test_existing_activation_link_ambiguous_inviter_membership_has_zero_writes(
    tenant_staff_db,
) -> None:
    from app.services.system import staff as staff_svc

    conn = tenant_staff_db
    admin_user_id, admin_staff_id = _admin_identity(conn)
    staff_id = _make_pending_staff(conn, staff_svc, admin_user_id)
    before = _pending_staff_snapshot(conn, staff_id)
    now = "2026-07-13T00:00:00Z"
    conn.execute(
        """
        INSERT INTO organizations (
            id, name, slug, industry, brand_profile_json, scoring_template,
            status, created_at, updated_at
        ) VALUES (2, 'Other', 'other-link', '', '{}', '', 'active', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO organization_members (organization_id, staff_id, role, created_at)
        VALUES (2, ?, 'owner', ?)
        """,
        (admin_staff_id, now),
    )
    conn.commit()

    with pytest.raises(ValueError, match="exactly one organization membership"):
        staff_svc.create_existing_activation_link(staff_id, inviter_id=admin_user_id)

    assert _pending_staff_snapshot(conn, staff_id) == before


@pytest.mark.parametrize("membership_case", ["missing", "ambiguous", "cross_tenant"])
def test_existing_activation_link_rejects_invalid_invitee_scope_without_writes(
    tenant_staff_db,
    membership_case: str,
) -> None:
    from app.services.system import staff as staff_svc

    conn = tenant_staff_db
    admin_user_id, _ = _admin_identity(conn)
    staff_id = _make_pending_staff(conn, staff_svc, admin_user_id)
    before = _pending_staff_snapshot(conn, staff_id)
    now = "2026-07-13T00:00:00Z"
    conn.execute("DELETE FROM organization_members WHERE staff_id=?", (staff_id,))
    if membership_case != "missing":
        conn.execute(
            """
            INSERT INTO organizations (
                id, name, slug, industry, brand_profile_json, scoring_template,
                status, created_at, updated_at
            ) VALUES (2, 'Other', 'other-invitee', '', '{}', '', 'active', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO organization_members (organization_id, staff_id, role, created_at)
            VALUES (2, ?, 'viewer', ?)
            """,
            (staff_id, now),
        )
    if membership_case == "ambiguous":
        conn.execute(
            """
            INSERT INTO organization_members (organization_id, staff_id, role, created_at)
            VALUES (1, ?, 'member', ?)
            """,
            (staff_id, now),
        )
    conn.commit()

    expected = {
        "missing": "exactly one organization membership",
        "ambiguous": "ambiguous",
        "cross_tenant": "different organization",
    }[membership_case]
    with pytest.raises(ValueError, match=expected):
        staff_svc.create_existing_activation_link(staff_id, inviter_id=admin_user_id)

    assert _pending_staff_snapshot(conn, staff_id) == before


def test_postgres_greenfield_bootstrap_binds_new_admin_staff_to_org_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection as db_connection

    class FakeCursor:
        def __init__(self) -> None:
            self.executions: list[tuple[str, tuple[object, ...]]] = []
            self.next_row = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql: str, params=()) -> None:
            normalized = " ".join(str(sql).split())
            bound = tuple(params or ())
            self.executions.append((normalized, bound))
            if normalized.startswith("SELECT 1 FROM users"):
                self.next_row = (1,)
            elif normalized.startswith("SELECT id FROM users"):
                self.next_row = (41,)
            elif normalized.startswith("SELECT id FROM staff"):
                self.next_row = None
            elif normalized.startswith("INSERT INTO staff"):
                self.next_row = (52,)
            else:
                self.next_row = None

        def fetchone(self):
            row = self.next_row
            self.next_row = None
            return row

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_instance = FakeCursor()
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self.cursor_instance

        def commit(self) -> None:
            self.committed = True

    class FakePool:
        def __init__(self) -> None:
            self.conn = FakeConnection()

        def connection(self, **_kwargs):
            return self.conn

    pool = FakePool()
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(db_connection, "_get_pg_pool", lambda: pool)
    monkeypatch.setattr(db_connection, "_read_env_override", lambda _key: "")

    db_connection._bootstrap_default_admin()

    membership_writes = [
        params
        for sql, params in pool.conn.cursor_instance.executions
        if sql.startswith("INSERT INTO organization_members")
    ]
    assert membership_writes == [(1, 52, "owner")]
    assert pool.conn.committed is True
