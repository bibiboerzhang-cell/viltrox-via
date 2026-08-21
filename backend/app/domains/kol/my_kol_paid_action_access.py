"""Row-level authorization and durable fences for paid MY KOL actions.

Team shares are deliberately read-only.  A provider-facing action may target a
pool row only when the actor is a manager (``scope.can_view_all``) or owns the
favorite row.  Durable jobs snapshot the actor, target and evidence identity so
the worker can fail closed after revocation or target drift.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.core.permissions import check_tab_permission
from app.core.security import user_status_allows_auth
from app.domains.access import scope
from app.domains.kol.video_url_identity import (
    VideoUrlIdentityError,
    parse_supported_video_url,
)


FENCE_KEY = "my_kol_paid_action_fence"
FENCE_VERSION = 1


class MyKolPaidActionError(RuntimeError):
    """Stable authorization/identity failure safe to expose as an error code."""

    def __init__(self, code: str, status_code: int = 403):
        super().__init__(code)
        self.code = str(code)
        self.status_code = int(status_code)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, duplicate_of_id FROM vkpi_kol_pool WHERE id=? LIMIT 1",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise MyKolPaidActionError("kol_pool_not_found", 404)
    item = dict(row)
    if _int(item.get("duplicate_of_id")):
        raise MyKolPaidActionError("kol_pool_duplicate_not_writable", 409)
    return item


def assert_target_readable(
    conn: Any,
    *,
    kol_pool_id: int,
    staff: dict[str, Any] | None,
) -> int:
    """Allow manager, own favorite, or an explicit read-only team share."""

    actor_id = scope.actor_staff_id(staff)
    if actor_id <= 0:
        raise MyKolPaidActionError("staff_identity_required", 403)
    _pool_row(conn, int(kol_pool_id))
    if scope.can_view_all(staff):
        return int(actor_id)
    favorite = conn.execute(
        """
        SELECT id FROM vkpi_kol_pool_favorites
        WHERE kol_pool_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_pool_id), int(actor_id)),
    ).fetchone()
    if favorite:
        return int(actor_id)
    shared = conn.execute(
        """
        SELECT id FROM vkpi_kol_pool_members
        WHERE kol_pool_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_pool_id), int(actor_id)),
    ).fetchone()
    if shared:
        return int(actor_id)
    raise MyKolPaidActionError("my_kol_target_read_forbidden", 403)


def assert_target_writable(
    conn: Any,
    *,
    kol_pool_id: int,
    staff: dict[str, Any] | None,
) -> int:
    """Allow manager or own favorite; a member share never grants mutation."""

    actor_id = scope.actor_staff_id(staff)
    if actor_id <= 0:
        raise MyKolPaidActionError("staff_identity_required", 403)
    _pool_row(conn, int(kol_pool_id))
    if scope.can_view_all(staff):
        return int(actor_id)
    favorite = conn.execute(
        """
        SELECT id FROM vkpi_kol_pool_favorites
        WHERE kol_pool_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_pool_id), int(actor_id)),
    ).fetchone()
    if not favorite:
        # vkpi_kol_pool_members is a visibility grant only.
        raise MyKolPaidActionError("my_kol_paid_action_write_forbidden", 403)
    return int(actor_id)


