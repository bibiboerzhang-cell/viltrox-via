"""Claim, signup, pass, and QR issuance actions for student identity."""
from __future__ import annotations

import base64
import csv
import io
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from app.core.config import IS_PRODUCTION, SITE_URL
from app.core.security import hash_password, invalidate_user_cache
from app.db.connection import get_conn, is_postgres_runtime
from app.db.repositories.student_identity import (
    bind_student_qr_code,
    count_student_verifications_for_school,
    create_student_identity_audit,
    create_student_qr_code,
    create_student_scan_event,
    create_student_verification,
    get_school,
    get_student_qr_code,
    get_student_verification_for_user,
    list_student_qr_codes,
    list_schools,
    list_student_scan_events,
    update_student_qr_code,
)
from app.db.repositories.users import creator_code_exists, generate_creator_code
from app.services.auth.service import build_login_payload
from app.services.student_identity_common import (
    STUDENT_COMMISSION_RATE,
    STUDENT_PASS_TTL_SEC,
    _csv_rows_from_text,
    _derive_student_display_name,
    _ensure_student_dirs,
    _is_creator_code_conflict,
    _load_pil_modules,
    _normalize_public_vid,
    _normalize_school_student_id,
    _parse_timestamp,
    _public_student_claim_id,
    _render_qr_png,
    _render_student_card,
    _sign_claim,
    _sign_pass_token,
    _to_upload_url,
    _utcnow,
    _validate_student_email_domain,
)
from app.services.student_identity_defaults import (
    _school_from_student_id_code,
    ensure_student_identity_registry_defaults,
    ensure_student_school_defaults,
)
from app.services.student_identity_public import (
    _find_student_qr_by_vid,
    _public_vid_for_qr,
    _student_signup_url,
)

