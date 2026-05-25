"""Public VID profile and scan helpers for student identity."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.core.config import PROJECT_ROOT, SITE_URL, UPLOAD_DIR
from app.db.connection import get_conn
from app.db.repositories.student_identity import create_student_scan_event, get_school, get_student_qr_code
from app.services.verification.viltrox_official import build_profile_url
from app.services.student_identity_common import (
    _cover_fit_image,
    _creator_code_candidates,
    _creator_code_path_url,
    _font_or_default,
    _load_json,
    _load_pil_modules,
    _normalize_public_vid,
    _parse_timestamp,
    _public_student_claim_id,
    _public_vid_url,
    _safe_slug,
    _shop_url_for_creator,
    _sign_claim,
    _text_width,
    _to_upload_url,
)

def _public_vid_for_qr(qr: dict[str, Any], school: dict[str, Any] | None = None) -> str:
    metadata = dict(qr.get("metadata") or {})
    stored = _normalize_public_vid(metadata.get("public_claim_id"))
    if stored.startswith("V-"):
        return stored
    resolved_school = school or get_school(str(qr.get("school_id") or "")) or {}
    return _public_student_claim_id(
        school_code=str(resolved_school.get("school_code") or qr.get("school_id") or ""),
        serial=str(qr.get("display_serial") or ""),
        qr_id=str(qr.get("qr_id") or ""),
    )

def _find_student_qr_by_vid(vid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = _normalize_public_vid(vid)
    if not normalized:
        return {}, {}
    conn = get_conn()
    match = re.match(r"^V-([A-Z0-9]+)-([A-Z0-9]+)$", normalized)
    rows = []
    if match:
        school_code, tail = match.groups()
        rows = conn.execute(
            """
            SELECT q.*
            FROM student_qr_codes q
            LEFT JOIN schools s ON s.school_id = q.school_id
            WHERE UPPER(COALESCE(s.school_code, q.school_id)) = UPPER(?)
              AND (
                UPPER(q.display_serial) LIKE UPPER(?)
                OR UPPER(q.qr_id) LIKE UPPER(?)
              )
            ORDER BY q.id DESC
            LIMIT 16
            """,
            (school_code, f"%{tail}", f"%{tail}"),
        ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT *
            FROM student_qr_codes
            ORDER BY id DESC
            LIMIT 800
            """
        ).fetchall()
    for row in rows:
        qr = {
            "id": int(row["id"]),
            "qr_id": row["qr_id"] or "",
            "school_id": row["school_id"] or "",
            "issued_batch": row["issued_batch"] or "",
            "display_serial": row["display_serial"] or "",
            "claim_token": row["claim_token"] or "",
            "claim_signature": row["claim_signature"] or "",
            "claim_url": row["claim_url"] or "",
            "qr_code_url": row["qr_code_url"] or "",
            "card_image_url": row["card_image_url"] or "",
            "manifest_url": row["manifest_url"] or "",
            "status": row["status"] or "issued",
            "roster_mode": row["roster_mode"] or "anonymous",
            "bound_user_id": int(row["bound_user_id"] or 0),
            "bound_at": row["bound_at"] or "",
            "issued_at": row["issued_at"] or "",
            "expires_at": row["expires_at"] or "",
            "revoked_reason": row["revoked_reason"] or "",
            "prefilled": _load_json(row["prefilled_json"], {}),
            "metadata": _load_json(row["metadata_json"], {}),
        }
        school = get_school(str(qr.get("school_id") or "")) or {}
        if _normalize_public_vid(_public_vid_for_qr(qr, school)) == normalized:
            return qr, school
    return {}, {}

def _student_signup_url(qr_id: str, *, claim: str = "", sig: str = "", error: str = "") -> str:
    qr = get_student_qr_code(qr_id) if qr_id else {}
    school = get_school(str(qr.get("school_id") or "")) if qr else {}
    vid = _public_vid_for_qr(qr, school) if qr and school else ""
    base = f"{SITE_URL.rstrip('/')}/?auth=register"
    if vid:
        base += f"&student_id={quote(vid)}"
    if qr_id:
        base += f"&qr_id={quote(str(qr_id or ''))}"
    if error:
        base += f"&error={quote(str(error))}"
    return base

def _find_creator_user_by_code(vid: str):
    conn = get_conn()
    for candidate in _creator_code_candidates(vid):
        row = conn.execute(
            """
            SELECT id, name, creator_code
            FROM users
            WHERE UPPER(COALESCE(creator_code, '')) = UPPER(?)
            LIMIT 1
            """,
            (candidate,),
        ).fetchone()
        if row:
            return row
    return None

