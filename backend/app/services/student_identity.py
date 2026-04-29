"""
services/student_identity.py — QR-first student identity runtime
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.core.config import IS_PRODUCTION, PROJECT_ROOT, SHOPIFY_AFFILIATE_BASE_URL, SITE_URL, UPLOAD_DIR, JWT_SECRET
from app.core.security import hash_password, invalidate_user_cache
from app.db.connection import get_conn, is_postgres_runtime
from app.db.repositories.student_identity import (
    bind_student_qr_code,
    count_student_verifications_for_school,
    create_or_update_school,
    create_student_identity_audit,
    create_student_qr_code,
    create_student_scan_event,
    create_student_verification,
    get_school,
    get_student_qr_code,
    get_student_verification_for_user,
    list_student_identity_audit,
    list_schools,
    list_student_qr_codes,
    list_student_scan_events,
    update_student_qr_code,
)
from app.db.repositories.users import creator_code_exists, generate_creator_code
from app.services.auth.service import build_login_payload
from app.services.verification.viltrox_official import build_profile_url

STUDENT_COMMISSION_RATE = 0.10
STUDENT_PASS_TTL_SEC = 60

_STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,62}[A-Za-z0-9]$")
_STUDENT_EMAIL_DOMAIN_RULES: dict[str, tuple[str, ...]] = {
    "SCAD": ("student.scad.edu",),
    "SCAD_001": ("student.scad.edu",),
    "AFI": ("afi.edu", "student.afi.edu"),
    "AFI_001": ("afi.edu", "student.afi.edu"),
    "USC": ("usc.edu",),
    "USC_001": ("usc.edu",),
    "NYU": ("nyu.edu",),
    "NYU_TISCH_001": ("nyu.edu",),
    "NFTS": ("nfts.co.uk",),
    "NFTS_001": ("nfts.co.uk",),
    "BFA": ("bfa.edu.cn",),
    "BFA_001": ("bfa.edu.cn",),
}


def _is_creator_code_conflict(exc: Exception) -> bool:
    text = str(exc).lower()
    return "creator_code" in text and "unique" in text


def _load_json(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _clean_allowed_email_domain(domain: Any) -> str:
    text = str(domain or "").lower().strip().removeprefix("@")
    if not text:
        return ""
    if text.startswith("*."):
        return text
    return text.lstrip(".")


def _domains_from_school_metadata(school: dict[str, Any]) -> list[str]:
    metadata = dict(school.get("metadata") or {})
    domains: list[str] = []
    for key in ("student_email_domains", "email_domains", "allowed_email_domains"):
        raw = metadata.get(key)
        if isinstance(raw, str):
            domains.extend(part for part in re.split(r"[\s,;]+", raw) if part)
        elif isinstance(raw, (list, tuple, set)):
            domains.extend(str(part) for part in raw if str(part or "").strip())
    return [_clean_allowed_email_domain(item) for item in domains if _clean_allowed_email_domain(item)]


def _student_email_domains_for_school(school: dict[str, Any]) -> list[str]:
    metadata_domains = _domains_from_school_metadata(school)
    if metadata_domains:
        return sorted(dict.fromkeys(metadata_domains))

    lookup_tokens = [
        str(school.get("school_id") or ""),
        str(school.get("school_code") or ""),
        str(school.get("school_name") or ""),
    ]
    for token in lookup_tokens:
        normalized = re.sub(r"[^A-Z0-9_]+", "_", token.upper()).strip("_")
        if normalized in _STUDENT_EMAIL_DOMAIN_RULES:
            return list(_STUDENT_EMAIL_DOMAIN_RULES[normalized])
        first_word = normalized.split("_", 1)[0] if normalized else ""
        if first_word in _STUDENT_EMAIL_DOMAIN_RULES:
            return list(_STUDENT_EMAIL_DOMAIN_RULES[first_word])

    country = str(school.get("country") or "").strip().lower()
    if country in {"us", "usa", "united states", "united states of america"}:
        return ["*.edu"]
    return []


def _email_domain_matches(domain: str, allowed: str) -> bool:
    domain = domain.lower().strip()
    allowed = _clean_allowed_email_domain(allowed)
    if not allowed:
        return True
    if allowed.startswith("*."):
        suffix = allowed[1:]
        return domain.endswith(suffix)
    return domain == allowed or domain.endswith(f".{allowed}")


def _validate_student_email_domain(email_clean: str, school: dict[str, Any]) -> tuple[str, list[str]]:
    domain = email_clean.rsplit("@", 1)[1].lower().strip()
    allowed = _student_email_domains_for_school(school)
    if allowed and not any(_email_domain_matches(domain, item) for item in allowed):
        school_code = str(school.get("school_code") or school.get("school_name") or "school").strip()
        domain_hint = ", ".join(allowed)
        raise ValueError(f"Please use your {school_code} student email ({domain_hint})")
    return domain, allowed


def _normalize_school_student_id(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    if not text:
        raise ValueError("VID is required")
    if len(text) < 3 or len(text) > 64 or not _STUDENT_ID_RE.match(text):
        raise ValueError("VID must be 3-64 letters/numbers")
    return text


def _derive_student_display_name(*, email: str, name: str = "", prefilled: dict[str, Any] | None = None) -> str:
    candidate = str(name or (prefilled or {}).get("name") or "").strip()
    if len(candidate) >= 2:
        return candidate[:120]
    local_part = email.split("@", 1)[0]
    readable = " ".join(part.capitalize() for part in re.split(r"[._-]+", local_part) if part)
    if len(readable) >= 2:
        return readable[:120]
    return local_part[:120] or "Student Creator"


_PILOT_SCHOOL_THEMES: dict[str, dict[str, Any]] = {
    "AFI_001": {
        "school_code": "AFI",
        "school_name": "American Film Institute",
        "country": "USA",
        "region": "Los Angeles",
        "tier": "top",
        "partnership_status": "pilot",
        "visual_theme": {
            "primary_color": "#0A2463",
            "accent_color": "#D62828",
            "tagline": "Where the storytellers are made.",
        },
    },
    "USC_001": {
        "school_code": "USC",
        "school_name": "USC School of Cinematic Arts",
        "country": "USA",
        "region": "Los Angeles",
        "tier": "top",
        "partnership_status": "pilot",
        "visual_theme": {
            "primary_color": "#990000",
            "accent_color": "#FFC72C",
            "tagline": "Fight on, creators.",
        },
    },
    "NYU_TISCH_001": {
        "school_code": "NYU",
        "school_name": "NYU Tisch",
        "country": "USA",
        "region": "New York City",
        "tier": "high",
        "partnership_status": "pilot",
        "visual_theme": {
            "primary_color": "#57068C",
            "accent_color": "#111111",
            "tagline": "Make it in NYC.",
        },
    },
    "NFTS_001": {
        "school_code": "NFTS",
        "school_name": "National Film and Television School",
        "country": "UK",
        "region": "London",
        "tier": "high",
        "partnership_status": "pending",
        "visual_theme": {
            "primary_color": "#1B1F3B",
            "accent_color": "#E63946",
            "tagline": "Britain's leading film school.",
        },
    },
    "BFA_001": {
        "school_code": "BFA",
        "school_name": "Beijing Film Academy",
        "school_name_native": "北京电影学院",
        "country": "China",
        "region": "Beijing",
        "tier": "high",
        "partnership_status": "pending",
        "visual_theme": {
            "primary_color": "#C8102E",
            "accent_color": "#FFD700",
            "tagline": "中国电影的摇篮",
        },
    },
}

_STUDENT_ID_REGISTRY_SEEDS: tuple[dict[str, str], ...] = (
    {
        "student_id_code": "AFI-2026-0001",
        "school_id": "AFI_001",
        "full_name": "Alex Chen",
        "major": "Directing",
        "year_label": "2026",
    },
    {
        "student_id_code": "USC-2026-0007",
        "school_id": "USC_001",
        "full_name": "Mia Rodriguez",
        "major": "Cinematography",
        "year_label": "2026",
    },
    {
        "student_id_code": "NYU-2026-0012",
        "school_id": "NYU_TISCH_001",
        "full_name": "Jordan Park",
        "major": "Film & TV Production",
        "year_label": "2026",
    },
    {
        "student_id_code": "NFTS-2026-0003",
        "school_id": "NFTS_001",
        "full_name": "Hugo Clarke",
        "major": "Editing",
        "year_label": "2026",
    },
    {
        "student_id_code": "BFA-2026-0005",
        "school_id": "BFA_001",
        "full_name": "Lin Yue",
        "major": "Directing",
        "year_label": "2026",
    },
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    while "--" in text:
        text = text.replace("--", "-")
    return text.strip("-") or "batch"


def _sign_claim(qr_id: str, claim_token: str) -> str:
    payload = f"student-claim:{qr_id}:{claim_token}"
    return hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]


def _sign_pass_token(user_id: int, school_id: str, bucket: int) -> str:
    payload = f"student-pass:{int(user_id)}:{school_id}:{int(bucket)}"
    return hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]


def _load_pil_modules():
    try:
        import qrcode  # type: ignore
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised in runtime, not tests
        raise RuntimeError("QR asset generation requires qrcode + Pillow in the runtime environment") from exc
    return qrcode, Image, ImageDraw, ImageFont


def _to_upload_url(path: Path) -> str:
    relative = path.relative_to(UPLOAD_DIR)
    return "/uploads/" + relative.as_posix()


def _ensure_student_dirs(school_code: str, batch_name: str) -> dict[str, Path]:
    base = UPLOAD_DIR / "student_cards" / _safe_slug(school_code) / _safe_slug(batch_name)
    qr_dir = base / "qr"
    card_dir = base / "cards"
    qr_dir.mkdir(parents=True, exist_ok=True)
    card_dir.mkdir(parents=True, exist_ok=True)
    return {"base": base, "qr": qr_dir, "cards": card_dir}


def _render_qr_png(target_url: str, output_path: Path) -> None:
    qrcode, _Image, _ImageDraw, _ImageFont = _load_pil_modules()
    qr = qrcode.QRCode(version=5, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(target_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    image.save(output_path)


def _render_student_card(
    *,
    school: dict[str, Any],
    qr_path: Path,
    card_path: Path,
    serial: str,
    prefilled: dict[str, Any],
) -> None:
    _qrcode, Image, ImageDraw, ImageFont = _load_pil_modules()
    canvas = Image.new("RGB", (1080, 680), color="white")
    draw = ImageDraw.Draw(canvas)
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 72)
        body_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
        small_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
    except Exception:  # pragma: no cover
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    theme = dict(school.get("visual_theme") or {})
    primary = theme.get("primary_color") or "#111111"
    accent = theme.get("accent_color") or "#ff8f2a"
    draw.rounded_rectangle((28, 28, 1052, 652), radius=42, fill="#F8FAFC", outline=primary, width=4)
    draw.rounded_rectangle((46, 46, 1034, 634), radius=34, fill="#FFFFFF", outline="#E5E7EB", width=2)
    draw.text((74, 76), "V-OS STUDENT CREATOR", fill=primary, font=small_font)
    draw.text((74, 118), str(school.get("school_code") or ""), fill=accent, font=title_font)
    draw.text((74, 208), str(school.get("school_name") or ""), fill="#111111", font=body_font)
    draw.text((74, 264), str(theme.get("tagline") or "Student creator lane"), fill="#6B7280", font=small_font)
    public_claim_id = _public_student_claim_id(school_code=str(school.get("school_code") or ""), serial=serial)
    draw.text((74, 340), f"Viltrox ID: {public_claim_id}", fill="#111111", font=body_font)
    draw.text((74, 392), f"Batch: {prefilled.get('issued_batch') or ''}", fill="#374151", font=small_font)
    draw.text((74, 440), f"Name: {prefilled.get('name') or 'Open claim card'}", fill="#111111", font=small_font)
    draw.text((74, 478), f"Major: {prefilled.get('major') or 'Creator lane'}", fill="#111111", font=small_font)
    draw.text((74, 516), f"Year: {prefilled.get('year') or datetime.now().year}", fill="#111111", font=small_font)
    draw.text((74, 570), "Scan to claim your V-OS student account", fill=accent, font=body_font)

    qr_image = Image.open(qr_path).convert("RGB").resize((310, 310))
    canvas.paste(qr_image, (700, 180))
    draw.rounded_rectangle((684, 164, 1026, 506), radius=26, outline=accent, width=4)
    canvas.save(card_path)


def _font_or_default(ImageFont: Any, size: int, *, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_width(draw: Any, text: str, font: Any) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])
    except Exception:
        try:
            return int(draw.textlength(text, font=font))
        except Exception:
            return max(1, len(text) * 10)


def _cover_fit_image(Image: Any, source_path: Path, size: tuple[int, int]):
    image = Image.open(source_path).convert("RGBA")
    target_w, target_h = size
    scale = max(target_w / max(image.width, 1), target_h / max(image.height, 1))
    resized_w = max(target_w, int(round(image.width * scale)))
    resized_h = max(target_h, int(round(image.height * scale)))
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    image = image.resize((resized_w, resized_h), resampling)
    left = max(0, (resized_w - target_w) // 2)
    top = max(0, (resized_h - target_h) // 2)
    return image.crop((left, top, left + target_w, top + target_h))


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


def _csv_rows_from_text(roster_csv: str) -> list[dict[str, str]]:
    text = str(roster_csv or "").strip()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in reader]


def ensure_student_school_defaults() -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for school_id, payload in _PILOT_SCHOOL_THEMES.items():
        created.append(
            create_or_update_school(
                school_id=school_id,
                school_code=str(payload.get("school_code") or ""),
                school_name=str(payload.get("school_name") or ""),
                school_name_native=str(payload.get("school_name_native") or ""),
                country=str(payload.get("country") or ""),
                region=str(payload.get("region") or ""),
                school_type="film",
                tier=str(payload.get("tier") or "standard"),
                partnership_status=str(payload.get("partnership_status") or "pilot"),
                visual_theme=payload.get("visual_theme") or {},
                metadata={},
            )
        )
    return created


def _ensure_student_identity_registry_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_identity_registry (
                id BIGSERIAL PRIMARY KEY,
                student_id_code TEXT NOT NULL UNIQUE,
                school_id TEXT NOT NULL,
                full_name TEXT DEFAULT '',
                major TEXT DEFAULT '',
                year_label TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                source TEXT DEFAULT 'seed',
                bound_user_id BIGINT DEFAULT 0,
                claimed_at TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_identity_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id_code TEXT NOT NULL UNIQUE,
                school_id TEXT NOT NULL,
                full_name TEXT DEFAULT '',
                major TEXT DEFAULT '',
                year_label TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                source TEXT DEFAULT 'seed',
                bound_user_id INTEGER DEFAULT 0,
                claimed_at TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    conn.commit()


def ensure_student_identity_registry_defaults() -> list[dict[str, Any]]:
    ensure_student_school_defaults()
    _ensure_student_identity_registry_schema()
    conn = get_conn()
    now = _utcnow()
    for item in _STUDENT_ID_REGISTRY_SEEDS:
        conn.execute(
            """
            INSERT OR IGNORE INTO student_identity_registry (
                student_id_code, school_id, full_name, major, year_label, status,
                source, bound_user_id, claimed_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(item.get("student_id_code") or "").strip().upper(),
                str(item.get("school_id") or "").strip(),
                str(item.get("full_name") or "").strip(),
                str(item.get("major") or "").strip(),
                str(item.get("year_label") or "").strip(),
                "active",
                "seed",
                0,
                "",
                json.dumps({"seeded": True}, ensure_ascii=False),
                now,
                now,
            ),
        )
    conn.commit()
    rows = conn.execute(
        """
        SELECT * FROM student_identity_registry
        ORDER BY school_id ASC, student_id_code ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _school_from_student_id_code(student_id_code: str) -> dict[str, Any]:
    code = str(student_id_code or "").strip().upper()
    if not code:
        return {}
    school_code = code.split("-", 1)[0].strip()
    if not school_code:
        return {}
    for school in list_schools(limit=240):
        if str(school.get("school_code") or "").strip().upper() == school_code:
            return school
    return {}


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


def _school_stats(school_id: str) -> dict[str, int]:
    conn = get_conn()
    stats = {"issued": 0, "bound": 0, "active_students": 0}
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS issued_count,
            SUM(CASE WHEN status='bound' THEN 1 ELSE 0 END) AS bound_count
        FROM student_qr_codes
        WHERE school_id=?
        """,
        (str(school_id or "").strip(),),
    ).fetchone()
    if row:
        stats["issued"] = int(row["issued_count"] or 0)
        stats["bound"] = int(row["bound_count"] or 0)
    stats["active_students"] = count_student_verifications_for_school(school_id)
    return stats