def resolve_student_identity_code(student_id_code: str) -> dict[str, Any]:
    code = str(student_id_code or "").strip().upper()
    if not code:
        return {}
    normalized_vid = _normalize_public_vid(code)
    if normalized_vid.startswith("V-"):
        qr, school = _find_student_qr_by_vid(normalized_vid)
        if qr and school and str(qr.get("status") or "") != "revoked":
            return {
                "id": 0,
                "student_id_code": _public_vid_for_qr(qr, school),
                "school_id": school.get("school_id") or "",
                "full_name": "",
                "major": "",
                "year_label": str(datetime.now(timezone.utc).year),
                "status": "active" if str(qr.get("status") or "") != "bound" else "claimed",
                "source": "vid_qr",
                "qr_id": qr.get("qr_id") or "",
                "bound_user_id": int(qr.get("bound_user_id") or 0),
                "claimed_at": qr.get("bound_at") or "",
                "metadata_json": json.dumps(
                    {"vid": normalized_vid, "display_serial": qr.get("display_serial") or ""},
                    ensure_ascii=False,
                ),
            }
    ensure_student_identity_registry_defaults()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT * FROM student_identity_registry
        WHERE UPPER(student_id_code)=UPPER(?)
        LIMIT 1
        """,
        (code,),
    ).fetchone()
    if row:
        return dict(row)
    if IS_PRODUCTION:
        return {}
    school = _school_from_student_id_code(code)
    if not school:
        return {}
    return {
        "id": 0,
        "student_id_code": code,
        "school_id": school.get("school_id") or "",
        "full_name": "",
        "major": "",
        "year_label": code.split("-", 2)[1] if code.count("-") >= 1 else "",
        "status": "active",
        "source": "school_code_fallback",
        "bound_user_id": 0,
        "claimed_at": "",
        "metadata_json": "{}",
    }

def claim_student_identity_for_user(
    *,
    user_id: int,
    student_id_code: str,
    verified_by: str = "auth_register",
) -> dict[str, Any]:
    match = resolve_student_identity_code(student_id_code)
    if not match:
        raise ValueError("VID not recognized")
    bound_user_id = int(match.get("bound_user_id") or 0)
    if bound_user_id and bound_user_id != int(user_id):
        raise ValueError("VID is already linked to another account")
    school = get_school(str(match.get("school_id") or ""))
    if not school:
        raise ValueError("VID did not resolve to a known school")

    proof = {
        "source": str(match.get("source") or "seed"),
        "registry_id": int(match.get("id") or 0),
        "qr_id": str(match.get("qr_id") or "").strip(),
        "vid": _normalize_public_vid(match.get("student_id_code") or ""),
        "full_name": str(match.get("full_name") or "").strip(),
        "major": str(match.get("major") or "").strip(),
        "year": str(match.get("year_label") or "").strip(),
    }
    verification = create_student_verification(
        user_id=int(user_id),
        school_id=str(school.get("school_id") or ""),
        student_id_code=str(match.get("student_id_code") or "").strip().upper(),
        verification_method="student_id",
        verification_proof=proof,
        status="active",
        commission_rate_override=STUDENT_COMMISSION_RATE,
        verified_by=verified_by,
        expires_at="",
    )

    if int(match.get("id") or 0) > 0:
        conn = get_conn()
        now = _utcnow()
        conn.execute(
            """
            UPDATE student_identity_registry
            SET bound_user_id=?, claimed_at=?, status='claimed', updated_at=?
            WHERE id=?
            """,
            (int(user_id), now, now, int(match["id"])),
        )
        conn.commit()
    if proof["qr_id"]:
        bind_student_qr_code(proof["qr_id"], user_id=int(user_id))

    create_student_identity_audit(
        audit_type="student_id_linked",
        user_id=int(user_id),
        school_id=str(school.get("school_id") or ""),
        actor=verified_by,
        reason="student_id_register",
        payload={
            "student_id_code": verification.get("student_id_code") or str(match.get("student_id_code") or "").strip().upper(),
            "registry_id": int(match.get("id") or 0),
            "qr_id": proof["qr_id"],
            "vid": proof["vid"],
            "school_code": school.get("school_code") or "",
            "school_name": school.get("school_name") or "",
        },
    )
    invalidate_user_cache(int(user_id))
    return {
        "status": verification.get("status") or "active",
        "school_id": school.get("school_id") or "",
        "school_name": school.get("school_name") or "",
        "student_id_code": verification.get("student_id_code") or str(match.get("student_id_code") or "").strip().upper(),
        "vid": proof["vid"],
        "commission_rate_override": verification.get("commission_rate_override") or STUDENT_COMMISSION_RATE,
        "verification_method": verification.get("verification_method") or "student_id",
        "major": proof["major"],
        "year": proof["year"],
    }

def validate_student_identity_email(student_id_code: str, email: str) -> dict[str, Any]:
    match = resolve_student_identity_code(student_id_code)
    if not match:
        raise ValueError("VID not recognized")
    school = get_school(str(match.get("school_id") or ""))
    if not school:
        raise ValueError("VID did not resolve to a known school")
    email_domain, allowed_domains = _validate_student_email_domain(str(email or "").lower().strip(), school)
    return {
        "school_id": school.get("school_id") or "",
        "school_code": school.get("school_code") or "",
        "email_domain": email_domain,
        "allowed_domains": allowed_domains,
    }

def create_student_qr_batch(
    *,
    school_id: str,
    batch_name: str,
    count: int = 0,
    roster_csv: str = "",
    expires_days: int = 365 * 4,
    qr_only: bool = True,
) -> dict[str, Any]:
    ensure_student_school_defaults()
    school = get_school(school_id)
    if not school:
        raise ValueError("Unknown school_id")
    roster_rows = _csv_rows_from_text(roster_csv)
    roster_mode = "roster_bound" if roster_rows else "anonymous"
    total = len(roster_rows) if roster_rows else max(1, int(count or 1))
    batch_label = _safe_slug(batch_name or f"{school.get('school_code', 'school').lower()}-{datetime.now().strftime('%Y%m%d')}")
    dirs = _ensure_student_dirs(str(school.get("school_code") or "school"), batch_label)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=max(30, int(expires_days or 365)))).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows: list[dict[str, Any]] = []
    for idx in range(total):
        prefilled = dict(roster_rows[idx] if idx < len(roster_rows) else {})
        serial = f"{school['school_code']}-{datetime.now(timezone.utc).year}-{idx + 1:04d}"
        qr_id = f"{school['school_code'].lower()}_{secrets.token_hex(6)}"
        claim_token = secrets.token_urlsafe(12)
        signature = _sign_claim(qr_id, claim_token)
        claim_url = f"{SITE_URL.rstrip('/')}/r/{quote(qr_id)}?claim={quote(claim_token)}&sig={quote(signature)}"
        public_claim_id = _public_student_claim_id(
            school_code=str(school.get("school_code") or ""),
            serial=serial,
            qr_id=qr_id,
        )
        qr_path = dirs["qr"] / f"{serial}.png"
        _render_qr_png(claim_url, qr_path)
        card_image_url = _to_upload_url(qr_path)
        if not qr_only:
            card_path = dirs["cards"] / f"{serial}.png"
            _render_student_card(
                school=school,
                qr_path=qr_path,
                card_path=card_path,
                serial=serial,
                prefilled={**prefilled, "issued_batch": batch_label},
            )
            card_image_url = _to_upload_url(card_path)
        record = create_student_qr_code(
            qr_id=qr_id,
            school_id=school["school_id"],
            issued_batch=batch_label,
            display_serial=serial,
            claim_token=claim_token,
            claim_signature=signature,
            claim_url=claim_url,
            qr_code_url=_to_upload_url(qr_path),
            card_image_url=card_image_url,
            manifest_url="",
            roster_mode=roster_mode,
            expires_at=expires_at,
            prefilled=prefilled,
            metadata={
                "serial_index": idx + 1,
                "asset_mode": "qr_only" if qr_only else "qr_plus_card",
                "public_claim_id": public_claim_id,
            },
        )
        create_student_identity_audit(
            audit_type="issued",
            qr_id=record["qr_id"],
            school_id=school["school_id"],
            actor="system_batch",
            reason=f"batch:{batch_label}",
            payload={
                "display_serial": serial,
                "public_claim_id": public_claim_id,
                "asset_mode": "qr_only" if qr_only else "qr_plus_card",
                "roster_mode": roster_mode,
            },
        )
        rows.append(record)

    manifest_path = dirs["base"] / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["public_id", "qr_id", "school_id", "serial", "claim_url", "qr_code_url", "status", "name", "email", "major", "year"])
        for row in rows:
            prefilled = dict(row.get("prefilled") or {})
            public_id = _public_student_claim_id(
                school_code=str(school.get("school_code") or row.get("school_id") or ""),
                serial=str(row.get("display_serial") or ""),
                qr_id=str(row.get("qr_id") or ""),
            )
            writer.writerow([
                public_id,
                row.get("qr_id"),
                row.get("school_id"),
                row.get("display_serial"),
                row.get("claim_url"),
                row.get("qr_code_url"),
                row.get("status"),
                prefilled.get("name", ""),
                prefilled.get("email", ""),
                prefilled.get("major", ""),
                prefilled.get("year", ""),
            ])

    printable_path = dirs["base"] / "printable.html"
    printable_path.write_text(
        "\n".join(
            [
                "<!doctype html><html><head><meta charset='utf-8'><title>Student Cards</title><style>",
                "body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f3f4f6;margin:0;padding:24px}",
                ".grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}",
                ".card{background:#fff;border:1px solid #e5e7eb;border-radius:20px;padding:18px}",
                ".card img{width:100%;display:block;border-radius:16px}",
                ".meta{margin-top:10px;font-size:12px;color:#6b7280}",
                "</style></head><body>",
                f"<h1>{school['school_name']} · {batch_label}</h1>",
                "<div class='grid'>",
                *[
                    f"<div class='card'><img src='{row['qr_code_url']}' alt='{row['display_serial']}'><div class='meta'>{_public_student_claim_id(school_code=str(school.get('school_code') or row.get('school_id') or ''), serial=str(row.get('display_serial') or ''), qr_id=str(row.get('qr_id') or ''))}</div></div>"
                    for row in rows
                ],
                "</div></body></html>",
            ]
        ),
        encoding="utf-8",
    )
    manifest_url = _to_upload_url(manifest_path)
    printable_url = _to_upload_url(printable_path)
    for row in rows:
        update_student_qr_code(row["qr_id"], manifest_url=manifest_url)

    return {
        "school": school,
        "batch_name": batch_label,
        "roster_mode": roster_mode,
        "asset_mode": "qr_only" if qr_only else "qr_plus_card",
        "count": total,
        "manifest_url": manifest_url,
        "printable_url": printable_url,
        "items": list_student_qr_codes(school_id=school["school_id"], batch_name=batch_label, limit=max(50, total + 10)),
    }

def _validate_static_claim(qr_id: str, claim_token: str, signature: str) -> dict[str, Any]:
    qr = get_student_qr_code(qr_id)
    if not qr:
        raise ValueError("QR card not found")
    expected = _sign_claim(qr_id, claim_token)
    if signature != expected or claim_token != str(qr.get("claim_token") or ""):
        raise ValueError("Invalid claim signature")
    if str(qr.get("status") or "") == "bound":
        raise ValueError("This student card is already bound")
    if str(qr.get("status") or "") == "revoked":
        raise ValueError("This student card has been revoked")
    expires_at = _parse_timestamp(str(qr.get("expires_at") or ""))
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise ValueError("This student card has expired")
    school = get_school(str(qr.get("school_id") or ""))
    if not school:
        raise ValueError("School configuration is missing")
    return {"qr": qr, "school": school}

def get_student_claim_metadata(qr_id: str, *, claim_token: str, signature: str, log_event: bool = True) -> dict[str, Any]:
    resolved = _validate_static_claim(qr_id, claim_token, signature)
    qr = resolved["qr"]
    school = resolved["school"]
    if log_event:
        create_student_scan_event(
            event_type="claim_lookup",
            qr_id=qr["qr_id"],
            school_id=school["school_id"],
            event_payload={"status": qr["status"], "batch": qr.get("issued_batch") or ""},
        )
    return {
        "qr_id": qr["qr_id"],
        "school": school,
        "status": qr["status"],
        "display_serial": qr.get("display_serial") or "",
        "public_claim_id": _public_student_claim_id(
            school_code=str(school.get("school_code") or qr.get("school_id") or ""),
            serial=str(qr.get("display_serial") or ""),
            qr_id=str(qr.get("qr_id") or ""),
        ),
        "claim_url": qr.get("claim_url") or "",
        "prefilled": qr.get("prefilled") or {},
        "requirements": {
            "student_id": True,
            "student_email": True,
            "student_email_domains": _student_email_domains_for_school(school),
        },
        "expires_at": qr.get("expires_at") or "",
        "card_image_url": qr.get("card_image_url") or "",
    }

def _generate_student_id_code(school: dict[str, Any], *, year: int | str | None = None) -> str:
    display_year = str(year or datetime.now(timezone.utc).year).strip()[:4] or str(datetime.now(timezone.utc).year)
    sequence = count_student_verifications_for_school(str(school.get("school_id") or "")) + 1
    return f"{str(school.get('school_code') or 'SCH').upper()}-{display_year}-{sequence:04d}"

def signup_student_from_qr(
    *,
    qr_id: str,
    claim_token: str,
    signature: str,
    email: str,
    password: str,
    name: str = "",
    student_id: str = "",
    major: str = "",
    year: str = "",
) -> dict[str, Any]:
    email_clean = str(email or "").lower().strip()
    if email_clean.count("@") != 1:
        raise ValueError("Invalid email")
    if len(str(password or "")) < 6:
        raise ValueError("Password must be at least 6 characters")

    resolved = _validate_static_claim(qr_id, claim_token, signature)
    qr = resolved["qr"]
    school = resolved["school"]
    prefilled = dict(qr.get("prefilled") or {})
    profile_major = str(major or prefilled.get("major") or "").strip()
    profile_year = str(year or prefilled.get("year") or datetime.now(timezone.utc).year).strip()
    school_student_id = _normalize_school_student_id(
        student_id
        or prefilled.get("school_student_id")
        or prefilled.get("student_id")
        or prefilled.get("id")
        or ""
    )
    email_domain, allowed_domains = _validate_student_email_domain(email_clean, school)
    display_name = _derive_student_display_name(email=email_clean, name=name, prefilled=prefilled)
    public_vid = _public_vid_for_qr(qr, school)

    conn = get_conn()
    existing = conn.execute("SELECT * FROM users WHERE email=?", (email_clean,)).fetchone()
    if existing:
        raise ValueError("Email already registered")
    now = _utcnow()
    sql = """
        INSERT INTO users
        (created_at, email, password_hash, name, creator_code, status, role, email_verified)
        VALUES (?,?,?,?,?,?,?,?)
    """
    user_id = 0
    creator_code = ""
    for attempt in range(6):
        creator_code = generate_creator_code(conn, offset=attempt)
        if creator_code_exists(conn, creator_code):
            continue
        params = (
            now,
            email_clean,
            hash_password(password),
            display_name,
            creator_code,
            "approved",
            "creator",
            0,
        )
        try:
            if is_postgres_runtime():
                cur = conn.execute(sql + " RETURNING id", params)
                row = cur.fetchone()
                user_id = int(row["id"]) if row else 0
            else:
                cur = conn.execute(sql, params)
                user_id = int(cur.lastrowid or 0)
            break
        except Exception as exc:
            if _is_creator_code_conflict(exc):
                conn.rollback()
                continue
            raise
    if not user_id:
        raise RuntimeError("Could not allocate a unique creator_code")
    conn.commit()

    student_id_code = _generate_student_id_code(school, year=profile_year)
    verification = create_student_verification(
        user_id=user_id,
        school_id=school["school_id"],
        student_id_code=student_id_code,
        verification_method="qr_scan",
        verification_proof={
            "qr_id": qr["qr_id"],
            "vid": public_vid,
            "public_claim_id": public_vid,
            "issued_batch": qr.get("issued_batch") or "",
            "school_student_id": school_student_id,
            "student_email_domain": email_domain,
            "accepted_email_domains": allowed_domains,
            "major": profile_major,
            "year": profile_year,
            "prefilled": prefilled,
        },
        status="active",
        commission_rate_override=STUDENT_COMMISSION_RATE,
        verified_by="student_qr_signup",
        expires_at=str(qr.get("expires_at") or ""),
    )
    bind_student_qr_code(qr_id, user_id=user_id)
    create_student_scan_event(
        event_type="signup_bound",
        qr_id=qr["qr_id"],
        user_id=user_id,
        school_id=school["school_id"],
        event_payload={
            "student_id_code": student_id_code,
            "vid": public_vid,
            "school_student_id": school_student_id,
            "student_email_domain": email_domain,
            "creator_code": creator_code,
        },
    )
    create_student_identity_audit(
        audit_type="signup_bound",
        qr_id=qr["qr_id"],
        user_id=user_id,
        school_id=school["school_id"],
        actor="student_signup",
        reason="qr_claim_signup",
        payload={
            "student_id_code": student_id_code,
            "vid": public_vid,
            "public_claim_id": public_vid,
            "school_student_id": school_student_id,
            "student_email_domain": email_domain,
            "accepted_email_domains": allowed_domains,
            "creator_code": creator_code,
            "major": profile_major,
            "year": profile_year,
        },
    )
    invalidate_user_cache(user_id)
    user = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    payload = build_login_payload(user)
    payload["student"] = {
        "status": verification.get("status") or "active",
        "school_id": school["school_id"],
        "school_name": school["school_name"],
        "student_id_code": student_id_code,
        "vid": public_vid,
        "school_student_id": school_student_id,
        "expires_at": verification.get("expires_at") or "",
        "commission_rate_override": verification.get("commission_rate_override") or STUDENT_COMMISSION_RATE,
    }
    payload["claim"] = {
        "qr_id": qr["qr_id"],
        "display_serial": qr.get("display_serial") or "",
        "vid": public_vid,
        "school_id": school["school_id"],
    }
    return payload

def build_student_pass(user_id: int) -> dict[str, Any]:
    verification = get_student_verification_for_user(int(user_id))
    if not verification or str(verification.get("status") or "") != "active":
        raise ValueError("Student pass is only available for active student creators")
    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp()) // STUDENT_PASS_TTL_SEC
    expires_at = datetime.fromtimestamp((bucket + 1) * STUDENT_PASS_TTL_SEC, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = base64.urlsafe_b64encode(
        f"{int(user_id)}:{verification['school_id']}:{verification['student_id_code']}:{bucket}".encode()
    ).decode().rstrip("=")
    signature = _sign_pass_token(int(user_id), verification["school_id"], bucket)
    pass_url = f"{SITE_URL.rstrip('/')}/student-pass/check-in?token={quote(token)}&sig={quote(signature)}"

    png_bytes = io.BytesIO()
    qrcode, _Image, _ImageDraw, _ImageFont = _load_pil_modules()
    qr = qrcode.QRCode(version=4, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(pass_url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(png_bytes, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(png_bytes.getvalue()).decode()

    create_student_scan_event(
        event_type="dynamic_pass_issued",
        user_id=int(user_id),
        school_id=verification["school_id"],
        event_payload={"expires_at": expires_at, "student_id_code": verification["student_id_code"]},
    )
    return {
        "token": token,
        "signature": signature,
        "expires_at": expires_at,
        "pass_url": pass_url,
        "qr_data_uri": data_uri,
        "student_id_code": verification["student_id_code"],
        "school_id": verification["school_id"],
    }

def consume_student_pass(*, token: str, signature: str, location: str = "", context: str = "event_checkin") -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(str(token or "") + "==").decode()
    user_id_text, school_id, student_id_code, bucket_text = raw.split(":", 3)
    bucket = int(bucket_text)
    expected = _sign_pass_token(int(user_id_text), school_id, bucket)
    if signature != expected:
        raise ValueError("Invalid pass signature")
    now_bucket = int(datetime.now(timezone.utc).timestamp()) // STUDENT_PASS_TTL_SEC
    if bucket < now_bucket:
        raise ValueError("Dynamic pass has expired")
    prior = [
        item for item in list_student_scan_events(user_id=int(user_id_text), limit=200)
        if str((item.get("event_payload") or {}).get("pass_token") or "") == str(token or "")
        and str(item.get("event_type") or "") == "dynamic_pass_checkin"
    ]
    if prior:
        raise ValueError("Dynamic pass has already been used")
    create_student_scan_event(
        event_type="dynamic_pass_checkin",
        user_id=int(user_id_text),
        school_id=school_id,
        location=location,
        event_payload={"pass_token": token, "context": context, "student_id_code": student_id_code},
    )
    return {
        "status": "checked_in",
        "user_id": int(user_id_text),
        "school_id": school_id,
        "student_id_code": student_id_code,
        "context": context,
    }

def revoke_student_qr(qr_id: str, *, reason: str = "revoked_by_admin") -> dict[str, Any]:
    qr = get_student_qr_code(qr_id)
    if not qr:
        raise ValueError("QR card not found")
    if str(qr.get("status") or "") == "bound":
        raise ValueError("Bound cards cannot be revoked in place")
    updated = update_student_qr_code(qr_id, status="revoked", revoked_reason=reason)
    create_student_identity_audit(
        audit_type="revoked",
        qr_id=qr_id,
        school_id=str(qr.get("school_id") or ""),
        actor="admin",
        reason=str(reason or "revoked_by_admin"),
        payload={"display_serial": qr.get("display_serial") or "", "issued_batch": qr.get("issued_batch") or ""},
    )
    return updated

def reissue_student_qr(qr_id: str) -> dict[str, Any]:
    qr = get_student_qr_code(qr_id)
    if not qr:
        raise ValueError("QR card not found")
    if str(qr.get("status") or "") == "bound":
        raise ValueError("Bound cards cannot be reissued")
    school = get_school(str(qr.get("school_id") or ""))
    dirs = _ensure_student_dirs(str(school.get("school_code") or "school"), str(qr.get("issued_batch") or "batch"))
    claim_token = secrets.token_urlsafe(12)
    signature = _sign_claim(qr["qr_id"], claim_token)
    claim_url = f"{SITE_URL.rstrip('/')}/r/{quote(qr['qr_id'])}?claim={quote(claim_token)}&sig={quote(signature)}"
    public_claim_id = _public_student_claim_id(
        school_code=str(school.get("school_code") or qr.get("school_id") or ""),
        serial=str(qr.get("display_serial") or ""),
        qr_id=str(qr.get("qr_id") or ""),
    )
    qr_path = dirs["qr"] / f"{qr['display_serial']}.png"
    card_path = dirs["cards"] / f"{qr['display_serial']}.png"
    _render_qr_png(claim_url, qr_path)
    _render_student_card(
        school=school,
        qr_path=qr_path,
        card_path=card_path,
        serial=str(qr.get("display_serial") or ""),
        prefilled={**dict(qr.get("prefilled") or {}), "issued_batch": qr.get("issued_batch") or ""},
    )
    updated = update_student_qr_code(
        qr_id,
        status="issued",
        claim_token=claim_token,
        claim_signature=signature,
        claim_url=claim_url,
        qr_code_url=_to_upload_url(qr_path),
        card_image_url=_to_upload_url(card_path),
        revoked_reason="",
        metadata={**dict(qr.get("metadata") or {}), "public_claim_id": public_claim_id},
    )
    create_student_identity_audit(
        audit_type="reissued",
        qr_id=qr_id,
        school_id=str(qr.get("school_id") or ""),
        actor="admin",
        reason="reissue_qr_card",
        payload={"display_serial": qr.get("display_serial") or "", "public_claim_id": public_claim_id, "issued_batch": qr.get("issued_batch") or ""},
    )
    return updated
