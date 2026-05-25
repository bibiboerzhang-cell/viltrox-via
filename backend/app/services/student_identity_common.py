"""Common helpers and constants for student identity flows."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.core.config import JWT_SECRET, SHOPIFY_AFFILIATE_BASE_URL, SITE_URL, UPLOAD_DIR

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

def _csv_rows_from_text(roster_csv: str) -> list[dict[str, str]]:
    text = str(roster_csv or "").strip()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in reader]

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

def _shop_url_for_creator(creator_code: str = "") -> str:
    base = str(SHOPIFY_AFFILIATE_BASE_URL or "https://viltrox.com/").strip() or "https://viltrox.com/"
    code = str(creator_code or "").strip()
    if not code:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}ref={quote(code)}"

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