def list_student_schools_with_stats(limit: int = 200) -> list[dict[str, Any]]:
    ensure_student_school_defaults()
    items: list[dict[str, Any]] = []
    for school in list_schools(limit=limit):
        items.append({**school, "stats": _school_stats(str(school.get("school_id") or ""))})
    return items


def _student_batch_progress(limit: int = 48) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            q.school_id,
            s.school_name,
            q.issued_batch,
            COUNT(*) AS issued_count,
            SUM(CASE WHEN q.status='bound' THEN 1 ELSE 0 END) AS activated_count,
            SUM(CASE WHEN q.status='revoked' THEN 1 ELSE 0 END) AS revoked_count,
            MAX(COALESCE(q.bound_at, q.issued_at)) AS last_activity_at,
            MAX(q.issued_at) AS last_issued_at
        FROM student_qr_codes q
        LEFT JOIN schools s ON s.school_id = q.school_id
        GROUP BY q.school_id, s.school_name, q.issued_batch
        ORDER BY last_activity_at DESC, q.issued_batch DESC
        LIMIT ?
        """,
        (max(12, int(limit)),),
    ).fetchall()
    return [
        {
            "school_id": row["school_id"] or "",
            "school_name": row["school_name"] or "",
            "batch_name": row["issued_batch"] or "",
            "issued_count": int(row["issued_count"] or 0),
            "activated_count": int(row["activated_count"] or 0),
            "revoked_count": int(row["revoked_count"] or 0),
            "pending_count": max(0, int(row["issued_count"] or 0) - int(row["activated_count"] or 0) - int(row["revoked_count"] or 0)),
            "activation_rate": round((int(row["activated_count"] or 0) / max(1, int(row["issued_count"] or 0))), 4),
            "last_activity_at": row["last_activity_at"] or "",
            "last_issued_at": row["last_issued_at"] or "",
        }
        for row in rows
    ]


def _student_school_funnels(limit: int = 24) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            s.school_id,
            s.school_name,
            COUNT(q.id) AS issued_count,
            SUM(CASE WHEN q.status='bound' THEN 1 ELSE 0 END) AS activated_count,
            SUM(CASE WHEN q.status='revoked' THEN 1 ELSE 0 END) AS revoked_count,
            COUNT(DISTINCT sv.user_id) AS active_students,
            MAX(COALESCE(se.created_at, q.bound_at, q.issued_at)) AS last_activity_at
        FROM schools s
        LEFT JOIN student_qr_codes q ON q.school_id = s.school_id
        LEFT JOIN student_verifications sv ON sv.school_id = s.school_id AND sv.status='active'
        LEFT JOIN student_scan_events se ON se.school_id = s.school_id
        GROUP BY s.school_id, s.school_name
        ORDER BY activated_count DESC, issued_count DESC, s.school_name ASC
        LIMIT ?
        """,
        (max(8, int(limit)),),
    ).fetchall()
    return [
        {
            "school_id": row["school_id"] or "",
            "school_name": row["school_name"] or "",
            "issued_count": int(row["issued_count"] or 0),
            "activated_count": int(row["activated_count"] or 0),
            "revoked_count": int(row["revoked_count"] or 0),
            "pending_count": max(0, int(row["issued_count"] or 0) - int(row["activated_count"] or 0) - int(row["revoked_count"] or 0)),
            "active_students": int(row["active_students"] or 0),
            "activation_rate": round((int(row["activated_count"] or 0) / max(1, int(row["issued_count"] or 0))), 4),
            "last_activity_at": row["last_activity_at"] or "",
        }
        for row in rows
    ]


