"""Owner scope and durable authorization for industry account refresh jobs."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from app.core.config import IS_PRODUCTION, JWT_SECRET
from app.core.permissions import check_tab_permission
from app.core.security import user_status_allows_auth
from app.db.connection import get_conn
from app.domains.access import scope


FENCE_KEY = "industry_account_refresh_fence"
FENCE_VERSION = 1
REFRESH_ACTION = "industry_account_refresh"


class IndustryAccessError(RuntimeError):
    """Stable owner/identity error for HTTP and durable worker boundaries."""

    def __init__(self, code: str, status_code: int = 403):
        super().__init__(code)
        self.code = str(code)
        self.status_code = int(status_code)


@dataclass(frozen=True)
class ServerIndustryRefreshCapability:
    """Opaque scheduler-only capability; JSON dictionaries are invalid."""

    account_id: int
    project_id: int
    signature: str


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    return _text(value).lower() in {"1", "active", "on", "true", "yes"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _secret() -> bytes:
    explicit = _text(os.environ.get("VKPI_INDUSTRY_REFRESH_FENCE_SECRET"))
    if explicit:
        return explicit.encode("utf-8")
    if IS_PRODUCTION:
        return _text(JWT_SECRET).encode("utf-8")
    return b"vkpi-local-industry-refresh-fence-v1-development-only"


def _signature(value: Any) -> str:
    return hmac.new(_secret(), _canonical(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _signed(claim: dict[str, Any]) -> dict[str, Any]:
    unsigned = {key: value for key, value in claim.items() if key != "signature"}
    return {**unsigned, "signature": _signature(unsigned)}


def _valid_signature(claim: dict[str, Any]) -> bool:
    supplied = _text(claim.get("signature"))
    unsigned = {key: value for key, value in claim.items() if key != "signature"}
    return bool(supplied) and hmac.compare_digest(supplied, _signature(unsigned))


def _project_row(conn: Any, project_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, owner_staff_id, is_active FROM vkpi_industry_projects WHERE id=? LIMIT 1",
        (int(project_id),),
    ).fetchone()
    if not row:
        raise IndustryAccessError("industry_project_not_found", 404)
    return dict(row)


def _account_row(conn: Any, account_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT a.id, a.project_id, a.platform, a.platform_user_id, a.handle,
               a.profile_url, a.crawl_enabled, a.is_active AS account_is_active,
               p.owner_staff_id, p.is_active AS project_is_active
        FROM vkpi_industry_accounts a
        JOIN vkpi_industry_projects p ON p.id=a.project_id
        WHERE a.id=? LIMIT 1
        """,
        (int(account_id),),
    ).fetchone()
    if not row:
        raise IndustryAccessError("industry_account_not_found", 404)
    return dict(row)