def resolve_student_qr_scan_destination(qr_id: str, *, claim_token: str, signature: str) -> dict[str, Any]:
    qr = get_student_qr_code(qr_id)
    if not qr:
        raise ValueError("QR card not found")
    expected = _sign_claim(qr_id, claim_token)
    if signature != expected or claim_token != str(qr.get("claim_token") or ""):
        raise ValueError("Invalid claim signature")
    school = get_school(str(qr.get("school_id") or "")) or {}
    vid = _public_vid_for_qr(qr, school)
    status = str(qr.get("status") or "")
    if status == "bound":
        create_student_scan_event(
            event_type="public_vid_scan",
            qr_id=str(qr.get("qr_id") or ""),
            user_id=int(qr.get("bound_user_id") or 0),
            school_id=str(qr.get("school_id") or ""),
            event_payload={"vid": vid, "target": "public_vid"},
        )
        return {"target": "public_vid", "url": _public_vid_url(vid), "vid": vid}
    if status == "revoked":
        raise ValueError("This student card has been revoked")
    expires_at = _parse_timestamp(str(qr.get("expires_at") or ""))
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise ValueError("This student card has expired")
    create_student_scan_event(
        event_type="claim_auth_redirect",
        qr_id=str(qr.get("qr_id") or ""),
        school_id=str(qr.get("school_id") or ""),
        event_payload={"vid": vid, "target": "auth_register"},
    )
    return {
        "target": "auth_register",
        "url": _student_signup_url(qr_id),
        "vid": vid,
    }