def target_write_context(
    conn: Any,
    *,
    kol_pool_id: int,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Read-only UI projection of the exact server-side paid-action policy."""

    try:
        assert_target_writable(
            conn,
            kol_pool_id=int(kol_pool_id),
            staff=staff,
        )
    except MyKolPaidActionError as exc:
        return {
            "can_run_paid_actions": False,
            "reason": exc.code,
        }
    if not check_tab_permission(staff or {}, "vkpi", "write"):
        return {
            "can_run_paid_actions": False,
            "reason": "vkpi_write_permission_required",
        }
    return {
        "can_run_paid_actions": True,
        "reason": "manager" if scope.can_view_all(staff) else "owned_favorite",
    }


def _evidence_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    evidence_id = _int(row.get("id") or row.get("evidence_id"))
    kol_pool_id = _int(row.get("kol_pool_id"))
    platform = _text(row.get("platform") or row.get("evidence_platform")).lower()
    content_url = _text(row.get("content_url"))
    if evidence_id <= 0 or kol_pool_id <= 0 or not platform or not content_url:
        raise MyKolPaidActionError("my_kol_evidence_identity_invalid", 409)
    try:
        identity = parse_supported_video_url(content_url)
    except VideoUrlIdentityError as exc:
        raise MyKolPaidActionError("my_kol_evidence_identity_invalid", 409) from exc
    if identity.platform != platform:
        raise MyKolPaidActionError("my_kol_evidence_identity_invalid", 409)
    return {
        "evidence_id": evidence_id,
        "kol_pool_id": kol_pool_id,
        "platform": platform,
        "video_id": identity.video_id,
        "normalized_url": identity.normalized_url,
        "channel_id": _text(row.get("channel_id")),
    }


def load_evidence_snapshots(
    conn: Any,
    *,
    kol_pool_id: int,
    evidence_ids: Iterable[int],
) -> list[dict[str, Any]]:
    """Load and bind every evidence id to the selected KOL and video identity."""

    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_id in evidence_ids:
        evidence_id = _int(raw_id)
        if evidence_id <= 0 or evidence_id in seen:
            continue
        seen.add(evidence_id)
        row = conn.execute(
            "SELECT * FROM vkpi_kol_video_evidence WHERE id=? LIMIT 1",
            (evidence_id,),
        ).fetchone()
        if not row:
            raise MyKolPaidActionError("video_evidence_not_found", 404)
        item = dict(row)
        if _int(item.get("kol_pool_id")) != int(kol_pool_id):
            raise MyKolPaidActionError("video_evidence_owned_by_other_kol", 409)
        if item.get("is_active") in (False, 0):
            raise MyKolPaidActionError("video_evidence_inactive", 409)
        if _text(item.get("evidence_type") or "video").lower() != "video":
            raise MyKolPaidActionError("video_evidence_not_video", 409)
        result.append(_evidence_snapshot(item))
    return result


def build_target_fence(
    conn: Any,
    *,
    action: str,
    kol_pool_id: int,
    staff: dict[str, Any] | None,
    evidence_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Authorize now and snapshot the durable actor/target/evidence binding."""

    actor_id = assert_target_writable(
        conn,
        kol_pool_id=int(kol_pool_id),
        staff=staff,
    )
    if not check_tab_permission(staff or {}, "vkpi", "write"):
        raise MyKolPaidActionError("vkpi_write_permission_required", 403)
    snapshots = load_evidence_snapshots(
        conn,
        kol_pool_id=int(kol_pool_id),
        evidence_ids=evidence_ids,
    )
    return {
        "version": FENCE_VERSION,
        "action": _text(action),
        "kol_pool_id": int(kol_pool_id),
        "staff_id": int(actor_id),
        "user_id": _int((staff or {}).get("user_id")) or None,
        "evidence": sorted(snapshots, key=lambda item: int(item["evidence_id"])),
    }


def _active_actor(
    conn: Any,
    *,
    staff_id: int,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT s.*, u.status AS user_status
        FROM staff s
        JOIN users u ON u.id=s.user_id
        WHERE s.id=?
        LIMIT 1
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        raise MyKolPaidActionError("my_kol_paid_action_actor_inactive", 403)
    actor = dict(row)
    if actor.get("active") not in (True, 1, "1") or _text(actor.get("suspended_at")):
        raise MyKolPaidActionError("my_kol_paid_action_actor_inactive", 403)
    if not user_status_allows_auth(actor.get("user_status"), production=True):
        raise MyKolPaidActionError("my_kol_paid_action_actor_inactive", 403)
    if not check_tab_permission(actor, "vkpi", "write"):
        raise MyKolPaidActionError("my_kol_paid_action_permission_revoked", 403)
    return actor


def revalidate_target_fence(
    conn: Any,
    payload: dict[str, Any],
    *,
    expected_action: str,
) -> dict[str, Any] | None:
    """Recheck a fenced durable job immediately before any provider call."""

    fence = payload.get(FENCE_KEY)
    if not isinstance(fence, dict):
        return None
    if _int(fence.get("version")) != FENCE_VERSION:
        raise MyKolPaidActionError("my_kol_paid_action_fence_invalid", 403)
    if _text(fence.get("action")) != _text(expected_action):
        raise MyKolPaidActionError("my_kol_paid_action_fence_action_mismatch", 403)
    kol_pool_id = _int(payload.get("kol_pool_id") or payload.get("target_id"))
    if kol_pool_id <= 0 or kol_pool_id != _int(fence.get("kol_pool_id")):
        raise MyKolPaidActionError("my_kol_paid_action_target_drifted", 409)
    actor = _active_actor(conn, staff_id=_int(fence.get("staff_id")))
    fenced_user_id = _int(fence.get("user_id"))
    if fenced_user_id and fenced_user_id != _int(actor.get("user_id")):
        raise MyKolPaidActionError("my_kol_paid_action_actor_changed", 403)
    assert_target_writable(conn, kol_pool_id=kol_pool_id, staff=actor)

    fenced_evidence = fence.get("evidence")
    snapshots = [dict(item) for item in fenced_evidence] if isinstance(fenced_evidence, list) else []
    if payload.get("bind_evidence_at_worker") is True:
        if expected_action != "comments_collect" or snapshots:
            raise MyKolPaidActionError("my_kol_paid_action_fence_invalid", 403)
        rows = conn.execute(
            """
            SELECT id FROM vkpi_kol_video_evidence
            WHERE kol_pool_id=? AND is_active IS NOT FALSE
              AND COALESCE(evidence_type,'video')='video'
            ORDER BY COALESCE(view_count,0) DESC, id DESC LIMIT 20
            """,
            (kol_pool_id,),
        ).fetchall()
        bound_ids = [_int(dict(row).get("id")) for row in rows]
        snapshots = load_evidence_snapshots(
            conn,
            kol_pool_id=kol_pool_id,
            evidence_ids=bound_ids,
        )
        snapshots.sort(key=lambda item: int(item["evidence_id"]))
        fence["evidence"] = snapshots
        payload["evidence_ids"] = [int(item["evidence_id"]) for item in snapshots]
        payload["bind_evidence_at_worker"] = False
    payload_ids = payload.get("evidence_ids")
    if payload_ids is None and payload.get("target_type") == "video":
        payload_ids = [payload.get("target_id")]
    normalized_payload_ids = sorted({_int(value) for value in (payload_ids or []) if _int(value) > 0})
    normalized_fence_ids = sorted({_int(item.get("evidence_id")) for item in snapshots})
    if normalized_payload_ids != normalized_fence_ids:
        raise MyKolPaidActionError("my_kol_paid_action_evidence_target_drifted", 409)
    current = load_evidence_snapshots(
        conn,
        kol_pool_id=kol_pool_id,
        evidence_ids=normalized_fence_ids,
    )
    current.sort(key=lambda item: int(item["evidence_id"]))
    if current != snapshots:
        raise MyKolPaidActionError("my_kol_paid_action_evidence_identity_drifted", 409)
    if expected_action == "video_analysis":
        if len(current) != 1:
            raise MyKolPaidActionError("my_kol_paid_action_evidence_target_drifted", 409)
        source_url = _text(payload.get("source_url"))
        try:
            source_identity = parse_supported_video_url(source_url)
        except VideoUrlIdentityError as exc:
            raise MyKolPaidActionError(
                "my_kol_paid_action_evidence_identity_drifted",
                409,
            ) from exc
        expected = current[0]
        if (
            source_identity.normalized_url != expected["normalized_url"]
            or source_identity.platform != expected["platform"]
            or source_identity.video_id != expected["video_id"]
        ):
            raise MyKolPaidActionError("my_kol_paid_action_evidence_identity_drifted", 409)
        # Downstream provider code receives the database-backed locator only.
        payload["source_url"] = expected["normalized_url"]
        payload["platform"] = expected["platform"]
        payload["platform_by_host"] = expected["platform"]
    return actor


__all__ = [
    "FENCE_KEY",
    "MyKolPaidActionError",
    "assert_target_readable",
    "assert_target_writable",
    "build_target_fence",
    "load_evidence_snapshots",
    "revalidate_target_fence",
    "target_write_context",
]