def _recent_student_events(limit: int = 32) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            se.*,
            s.school_name,
            u.name AS user_name,
            u.creator_code
        FROM student_scan_events se
        LEFT JOIN schools s ON s.school_id = se.school_id
        LEFT JOIN users u ON u.id = se.user_id
        ORDER BY se.created_at DESC, se.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(_load_json(row["event_payload_json"], {}))
        items.append(
            {
                "event_key": row["event_key"] or "",
                "qr_id": row["qr_id"] or "",
                "user_id": int(row["user_id"] or 0),
                "school_id": row["school_id"] or "",
                "school_name": row["school_name"] or "",
                "event_type": row["event_type"] or "",
                "location": row["location"] or "",
                "event_payload": payload,
                "user_name": row["user_name"] or "",
                "creator_code": row["creator_code"] or "",
                "created_at": row["created_at"] or "",
            }
        )
    return items


def _recent_student_anomalies(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT qr_id, school_id, issued_batch, display_serial, status, revoked_reason, expires_at, issued_at
        FROM student_qr_codes
        WHERE status='revoked' OR revoked_reason<>''
        ORDER BY issued_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "qr_id": row["qr_id"] or "",
                "school_id": row["school_id"] or "",
                "batch_name": row["issued_batch"] or "",
                "display_serial": row["display_serial"] or "",
                "status": row["status"] or "",
                "reason": row["revoked_reason"] or "",
                "expires_at": row["expires_at"] or "",
                "issued_at": row["issued_at"] or "",
            }
        )
    return items