def _load_public_creator_activity(user_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = get_conn()
    accounts: list[dict[str, Any]] = []
    submissions: list[dict[str, Any]] = []
    if not user_id:
        return accounts, submissions

    account_rows = conn.execute(
        """
        SELECT platform, handle, verified, verified_at
        FROM user_social_accounts
        WHERE user_id=? AND verified=1
        ORDER BY verified_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    for row in account_rows:
        platform = str(row["platform"] or "").strip().lower()
        handle = str(row["handle"] or "").strip()
        accounts.append(
            {
                "platform": platform,
                "handle": handle,
                "profile_url": build_profile_url(platform, handle) if platform and handle else "",
                "verified": bool(int(row["verified"] or 0)),
                "verified_at": row["verified_at"] or "",
            }
        )

    submission_rows = conn.execute(
        """
        SELECT id, created_at, platform, url, extracted_handle, title,
               detection_status, product_series, product_label,
               final_score, overall_score, views, likes, comments, shares,
               points_awarded, video_path
        FROM submissions
        WHERE user_id=?
          AND LOWER(COALESCE(detection_status, '')) NOT IN ('rejected', 'failed', 'prefilter_rejected', 'error')
        ORDER BY created_at DESC, id DESC
        LIMIT 18
        """,
        (user_id,),
    ).fetchall()
    for row in submission_rows:
        submission_id = int(row["id"])
        status = str(row["detection_status"] or "").strip().lower()
        video_path = str(row["video_path"] or "").strip()
        is_public_media = status in {"confirmed", "approved"}
        submissions.append(
            {
                "id": submission_id,
                "created_at": row["created_at"] or "",
                "platform": row["platform"] or "",
                "url": row["url"] or "",
                "media_url": f"/api/submissions/{submission_id}/video" if video_path and is_public_media else "",
                "poster_url": f"/api/submissions/{submission_id}/poster" if video_path and is_public_media else "",
                "handle": row["extracted_handle"] or "",
                "title": row["title"] or f"Via submission #{submission_id}",
                "status": row["detection_status"] or "",
                "product_series": row["product_series"] or "",
                "product_label": row["product_label"] or "",
                "score": int(row["overall_score"] or row["final_score"] or 0),
                "views": int(row["views"] or 0),
                "likes": int(row["likes"] or 0),
                "comments": int(row["comments"] or 0),
                "shares": int(row["shares"] or 0),
                "points": int(row["points_awarded"] or 0),
            }
        )
    return accounts, submissions

def _public_creator_profile_payload(*, public_vid: str, user: Any, claim_status: str, school: dict[str, Any] | None = None, signup_url: str = "") -> dict[str, Any]:
    user_id = int(user["id"]) if user else 0
    creator_code = str(user["creator_code"] or public_vid).strip() if user else ""
    profile_vid = creator_code or public_vid
    accounts, submissions = _load_public_creator_activity(user_id)
    via_url = _creator_code_path_url(profile_vid) if creator_code else _public_vid_url(profile_vid)

    return {
        "status": "success",
        "vid": profile_vid,
        "claim_status": claim_status,
        "is_bound": bool(user),
        "school": {
            "school_id": (school or {}).get("school_id") or "",
            "school_code": (school or {}).get("school_code") or "",
            "school_name": (school or {}).get("school_name") or "",
        },
        "creator": {
            "id": user_id,
            "name": user["name"] or "" if user else "",
            "creator_code": creator_code,
        },
        "links": {
            "shop": _shop_url_for_creator(creator_code),
            "via": via_url,
            "signup": signup_url,
            "qr": f"/api/public/vid/{quote(profile_vid)}/qr.png",
            "share_card": f"/api/public/vid/{quote(profile_vid)}/share-card",
            "apple_wallet": f"/api/public/vid/{quote(profile_vid)}/apple-wallet",
        },
        "accounts": accounts,
        "submissions": submissions,
    }

def build_public_vid_profile(vid: str) -> dict[str, Any]:
    qr, school = _find_student_qr_by_vid(vid)
    if not qr:
        creator_user = _find_creator_user_by_code(vid)
        if creator_user:
            creator_code = str(creator_user["creator_code"] or vid).strip()
            return _public_creator_profile_payload(
                public_vid=creator_code,
                user=creator_user,
                claim_status="creator",
                school={
                    "school_id": "",
                    "school_code": "VIA",
                    "school_name": "Viltrox Creator",
                },
                signup_url=f"{SITE_URL.rstrip('/')}/?auth=register&ref={quote(creator_code)}",
            )

        public_vid = _normalize_public_vid(vid)
        if not public_vid:
            raise ValueError("VID not found")
        match = re.match(r"^V-([A-Z0-9]+)-", public_vid)
        school_code = match.group(1) if match else ""
        signup_url = f"{SITE_URL.rstrip('/')}/?auth=register&student_id={quote(public_vid)}"
        return {
            "status": "success",
            "vid": public_vid,
            "claim_status": "unissued",
            "is_bound": False,
            "school": {
                "school_id": "",
                "school_code": school_code,
                "school_name": school_code,
            },
            "creator": {
                "id": 0,
                "name": "",
                "creator_code": "",
            },
            "links": {
                "shop": _shop_url_for_creator(""),
                "via": _public_vid_url(public_vid),
                "signup": signup_url,
                "qr": f"/api/public/vid/{quote(public_vid)}/qr.png",
                "share_card": f"/api/public/vid/{quote(public_vid)}/share-card",
                "apple_wallet": f"/api/public/vid/{quote(public_vid)}/apple-wallet",
            },
            "accounts": [],
            "submissions": [],
        }
    public_vid = _public_vid_for_qr(qr, school)
    user_id = int(qr.get("bound_user_id") or 0)
    conn = get_conn()
    user = conn.execute("SELECT id, name, creator_code FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
    creator_code = str(user["creator_code"] or "").strip() if user else ""
    accounts, submissions = _load_public_creator_activity(user_id if user else 0)

    return {
        "status": "success",
        "vid": public_vid,
        "claim_status": qr.get("status") or "issued",
        "is_bound": bool(user),
        "school": {
            "school_id": school.get("school_id") or qr.get("school_id") or "",
            "school_code": school.get("school_code") or "",
            "school_name": school.get("school_name") or "",
        },
        "creator": {
            "id": int(user["id"]) if user else 0,
            "name": user["name"] or "" if user else "",
            "creator_code": creator_code,
        },
        "links": {
            "shop": _shop_url_for_creator(creator_code),
            "via": _public_vid_url(public_vid),
            "signup": _student_signup_url(str(qr.get("qr_id") or "")),
            "qr": f"/api/public/vid/{quote(public_vid)}/qr.png",
            "share_card": f"/api/public/vid/{quote(public_vid)}/share-card",
            "apple_wallet": f"/api/public/vid/{quote(public_vid)}/apple-wallet",
        },
        "accounts": accounts,
        "submissions": submissions,
    }

def build_public_vid_share_card(vid: str) -> dict[str, Any]:
    profile = build_public_vid_profile(vid)
    public_vid = str(profile.get("vid") or _normalize_public_vid(vid))
    creator = dict(profile.get("creator") or {})
    creator_name = str(creator.get("name") or public_vid).strip() or public_vid
    target_url = str((profile.get("links") or {}).get("via") or _public_vid_url(public_vid))

    qrcode, Image, ImageDraw, ImageFont = _load_pil_modules()
    preferred_template_path = PROJECT_ROOT / "frontend" / "public" / "mockups" / "vid-share-template-white.png"
    fallback_template_path = PROJECT_ROOT / "frontend" / "public" / "mockups" / "vid-share-template.png"
    template_path = preferred_template_path if preferred_template_path.exists() else fallback_template_path
    if template_path.exists():
        canvas = Image.open(template_path).convert("RGBA")
    else:
        width, height = 1054, 1492
        canvas = Image.new("RGBA", (width, height), color="white")
        fallback_draw = ImageDraw.Draw(canvas)
        fallback_draw.rectangle((0, 104, width, 1144), fill=(255, 255, 255, 255))

    width, height = canvas.size
    sx = width / 1054
    sy = height / 1492
    scale = min(sx, sy)

    def xy(x: float, y: float) -> tuple[int, int]:
        return (int(round(x * sx)), int(round(y * sy)))

    def box(x1: float, y1: float, x2: float, y2: float) -> tuple[int, int, int, int]:
        return (int(round(x1 * sx)), int(round(y1 * sy)), int(round(x2 * sx)), int(round(y2 * sy)))

    draw = ImageDraw.Draw(canvas)
    header_font = _font_or_default(ImageFont, max(20, int(38 * scale)), bold=True)
    header_small_font = _font_or_default(ImageFont, max(18, int(34 * scale)))
    vid_label = str(creator.get("creator_code") or public_vid).strip() or public_vid

    # Keep the supplied artwork intact. Only patch the dynamic slots:
    # header copy after the logo, creator code, and the QR block.
    draw.rectangle(box(82, 18, 735, 88), fill=(255, 255, 255, 255))
    draw.text(xy(86, 38), f"viltrox.official and {creator_name}", fill=(18, 18, 18, 255), font=header_font)

    draw.rectangle(box(880, 18, 1032, 88), fill=(255, 255, 255, 255))
    draw.text(xy(1008, 40), vid_label, fill=(18, 18, 18, 255), font=header_small_font, anchor="ra")

    qr = qrcode.QRCode(version=5, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=2)
    qr.add_data(target_url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    qr_size = max(190, int(round(248 * scale)))
    qr_image = qr_image.resize((qr_size, qr_size), resampling)
    qr_x, qr_y = xy(766, 1218)
    draw.rectangle(box(744, 1210, 1026, 1480), fill=(255, 255, 255, 255))
    canvas.alpha_composite(qr_image, (qr_x, qr_y))
    v_font = _font_or_default(ImageFont, max(48, int(74 * scale)), bold=True)
    v_w = _text_width(draw, "V", v_font)
    draw.text((int(qr_x + qr_size / 2 - v_w / 2), int(qr_y + qr_size * 0.36)), "V", fill=(18, 18, 18, 255), font=v_font)

    output_dir = UPLOAD_DIR / "student_cards" / "vid_share"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_slug(public_vid)}.png"
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)

    return {
        "status": "success",
        "vid": public_vid,
        "creator_code": vid_label,
        "path": str(output_path),
        "image_url": _to_upload_url(output_path),
        "target_url": target_url,
        "wallet_ready": False,
        "apple_wallet_url": f"/api/public/vid/{quote(public_vid)}/apple-wallet",
        "qr_url": f"/api/public/vid/{quote(public_vid)}/qr.png",
    }

def build_public_vid_qr_png(vid: str) -> dict[str, Any]:
    profile = build_public_vid_profile(vid)
    public_vid = str(profile.get("vid") or _normalize_public_vid(vid))
    target_url = str((profile.get("links") or {}).get("via") or _public_vid_url(public_vid))

    qrcode, Image, _ImageDraw, _ImageFont = _load_pil_modules()
    qr = qrcode.QRCode(version=5, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=18, border=4)
    qr.add_data(target_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    output_dir = UPLOAD_DIR / "student_cards" / "vid_qr"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_slug(public_vid)}.png"
    image.save(output_path, format="PNG", optimize=True)

    return {
        "status": "success",
        "vid": public_vid,
        "target_url": target_url,
        "image_url": _to_upload_url(output_path),
        "path": output_path,
    }
