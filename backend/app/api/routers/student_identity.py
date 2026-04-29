"""
api/routers/student_identity.py — public QR-first student identity endpoints
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.core.config import SITE_URL
from app.core.security import apply_auth_cookie, get_current_user
from app.schemas.student_identity import StudentPassConsumeRequest, StudentSignupRequest
from app.services.security.rate_limiter import rate_limit
from app.services.creator_public import build_creator_public_page_data, record_creator_public_click
from app.services.student_identity import (
    build_public_vid_profile,
    build_public_vid_qr_png,
    build_public_vid_share_card,
    build_student_pass,
    consume_student_pass,
    get_student_claim_metadata,
    resolve_student_qr_scan_destination,
    signup_student_from_qr,
)

router = APIRouter(tags=["student-identity"])


def _student_signup_redirect_url(qr_id: str, *, claim: str = "", sig: str = "", error: str = "") -> str:
    base = f"{SITE_URL.rstrip('/')}/?auth=register"
    if qr_id:
        base += f"&qr_id={quote(str(qr_id or ''))}"
    if error:
        base += f"&error={quote(str(error))}"
    return base


@router.get("/r/{qr_id}")
def student_qr_landing(
    qr_id: str,
    claim: str = Query(default=""),
    sig: str = Query(default=""),
):
    if not claim or not sig:
        return RedirectResponse(_student_signup_redirect_url(qr_id, error="missing-claim"), status_code=307)
    try:
        destination = resolve_student_qr_scan_destination(qr_id, claim_token=claim, signature=sig)
    except Exception as exc:
        return RedirectResponse(
            _student_signup_redirect_url(qr_id, claim=claim, sig=sig, error=str(exc)),
            status_code=307,
        )
    return RedirectResponse(str(destination.get("url") or _student_signup_redirect_url(qr_id, claim=claim, sig=sig)), status_code=307)


@router.get("/api/student/claim/{qr_id}")
def student_claim_metadata(
    qr_id: str,
    claim: str = Query(default=""),
    sig: str = Query(default=""),
):
    try:
        claim_payload = get_student_claim_metadata(qr_id, claim_token=claim, signature=sig)
        qr_status = str(claim_payload.get("status") or "")
        return {
            "status": "success",
            "claim_status": qr_status,
            **{k: v for k, v in claim_payload.items() if k != "status"},
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/public/vid/{vid}")
def public_vid_profile(vid: str):
    try:
        return build_public_vid_profile(vid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/creator-public/{vid}")
def creator_public_page_data(vid: str):
    try:
        return build_creator_public_page_data(vid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/api/creator-public/click")
async def creator_public_click(request: Request):
    try:
        payload = await request.json()
        return record_creator_public_click(
            payload if isinstance(payload, dict) else {},
            user_agent=request.headers.get("user-agent", ""),
            ip_address=request.client.host if request.client else "",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/public/vid/{vid}/share-card")
def public_vid_share_card(vid: str):
    try:
        return build_public_vid_share_card(vid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/public/vid/{vid}/share-card.png")
def public_vid_share_card_png(vid: str, download: bool = Query(False)):
    try:
        payload = build_public_vid_share_card(vid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    filename = f"{payload['creator_code']}-viltrox-qr-card.png"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else None
    return FileResponse(payload["path"], media_type="image/png", filename=filename if download else None, headers=headers)


@router.get("/api/public/vid/{vid}/qr.png")
def public_vid_qr_png(vid: str, download: bool = Query(False)):
    try:
        payload = build_public_vid_qr_png(vid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    filename = f"{payload['vid']}-qr.png"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else None
    return FileResponse(payload["path"], media_type="image/png", filename=filename if download else None, headers=headers)


@router.get("/api/public/vid/{vid}/apple-wallet")
def public_vid_apple_wallet(vid: str):
    try:
        build_public_vid_profile(vid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    raise HTTPException(
        status_code=501,
        detail="Apple Wallet pass signing is not configured yet. Configure the Apple Pass Type ID, Team ID, and signing certificate before issuing .pkpass files.",
    )


@router.post("/api/student/signup")
@rate_limit("student_claim", max_requests=10, window_sec=300)
def student_signup(request: Request, req: StudentSignupRequest, response: Response):
    try:
        payload = signup_student_from_qr(
            qr_id=req.qr_id,
            claim_token=req.claim_token,
            signature=req.signature,
            email=req.email,
            password=req.password,
            name=req.name,
            student_id=req.student_id,
            major=req.major,
            year=req.year,
        )
        if isinstance(payload, dict) and payload.get("status") == "success" and payload.get("token"):
            apply_auth_cookie(response, str(payload["token"]))
        return payload
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/student/pass")
def student_pass(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return {"status": "success", **build_student_pass(int(user["id"]))}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/student/pass/check-in")
@rate_limit("student_claim", max_requests=20, window_sec=300)
def student_pass_check_in(payload: StudentPassConsumeRequest):
    try:
        return {"status": "success", **consume_student_pass(
            token=payload.token,
            signature=payload.signature,
            location=payload.location,
            context=payload.context,
        )}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/student-pass/check-in")
def student_pass_check_in_page(
    token: str = Query(default=""),
    sig: str = Query(default=""),
    location: str = Query(default=""),
    context: str = Query(default="event_checkin"),
):
    try:
        result = consume_student_pass(token=token, signature=sig, location=location, context=context)
        html = f"""
        <html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f7;padding:32px">
        <div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:24px;padding:28px">
          <h1 style="margin:0 0 12px">Student check-in complete</h1>
          <p style="color:#4b5563;line-height:1.7">User #{result['user_id']} · {result['student_id_code']} · {result['context']}</p>
        </div></body></html>
        """
        return HTMLResponse(html)
    except Exception as exc:
        html = f"""
        <html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f7;padding:32px">
        <div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:24px;padding:28px">
          <h1 style="margin:0 0 12px">Student check-in unavailable</h1>
          <p style="color:#b91c1c;line-height:1.7">{str(exc)}</p>
        </div></body></html>
        """
        return HTMLResponse(html, status_code=400)
