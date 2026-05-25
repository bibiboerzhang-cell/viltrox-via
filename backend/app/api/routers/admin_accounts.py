"""Admin social account and user account routes."""
from __future__ import annotations

from app.api.routers.admin_common import *

router = APIRouter(tags=["admin"])

@router.get("/api/admin/social-accounts")
def admin_list_social_accounts(request: Request, verified: str = ""):
    require_admin(request)

    def _build():
        conn = get_conn()
        base = "SELECT sa.*,u.email,u.name as user_name FROM user_social_accounts sa LEFT JOIN users u ON sa.user_id=u.id"
        if verified == "0": q = base + " WHERE sa.verified=0 ORDER BY sa.id DESC"
        elif verified == "1": q = base + " WHERE sa.verified=1 ORDER BY sa.id DESC"
        else: q = base + " ORDER BY sa.id DESC"
        return {"accounts": [dict(r) for r in conn.execute(q).fetchall()]}

    return _admin_cache_get_or_build(
        "social_accounts",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        verified=verified or "all",
    )

@router.post("/api/admin/social-accounts/{account_id}/verify")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_verify_social(account_id: int, request: Request):
    require_admin(request)
    raise HTTPException(
        409,
        "Strict verification mode is enabled. Accounts can only become verified when the comment scanner detects the active code.",
    )

@router.post("/api/admin/social-accounts/{account_id}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_reject_social(account_id: int, request: Request):
    require_admin(request)
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM user_social_accounts WHERE id=?", (account_id,)).fetchone()
    conn.execute("DELETE FROM user_social_accounts WHERE id=?", (account_id,))
    conn.commit()
    if row and int(row["user_id"] or 0) > 0:
        refresh_user_social_verified(
            int(row["user_id"]),
            reason="admin_social_account_rejected",
            context={"account_id": int(account_id)},
        )
    _invalidate_admin_cache()
    return {"status":"rejected"}


# ── Users admin ──
@router.get("/api/admin/users")
def admin_list_users(request: Request, status: str = Query(default="")):
    require_admin(request)

    def _build():
        conn = get_conn()
        q = "SELECT id, created_at, email, name, creator_code, status, role, points_balance, points_total, last_login, note FROM users"
        rows = conn.execute(q + (" WHERE status=? ORDER BY id DESC" if status else " ORDER BY id DESC"),
                            ((status,) if status else ())).fetchall()
        return {"users": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "users",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        status=status or "all",
    )

@router.post("/api/admin/users/upsert")
@rate_limit("admin_mutation", max_requests=60, window_sec=300)
async def admin_upsert_user_account(request: Request):
    require_admin(request)
    try:
        body = await request.json()
        user = await db_write(partial(_upsert_admin_user_account, body if isinstance(body, dict) else {}))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _invalidate_admin_cache()
    return {"status": "success", "user": user}

@router.post("/api/admin/users/{uid}/approve")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_approve_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "approved", payload.get("note", ""))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "approved"}

@router.post("/api/admin/users/{uid}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_reject_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "rejected", payload.get("note", "Rejected"))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "rejected"}

@router.post("/api/admin/users/{uid}/block")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_block_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "blocked", payload.get("reason", "Blocked by admin"))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "blocked"}

@router.post("/api/admin/users/{uid}/unblock")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_unblock_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "approved", payload.get("reason", "Unblocked by admin"))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "approved"}

@router.delete("/api/admin/users/{uid}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_delete_user(uid: int, request: Request):
    require_admin(request)
    deps = _load_user_delete_dependencies(uid)
    if any(deps.values()):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "User has historical records and cannot be hard-deleted safely",
                "dependencies": deps,
            },
        )
    deleted = _delete_user_by_id(uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "success"}