def build_student_funnel_snapshot(limit: int = 48) -> dict[str, Any]:
    return {
        "school_funnels": _student_school_funnels(limit=max(12, int(limit))),
        "batch_progress": _student_batch_progress(limit=max(12, int(limit))),
        "recent_events": _recent_student_events(limit=max(12, int(limit))),
        "recent_anomalies": _recent_student_anomalies(limit=max(8, int(limit // 2) or 8)),
        "recent_audit": list_student_identity_audit(limit=max(12, int(limit))),
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


def _public_student_claim_id(*, school_code: str = "", serial: str = "", qr_id: str = "") -> str:
    code = re.sub(r"[^A-Z0-9]+", "", str(school_code or "SCH").upper()) or "SCH"
    serial_match = re.search(r"(\d{3,})$", str(serial or ""))
    if serial_match:
        return f"V-{code}-{serial_match.group(1)[-4:]}"
    qr_tail = str(qr_id or "").split("_", 1)[-1].upper()
    qr_tail = re.sub(r"[^A-Z0-9]+", "", qr_tail)[:8] or "0000"
    return f"V-{code}-{qr_tail}"


def _normalize_public_vid(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip().upper())
    text = text.replace("_", "-")
    if not text:
        return ""
    if text.startswith("VID-"):
        text = "V-" + text[4:]
    if not text.startswith("V-") and re.match(r"^[A-Z0-9]+-\d{3,}$", text):
        text = f"V-{text}"
    return text


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


def _shop_url_for_creator(creator_code: str = "") -> str:
    base = str(SHOPIFY_AFFILIATE_BASE_URL or "https://viltrox.com/").strip() or "https://viltrox.com/"
    code = str(creator_code or "").strip()
    if not code:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}ref={quote(code)}"


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


def _public_vid_url(vid: str) -> str:
    return f"{SITE_URL.rstrip('/')}/vid/{quote(_normalize_public_vid(vid) or str(vid or ''))}"


def _creator_code_path_url(creator_code: str) -> str:
    code = str(creator_code or "").strip()
    return f"{SITE_URL.rstrip('/')}/vid/{quote(code)}"


def _creator_code_candidates(vid: str) -> list[str]:
    raw = str(vid or "").strip()
    normalized = _normalize_public_vid(raw)
    candidates: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value.upper() not in {item.upper() for item in candidates}:
            candidates.append(value)

    add(raw)
    add(raw.upper())
    add(normalized)
    if normalized:
        add(normalized.replace("-", "_"))
    return candidates


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


def build_student_overview(limit: int = 48) -> dict[str, Any]:
    schools = list_student_schools_with_stats()
    cards = list_student_qr_codes(limit=max(64, int(limit)))
    recent_bound = [item for item in cards if str(item.get("status") or "") == "bound"][: int(limit)]
    recent_students = build_student_roster(limit=limit)
    funnel = build_student_funnel_snapshot(limit=max(12, int(limit)))
    return {
        "schools": schools,
        "recent_cards": cards[: int(limit)],
        "recent_bound": recent_bound,
        "students": recent_students,
        "batch_progress": funnel["batch_progress"],
        "school_funnels": funnel["school_funnels"],
        "recent_events": funnel["recent_events"],
        "recent_anomalies": funnel["recent_anomalies"],
        "recent_audit": funnel["recent_audit"],
    }


def build_student_batch_detail(*, school_id: str, batch_name: str, limit: int = 240) -> dict[str, Any]:
    school = get_school(school_id)
    items = list_student_qr_codes(school_id=school_id, batch_name=batch_name, limit=limit)
    manifest_url = next((str(item.get("manifest_url") or "") for item in items if str(item.get("manifest_url") or "")), "")
    printable_url = manifest_url.replace("/manifest.csv", "/printable.html") if manifest_url.endswith("/manifest.csv") else ""
    recent_events = [item for item in _recent_student_events(limit=max(12, min(int(limit), 48))) if str(item.get("school_id") or "") == str(school_id or "")]
    recent_audit = [item for item in list_student_identity_audit(school_id=school_id, limit=max(12, min(int(limit), 48))) if str(item.get("qr_id") or "") in {str(card.get("qr_id") or "") for card in items}]
    return {
        "school": school,
        "batch_name": batch_name,
        "manifest_url": manifest_url,
        "printable_url": printable_url,
        "summary": next((item for item in _student_batch_progress(limit=max(12, int(limit))) if str(item.get("school_id") or "") == str(school_id or "") and str(item.get("batch_name") or "") == str(batch_name or "")), {}),
        "recent_events": recent_events[:24],
        "recent_audit": recent_audit[:24],
        "items": items,
    }


def build_student_roster(limit: int = 80) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            sv.user_id,
            sv.school_id,
            sv.student_id_code,
            sv.status,
            sv.commission_rate_override,
            sv.verified_at,
            sv.expires_at,
            u.creator_code,
            u.email,
            u.name,
            s.school_name
        FROM student_verifications sv
        LEFT JOIN users u ON u.id = sv.user_id
        LEFT JOIN schools s ON s.school_id = sv.school_id
        ORDER BY sv.verified_at DESC, sv.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "user_id": int(row["user_id"] or 0),
                "school_id": row["school_id"] or "",
                "school_name": row["school_name"] or "",
                "student_id_code": row["student_id_code"] or "",
                "status": row["status"] or "",
                "commission_rate_override": float(row["commission_rate_override"] or 0.0),
                "verified_at": row["verified_at"] or "",
                "expires_at": row["expires_at"] or "",
                "creator_code": row["creator_code"] or "",
                "email": row["email"] or "",
                "name": row["name"] or "",
            }
        )
    return items


def build_student_detail(user_id: int) -> dict[str, Any]:
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    if not user:
        raise ValueError("User not found")
    verification = get_student_verification_for_user(int(user_id))
    scans = list_student_scan_events(user_id=int(user_id), limit=20)
    qr_rows = conn.execute(
        """
        SELECT * FROM student_qr_codes
        WHERE bound_user_id=?
        ORDER BY bound_at DESC, id DESC
        LIMIT 12
        """,
        (int(user_id),),
    ).fetchall()
    submissions = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN detection_status='confirmed' THEN 1 ELSE 0 END) AS confirmed,
               AVG(campaign_score) AS avg_campaign_score
        FROM submissions
        WHERE user_id=?
        """,
        (int(user_id),),
    ).fetchone()
    order_stats = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM platform_ingest_events
        WHERE source_platform='shopify' AND entity_type='order'
        """,
    ).fetchone()
    affiliate_orders = 0
    affiliate_gmv = 0.0
    creator_code = str(user["creator_code"] or "").strip().lower()
    if creator_code:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM platform_ingest_events
            WHERE source_platform='shopify' AND entity_type='order'
            ORDER BY id DESC
            LIMIT 400
            """
        ).fetchall()
        for row in rows:
            payload = row["payload_json"] or "{}"
            try:
                doc = json.loads(payload)
            except Exception:
                doc = {}
            ref_code = str(doc.get("ref_code") or "").strip().lower()
            if ref_code != creator_code:
                continue
            body = doc.get("body") if isinstance(doc.get("body"), dict) else {}
            affiliate_orders += 1
            affiliate_gmv += float(body.get("current_total_price") or body.get("total_price") or 0.0)
    commission_rate = float(verification.get("commission_rate_override") or STUDENT_COMMISSION_RATE) if verification else 0.0
    return {
        "user": {
            "id": int(user["id"]),
            "name": user["name"] or "",
            "email": user["email"] or "",
            "creator_code": user["creator_code"] or "",
        },
        "student": verification,
        "identity_cards": [
            {
                "qr_id": row["qr_id"] or "",
                "school_id": row["school_id"] or "",
                "display_serial": row["display_serial"] or "",
                "status": row["status"] or "",
                "card_image_url": row["card_image_url"] or "",
                "claim_url": row["claim_url"] or "",
                "bound_at": row["bound_at"] or "",
            }
            for row in qr_rows
        ],
        "submissions": {
            "total": int((submissions["total"] if submissions else 0) or 0),
            "approved": int((submissions["confirmed"] if submissions else 0) or 0),
            "avg_campaign_score": round(float((submissions["avg_campaign_score"] if submissions else 0.0) or 0.0), 2),
        },
        "affiliate": {
            "orders": affiliate_orders,
            "gmv": round(affiliate_gmv, 2),
            "estimated_commission": round(affiliate_gmv * commission_rate, 2),
            "commission_rate": commission_rate,
            "shopify_orders_scanned": int((order_stats["total"] if order_stats else 0) or 0),
        },
        "recent_scans": scans,
        "audit_log": list_student_identity_audit(user_id=int(user_id), limit=24),
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
