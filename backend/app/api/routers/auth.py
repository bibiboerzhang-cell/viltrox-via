"""
api/routers/auth.py — 认证路由 (/api/auth/*)
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from functools import partial
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.dependencies.auth import get_user_required
from app.core.config import UPLOAD_DIR
from app.core.config import IS_PRODUCTION
from app.db.connection import db_read, db_write, get_conn, is_postgres_runtime
from app.core.logging import get_logger
from app.core.security import (
    apply_auth_cookie,
    clear_auth_cookie,
    get_current_user,
    get_current_user_async,
    hash_password,
    invalidate_user_cache,
    needs_password_rehash,
    verify_password,
)
from app.schemas.auth import RegisterRequest, LoginRequest
from app.services.security.rate_limiter import rate_limit
from app.services.auth.email import send_verification_email, send_password_reset_email
from app.services.auth.service import build_login_payload, import_legacy_user_if_available, validate_login_credentials
from app.db.repositories.users import creator_code_exists, generate_creator_code, get_user_by_email, touch_user_last_login
from app.services.student_identity import claim_student_identity_for_user, resolve_student_identity_code, validate_student_identity_email
from app.services.activities.attribution import EVENT_COOKIE, record_registration

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger(__name__)

AVATAR_DIR = UPLOAD_DIR / "staff_avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_AVATAR_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_AVATAR_BYTES = 3 * 1024 * 1024


class SseTicketRequest(BaseModel):
    endpoint: str


@router.post("/sse-ticket")
async def create_sse_ticket(
    body: SseTicketRequest,
    response: Response,
    user=Depends(get_user_required),
):
    """Prepare one authenticated EventSource connection without exposing a JWT.

    The opaque ticket is set only as an HttpOnly, endpoint-scoped cookie; it is
    never present in the JSON payload or URL and is consumed once.
    """
    from app.services.auth.sse_tickets import (
        SseTicketStoreUnavailable,
        issue_sse_ticket,
        normalize_sse_endpoint,
        ticket_cookie_name,
        ticket_ttl_seconds,
    )

    user_id = int(user.get("id") or 0)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        endpoint = normalize_sse_endpoint(body.endpoint)
        ticket = issue_sse_ticket(user_id=user_id, endpoint=endpoint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except SseTicketStoreUnavailable:
        raise HTTPException(status_code=503, detail="Realtime authentication unavailable") from None
    ttl = ticket_ttl_seconds()
    response.set_cookie(
        key=ticket_cookie_name(endpoint),
        value=ticket,
        max_age=ttl,
        httponly=True,
        secure=bool(IS_PRODUCTION),
        samesite="lax",
        path=endpoint,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"status": "ready", "expires_in": ttl}


def _client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for", "") or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return str(getattr(request.client, "host", "") or "").strip()


def _is_creator_code_conflict(exc: Exception) -> bool:
    text = str(exc).lower()
    return "creator_code" in text and "unique" in text


def _fetch_user_verification_row(email: str):
    conn = get_conn()
    return conn.execute(
        "SELECT id,name,email,email_verified FROM users WHERE email=?",
        (email,),
    ).fetchone()


def _fetch_user_name_row(email: str):
    conn = get_conn()
    return conn.execute("SELECT id,name FROM users WHERE email=?", (email,)).fetchone()


def _fetch_user_row(user_id: int):
    conn = get_conn()
    return conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()


def _safe_avatar_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("/uploads/staff_avatars/"):
        return raw
    if raw.startswith("https://"):
        return raw
    raise ValueError("Avatar URL must use HTTPS or /uploads/staff_avatars/")


def _update_user_avatar(user_id: int, avatar_url: str) -> dict:
    conn = get_conn()
    conn.execute("UPDATE users SET avatar_url=? WHERE id=?", (avatar_url, int(user_id)))
    conn.commit()
    invalidate_user_cache(int(user_id))
    user_row = _fetch_user_row(int(user_id))
    if not user_row:
        return {"status": "error", "message": "User not found"}
    return build_login_payload(user_row)


def _update_user_name(user_id: int, name: str) -> dict:
    conn = get_conn()
    conn.execute("UPDATE users SET name=? WHERE id=?", (name, int(user_id)))
    conn.commit()
    invalidate_user_cache(int(user_id))
    user_row = _fetch_user_row(int(user_id))
    if not user_row:
        return {"status": "error", "message": "User not found"}
    return build_login_payload(user_row)


def _fetch_email_token(token: str, token_type: str):
    conn = get_conn()
    return conn.execute(
        "SELECT id,user_id,expires_at,used_at FROM email_tokens WHERE token=? AND type=?",
        (token, token_type),
    ).fetchone()


def _fetch_user_password_hash(user_id: int):
    conn = get_conn()
    return conn.execute(
        "SELECT password_hash FROM users WHERE id=?",
        (user_id,),
    ).fetchone()


def _reset_password_sync(now_str: str, token_id: int, user_id: int, password_hash: str):
    conn = get_conn()
    conn.execute("UPDATE email_tokens SET used_at=? WHERE id=?", (now_str, token_id))
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
    conn.commit()


def _update_password_sync(user_id: int, password_hash: str):
    conn = get_conn()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (password_hash, user_id),
    )
    conn.commit()


@router.post("/register")
@rate_limit("login_register", max_requests=10, window_sec=60)
def auth_register(request: Request, req: RegisterRequest, response: Response):
    if not req.email or "@" not in req.email:
        return {"status": "error", "message": "Invalid email"}
    if len(req.password) < 8:
        return {"status": "error", "message": "Password must be at least 8 characters"}
    if not req.name or len(req.name.strip()) < 2:
        return {"status": "error", "message": "Name must be at least 2 characters"}
    try:
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = get_conn()
        student_id = str(req.student_id or "").strip()
        student_identity_match = resolve_student_identity_code(student_id) if student_id else {}
        if student_id and not student_identity_match:
            return {"status": "error", "message": "VID not recognized"}
        if student_identity_match:
            validate_student_identity_email(str(student_identity_match.get("student_id_code") or student_id), req.email)
        user_note = (
            f"student_identity:{student_identity_match.get('school_id')}:{student_identity_match.get('student_id_code')}"
            if student_identity_match
            else (f"student_identity_hint:{student_id}" if student_id else "")
        )
        sql = """INSERT INTO users
               (created_at, email, password_hash, name, creator_code, status, role, email_verified, note)
               VALUES (?,?,?,?,?,?,?,?,?)"""
        user_id = 0
        student_program = {}
        for attempt in range(6):
            creator_code = generate_creator_code(conn, offset=attempt)
            if creator_code_exists(conn, creator_code):
                continue
            params = (
                now,
                req.email.lower().strip(),
                hash_password(req.password),
                req.name.strip(),
                creator_code,
                "pending" if IS_PRODUCTION else "approved",
                "creator",
                0 if IS_PRODUCTION else 1,
                user_note,
            )
            try:
                if is_postgres_runtime():
                    cur = conn.execute(sql + " RETURNING id", params)
                    row = cur.fetchone()
                    user_id = int(row["id"]) if row else 0
                else:
                    cur = conn.execute(sql, params)
                    user_id = int(cur.lastrowid)
                break
            except Exception as exc:
                if _is_creator_code_conflict(exc):
                    conn.rollback()
                    continue
                raise
        if not user_id:
            raise RuntimeError("Could not allocate a unique creator_code")
        conn.commit()
        if student_identity_match:
            try:
                student_program = claim_student_identity_for_user(
                    user_id=int(user_id),
                    student_id_code=str(student_identity_match.get("student_id_code") or student_id).strip(),
                    verified_by="auth_register",
                )
            except Exception:
                cleanup_conn = get_conn()
                cleanup_conn.execute("DELETE FROM users WHERE id=?", (int(user_id),))
                cleanup_conn.commit()
                raise
        try:
            record_registration(
                int(user_id),
                str(req.event_token or request.cookies.get(EVENT_COOKIE) or ""),
                request=request,
            )
        except Exception:
            logger.warning("auth.activity_registration_attribution_failed", extra={"user_id": int(user_id)}, exc_info=True)
        if not IS_PRODUCTION:
            user_row = _fetch_user_row(int(user_id))
            payload = build_login_payload(user_row)
            apply_auth_cookie(response, payload["token"])
            if student_program:
                payload["student"] = student_program
            payload["message"] = "Registration complete — signed in automatically on this local runtime"
            return payload
        try:
            send_verification_email(user_id, req.email.lower().strip(), req.name.strip())
        except Exception as e:
            logger.warning("register verification email failed | user_id=%s | error=%s", user_id, e)
        return {
            "status": "success",
            "message": "Registration submitted — check your email to verify, then await admin approval",
        }
    except Exception as e:
        if isinstance(e, ValueError):
            return {"status": "error", "message": str(e)}
        if "UNIQUE" in str(e):
            return {"status": "error", "message": "Email already registered"}
        logger.exception("auth.register_failed", extra={"email": req.email.lower().strip()})
        return {"status": "error", "message": "Registration could not be completed"}


@router.post("/login")
@rate_limit("login_register", max_requests=10, window_sec=60)
async def auth_login(request: Request, req: LoginRequest, response: Response):
    user = await db_read(lambda: get_user_by_email(req.email))
    if not user:
        imported = await db_write(lambda: import_legacy_user_if_available(req.email))
        if imported:
            user = await db_read(lambda: get_user_by_email(req.email))
    validation_error = await asyncio.to_thread(validate_login_credentials, user, req.password, client_ip=_client_ip(request))
    if validation_error:
        return validation_error
    if user and needs_password_rehash(user["password_hash"]):
        upgraded_hash = await asyncio.to_thread(hash_password, req.password)
        await db_write(partial(_update_password_sync, int(user["id"]), upgraded_hash))
    await db_write(lambda: touch_user_last_login(user["id"]))
    payload = await asyncio.to_thread(build_login_payload, user)
    apply_auth_cookie(response, payload["token"])
    return payload


@router.get("/me")
def auth_me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    # 2026-06-16 在线状态:/me 是前端轮询点,节流更新 last_seen_at(>50s 才写,免每次轮询都写库)。
    try:
        uid = int(user.get("id") or 0)
        if uid:
            from app.db.connection import get_conn
            conn = get_conn()
            conn.execute(
                "UPDATE users SET last_seen_at = now() "
                "WHERE id = ? AND (last_seen_at IS NULL OR last_seen_at < now() - INTERVAL '50 seconds')",
                (uid,),
            )
            conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return {"status": "success", "user": user}


@router.post("/me/avatar")
async def update_my_avatar(
    request: Request,
    response: Response,
    avatar_url: str = Form(default=""),
    file: UploadFile | None = File(default=None),
):
    user = await get_current_user_async(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    user_id = int(user["id"])
    if file is not None and file.filename:
        content_type = str(file.content_type or "").lower()
        ext = ALLOWED_AVATAR_TYPES.get(content_type)
        if not ext:
            return {"status": "error", "message": "Avatar must be JPG, PNG, or WebP"}
        data = await file.read()
        if not data:
            return {"status": "error", "message": "Avatar file is empty"}
        if len(data) > MAX_AVATAR_BYTES:
            return {"status": "error", "message": "Avatar file must be under 3MB"}
        token = secrets.token_hex(8)
        path = AVATAR_DIR / f"user_{user_id}_{token}{ext}"
        path.write_bytes(data)
        payload = _update_user_avatar(user_id, f"/uploads/staff_avatars/{path.name}")
        if payload.get("token"):
            apply_auth_cookie(response, str(payload["token"]))
        return payload
    try:
        clean_url = _safe_avatar_url(avatar_url)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if not clean_url:
        return {"status": "error", "message": "Avatar file or avatar_url required"}
    payload = _update_user_avatar(user_id, clean_url)
    if payload.get("token"):
        apply_auth_cookie(response, str(payload["token"]))
    return payload


@router.post("/me/profile")
async def update_my_profile(request: Request, response: Response):
    """自助改名(登录用户改自己 display name)。镜像 /me/avatar:更新后重签 cookie。"""
    user = await get_current_user_async(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        return {"status": "error", "message": "name required"}
    if len(name) > 80:
        return {"status": "error", "message": "name too long (max 80)"}
    payload = _update_user_name(int(user["id"]), name)
    if payload.get("token"):
        apply_auth_cookie(response, str(payload["token"]))
    return payload


@router.post("/logout")
def auth_logout(response: Response):
    clear_auth_cookie(response)
    return {"status": "success", "message": "Signed out"}


@router.get("/verify-email/{token}")
def verify_email_endpoint(token: str):
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    row = conn.execute(
        "SELECT id,user_id,expires_at,used_at FROM email_tokens WHERE token=? AND type='verify_email'",
        (token,),
    ).fetchone()
    if not row: return RedirectResponse(url="/?verify=invalid")
    if row["used_at"]: return RedirectResponse(url="/?verify=already_used")
    if datetime.utcnow() > datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ"):
        return RedirectResponse(url="/?verify=expired")
    conn.execute("UPDATE email_tokens SET used_at=? WHERE id=?", (now_str, row["id"]))
    conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (row["user_id"],))
    conn.commit()
    return RedirectResponse(url="/?verify=success")


@router.post("/resend-verification")
@rate_limit("login_register", max_requests=10, window_sec=60)
async def resend_verification(request: Request):
    body = await request.json()
    email = body.get("email", "").lower().strip()
    user = await db_read(lambda: _fetch_user_verification_row(email))
    if not user: return {"status": "error", "message": "Email not found"}
    if user["email_verified"]: return {"status": "error", "message": "Email already verified"}
    await asyncio.to_thread(send_verification_email, user["id"], user["email"], user["name"])
    return {"status": "success", "message": "Verification email resent"}


@router.post("/forgot-password")
@rate_limit("login_register", max_requests=10, window_sec=60)
async def forgot_password(request: Request):
    body = await request.json()
    email = body.get("email", "").lower().strip()
    user = await db_read(lambda: _fetch_user_name_row(email))
    if user:
        await asyncio.to_thread(send_password_reset_email, user["id"], email, user["name"])
    return {"status": "success", "message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password")
@rate_limit("login_register", max_requests=10, window_sec=60)
async def reset_password_endpoint(request: Request):
    body = await request.json()
    token = body.get("token", "")
    new_pass = body.get("password", "")
    if len(new_pass) < 8: return {"status": "error", "message": "Password must be at least 8 characters"}
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    password_hash = await asyncio.to_thread(hash_password, new_pass)

    row = await db_read(lambda: _fetch_email_token(token, "reset_password"))
    if not row or row["used_at"]: return {"status": "error", "message": "Invalid or expired reset link"}
    if datetime.utcnow() > datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ"):
        return {"status": "error", "message": "Reset link has expired"}

    await db_write(partial(_reset_password_sync, now_str, int(row["id"]), int(row["user_id"]), password_hash))
    return {"status": "success", "message": "Password updated — you can now log in"}


@router.post("/change-password")
@rate_limit("account_sensitive", max_requests=10, window_sec=300)
async def change_password(request: Request):
    user = await get_current_user_async(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    body = await request.json()
    current_pw = body.get("current_password", "")
    new_pw = body.get("new_password", "")
    if not current_pw or not new_pw:
        return {"status": "error", "message": "current_password and new_password required"}
    if len(new_pw) < 8:
        return {"status": "error", "message": "Password must be at least 8 characters"}
    db_user = await db_read(lambda: _fetch_user_password_hash(int(user["id"])))
    if not db_user or not verify_password(current_pw, db_user["password_hash"]):
        return {"status": "error", "message": "Current password is incorrect"}
    password_hash = await asyncio.to_thread(hash_password, new_pw)

    await db_write(partial(_update_password_sync, int(user["id"]), password_hash))
    return {"status": "success", "message": "Password updated"}