def assert_project_access(
    project_id: int,
    staff: dict[str, Any] | None,
    *,
    write: bool = False,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Managers see all; members only their exact ``owner_staff_id`` rows."""

    actor_id = scope.actor_staff_id(staff)
    if actor_id <= 0:
        raise IndustryAccessError("industry_staff_identity_required")
    row = _project_row(conn or get_conn(), int(project_id))
    if scope.can_view_all(staff):
        return row
    if _int(row.get("owner_staff_id")) != actor_id:
        raise IndustryAccessError(
            "industry_project_write_forbidden" if write else "industry_project_read_forbidden"
        )
    return row


def assert_account_access(
    account_id: int,
    staff: dict[str, Any] | None,
    *,
    write: bool = False,
    expected_project_id: int | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    db = conn or get_conn()
    row = _account_row(db, int(account_id))
    if expected_project_id is not None and _int(row.get("project_id")) != int(expected_project_id):
        raise IndustryAccessError("industry_account_project_mismatch", 409)
    assert_project_access(
        _int(row.get("project_id")),
        staff,
        write=write,
        conn=db,
    )
    return row


def resolve_create_owner(
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
    *,
    conn: Any | None = None,
) -> int:
    """Resolve an owner from server-side identity; never trust request JSON."""

    actor_id = scope.actor_staff_id(staff)
    if actor_id <= 0:
        raise IndustryAccessError("industry_staff_identity_required")
    requested = _int(payload.get("owner_staff_id"))
    if not scope.can_view_all(staff):
        if requested not in {0, actor_id}:
            raise IndustryAccessError("industry_project_owner_forgery_forbidden")
        return actor_id
    owner_id = requested or actor_id
    row = (conn or get_conn()).execute(
        """
        SELECT s.id, s.active, s.suspended_at, u.status AS user_status
        FROM staff s JOIN users u ON u.id=s.user_id
        WHERE s.id=? LIMIT 1
        """,
        (owner_id,),
    ).fetchone()
    if not row:
        raise IndustryAccessError("industry_project_owner_invalid", 400)
    owner = dict(row)
    if (
        owner.get("active") not in (True, 1, "1")
        or _text(owner.get("suspended_at"))
        or not user_status_allows_auth(owner.get("user_status"), production=True)
    ):
        raise IndustryAccessError("industry_project_owner_invalid", 400)
    return owner_id


def _active_actor(conn: Any, *, staff_id: int, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT s.*, u.status AS user_status
        FROM staff s JOIN users u ON u.id=s.user_id
        WHERE s.id=? LIMIT 1
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        raise IndustryAccessError("industry_refresh_actor_inactive")
    actor = dict(row)
    if int(user_id) > 0 and _int(actor.get("user_id")) != int(user_id):
        raise IndustryAccessError("industry_refresh_actor_changed")
    if actor.get("active") not in (True, 1, "1") or _text(actor.get("suspended_at")):
        raise IndustryAccessError("industry_refresh_actor_inactive")
    if not user_status_allows_auth(actor.get("user_status"), production=True):
        raise IndustryAccessError("industry_refresh_actor_inactive")
    if not check_tab_permission(actor, "vkpi", "write"):
        raise IndustryAccessError("industry_refresh_permission_revoked")
    return actor


def _account_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": _int(row.get("id")),
        "project_id": _int(row.get("project_id")),
        "platform": _text(row.get("platform")).lower(),
        "platform_user_id": _text(row.get("platform_user_id")),
        "handle": _text(row.get("handle")).lower(),
        "profile_url": _text(row.get("profile_url")),
    }


def issue_server_refresh_capability(
    *,
    account_id: int,
    project_id: int,
) -> ServerIndustryRefreshCapability:
    account = _int(account_id)
    project = _int(project_id)
    if account <= 0 or project <= 0:
        raise IndustryAccessError("industry_refresh_server_target_invalid")
    claim = {
        "version": FENCE_VERSION,
        "action": REFRESH_ACTION,
        "account_id": account,
        "project_id": project,
    }
    return ServerIndustryRefreshCapability(account, project, _signature(claim))


def _valid_server_capability(
    capability: ServerIndustryRefreshCapability | None,
    *,
    account_id: int,
    project_id: int,
) -> bool:
    if not isinstance(capability, ServerIndustryRefreshCapability):
        return False
    claim = {
        "version": FENCE_VERSION,
        "action": REFRESH_ACTION,
        "account_id": _int(capability.account_id),
        "project_id": _int(capability.project_id),
    }
    return bool(
        claim["account_id"] == int(account_id)
        and claim["project_id"] == int(project_id)
        and hmac.compare_digest(_text(capability.signature), _signature(claim))
    )


def build_refresh_payload(
    account_id: int,
    *,
    staff: dict[str, Any] | None = None,
    server_capability: ServerIndustryRefreshCapability | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Authorize enqueue and persist only actor ids plus signed target binding."""

    db = conn or get_conn()
    account = _account_row(db, int(account_id))
    project_id = _int(account.get("project_id"))
    if not _active(account.get("account_is_active")) or not _active(account.get("project_is_active")):
        raise IndustryAccessError("industry_refresh_target_inactive", 409)
    server_owned = _valid_server_capability(
        server_capability,
        account_id=int(account_id),
        project_id=project_id,
    )
    if server_capability is not None and not server_owned:
        raise IndustryAccessError("industry_refresh_server_capability_invalid")
    if server_owned:
        staff_id = user_id = 0
    else:
        requested_staff_id = scope.actor_staff_id(staff)
        requested_user_id = _int((staff or {}).get("user_id"))
        if requested_staff_id <= 0:
            raise IndustryAccessError("industry_staff_identity_required")
        actor = _active_actor(
            db,
            staff_id=requested_staff_id,
            user_id=requested_user_id,
        )
        assert_account_access(int(account_id), actor, write=True, conn=db)
        staff_id = _int(actor.get("id"))
        user_id = _int(actor.get("user_id"))
    binding = {
        "account_id": int(account_id),
        "project_id": project_id,
        "account_identity_sha256": _digest(_account_identity(account)),
        "staff_id": staff_id or None,
        "user_id": user_id or None,
    }
    fence = _signed(
        {
            "version": FENCE_VERSION,
            "mode": "server_owned" if server_owned else "user",
            "action": REFRESH_ACTION,
            "binding": binding,
        }
    )
    return {
        "account_id": int(account_id),
        "project_id": project_id,
        "staff_id": staff_id or None,
        "user_id": user_id or None,
        FENCE_KEY: fence,
    }


def revalidate_refresh_payload(
    payload: dict[str, Any],
    *,
    conn: Any | None = None,
    require_account_identity: bool = True,
) -> dict[str, Any]:
    """Recheck actor, owner, project membership and provider target identity."""

    fence = payload.get(FENCE_KEY)
    if not isinstance(fence, dict):
        raise IndustryAccessError("industry_refresh_fence_required")
    if _int(fence.get("version")) != FENCE_VERSION or not _valid_signature(fence):
        raise IndustryAccessError("industry_refresh_fence_invalid")
    if _text(fence.get("action")) != REFRESH_ACTION:
        raise IndustryAccessError("industry_refresh_action_drifted", 409)
    binding = fence.get("binding")
    if not isinstance(binding, dict):
        raise IndustryAccessError("industry_refresh_fence_invalid")
    current_binding = {
        "account_id": _int(payload.get("account_id")),
        "project_id": _int(payload.get("project_id")),
        "staff_id": _int(payload.get("staff_id")) or None,
        "user_id": _int(payload.get("user_id")) or None,
    }
    for key, value in current_binding.items():
        if binding.get(key) != value:
            raise IndustryAccessError("industry_refresh_payload_drifted", 409)
    db = conn or get_conn()
    account = _account_row(db, current_binding["account_id"])
    if _int(account.get("project_id")) != current_binding["project_id"]:
        raise IndustryAccessError("industry_refresh_account_moved", 409)
    if not _active(account.get("account_is_active")) or not _active(account.get("project_is_active")):
        raise IndustryAccessError("industry_refresh_target_inactive", 409)
    if require_account_identity and not hmac.compare_digest(
        _text(binding.get("account_identity_sha256")),
        _digest(_account_identity(account)),
    ):
        raise IndustryAccessError("industry_refresh_account_identity_drifted", 409)
    mode = _text(fence.get("mode"))
    if mode == "server_owned":
        if current_binding["staff_id"] is not None or current_binding["user_id"] is not None:
            raise IndustryAccessError("industry_refresh_server_fence_invalid")
        return {"id": None, "user_id": None, "server_owned": True}
    if mode != "user":
        raise IndustryAccessError("industry_refresh_fence_invalid")
    staff_id = _int(current_binding["staff_id"])
    user_id = _int(current_binding["user_id"])
    if staff_id <= 0 or user_id <= 0:
        raise IndustryAccessError("industry_refresh_actor_identity_required")
    actor = _active_actor(db, staff_id=staff_id, user_id=user_id)
    try:
        assert_account_access(
            current_binding["account_id"],
            actor,
            write=True,
            expected_project_id=current_binding["project_id"],
            conn=db,
        )
    except IndustryAccessError as exc:
        if exc.status_code == 404:
            raise
        raise IndustryAccessError("industry_refresh_owner_revoked") from exc
    return actor


def blocked_result(
    exc: IndustryAccessError,
    *,
    provider_calls_performed: bool,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": exc.code,
        "provider_calls_performed": bool(provider_calls_performed),
        "retryable": False,
    }


__all__ = [
    "FENCE_KEY",
    "IndustryAccessError",
    "ServerIndustryRefreshCapability",
    "assert_account_access",
    "assert_project_access",
    "blocked_result",
    "build_refresh_payload",
    "issue_server_refresh_capability",
    "resolve_create_owner",
    "revalidate_refresh_payload",
]
