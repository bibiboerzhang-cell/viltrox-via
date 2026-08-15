"""Canonical KOL contact ingestion with evidence-backed verification.

This module is deliberately provider-free.  It accepts already-observed public
contact facts, validates and normalizes them, and writes one canonical contact
plus one or more source records.  Raw evidence text is never persisted here.

Callers must use :func:`set_contact_verification_status` for invalidation or
revocation.  Discovery ingest can only observe or promote a contact to
``verified_public_business`` when the supplied public evidence meets the
minimum contract.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

from app.db.connection import get_conn


VERIFICATION_STATUSES = frozenset(
    {"observed", "verified_public_business", "stale", "invalid", "revoked"}
)
VERIFIED_PUBLIC_MIN_CONFIDENCE = 0.85

_VERIFIABLE_PUBLIC_SOURCES = frozenset(
    {
        "youtube_about_declared",
        "ig_business_profile",
        "bio_explicit_contact",
        "website_declared",
        "manual_verified_public_business",
    }
)
_RAW_ONLY_SOURCES = frozenset({"raw_full_scan"})
_IG_PUBLIC_FIELDS = frozenset(
    {
        "profile.public_email",
        "profile.publicemail",
        "profile.business_email",
        "profile.businessemail",
    }
)
_YOUTUBE_PUBLIC_FIELDS = frozenset(
    {
        "about.business_email",
        "about.email",
        "profile.about.business_email",
        "profile.about.email",
    }
)
_PROFILE_HOSTS = frozenset(
    {
        "instagram.com",
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        "x.com",
        "twitter.com",
        "facebook.com",
    }
)
_BIO_CONTACT_ANCHORS = (
    "business inquiries",
    "business inquiry",
    "business enquiries",
    "business enquiry",
    "business email",
    "for business",
    "contact:",
    "contact me",
    "reach me",
    "商务合作",
    "商务联系",
    "合作请联系",
    "联系邮箱",
)
_CONSENT_BASES = frozenset(
    {
        "legitimate_interest_public_business",
        "manual_entry",
        "public_scan",
        "source_observation",
        "creator_opt_in",
        "platform_messaging_consent",
        "existing_business_relationship",
    }
)
_EMAIL_TYPES = frozenset(
    {"email", "business_email", "public_email", "contact_email", "manager_email"}
)
_PHONE_TYPES = frozenset({"phone", "contact_phone", "phone_number", "mobile", "tel"})
_WHATSAPP_TYPES = frozenset({"whatsapp", "whatsapp_link", "wa", "wa.me"})
_DM_TYPE_TO_CHANNEL = {
    "instagram": "instagram_dm",
    "instagram_dm": "instagram_dm",
    "instagram_link": "instagram_dm",
    "ig_dm": "instagram_dm",
    "tiktok": "tiktok_dm",
    "tiktok_dm": "tiktok_dm",
    "tiktok_link": "tiktok_dm",
    "x": "x_dm",
    "x_dm": "x_dm",
    "twitter": "x_dm",
    "twitter_dm": "x_dm",
    "twitter_link": "x_dm",
    "facebook": "facebook_dm",
    "facebook_dm": "facebook_dm",
    "facebook_link": "facebook_dm",
    "messenger": "facebook_dm",
    "messenger_link": "facebook_dm",
    "telegram": "telegram_dm",
    "telegram_dm": "telegram_dm",
    "telegram_link": "telegram_dm",
}
_DM_HOSTS = {
    "instagram_dm": frozenset({"instagram.com"}),
    "tiktok_dm": frozenset({"tiktok.com"}),
    "x_dm": frozenset({"x.com", "twitter.com"}),
    "facebook_dm": frozenset({"facebook.com", "m.me"}),
    "telegram_dm": frozenset({"t.me", "telegram.me"}),
}
_URL_TYPE_TO_CHANNEL = {
    "url": "website",
    "website": "website",
    "link": "website",
    "link_hub": "link_hub",
    "youtube": "youtube_link",
    "youtube_link": "youtube_link",
    "linkedin": "linkedin_link",
    "linkedin_link": "linkedin_link",
    "twitch": "twitch_link",
    "twitch_link": "twitch_link",
    "pinterest": "pinterest_link",
    "pinterest_link": "pinterest_link",
}
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SAFE_FIELD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
class ContactValidationError(ValueError):
    """Raised when input cannot safely become a canonical contact fact."""


@dataclass(frozen=True)
class NormalizedContact:
    channel: str
    normalized_value: str
def _text(value: Any, *, field: str, maximum: int) -> str:
    raw = str(value or "")
    if _CONTROL_RE.search(raw):
        raise ContactValidationError(f"{field} contains control characters")
    normalized = unicodedata.normalize("NFKC", raw).strip()
    if not normalized or len(normalized) > maximum or _CONTROL_RE.search(normalized):
        raise ContactValidationError(f"invalid {field}")
    return normalized


def _normalize_email(value: Any) -> str:
    text = _text(value, field="email", maximum=254)
    if any(char.isspace() for char in text) or not _EMAIL_RE.fullmatch(text):
        raise ContactValidationError("invalid email")
    local, domain = text.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ContactValidationError("invalid email")
    try:
        ascii_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ContactValidationError("invalid email domain") from exc
    normalized = f"{local.lower()}@{ascii_domain}"
    if len(normalized) > 254 or any(len(label) > 63 for label in ascii_domain.split(".")):
        raise ContactValidationError("invalid email")
    return normalized


def _normalize_phone(value: Any) -> str:
    text = _text(value, field="phone", maximum=64)
    if not re.fullmatch(r"[+0-9\s().-]+", text):
        raise ContactValidationError("invalid phone")
    compact = re.sub(r"[\s().-]", "", text)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if not compact.startswith("+") or not compact[1:].isdigit():
        raise ContactValidationError("phone must use an explicit international prefix")
    digits = compact[1:]
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise ContactValidationError("invalid international phone")
    return "+" + digits


def _normalized_host(hostname: str) -> str:
    host = hostname.rstrip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ContactValidationError("invalid URL host") from exc
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        raise ContactValidationError("non-public URL host")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        labels = host.split(".")
        if "." not in host or not all(_DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
            raise ContactValidationError("invalid URL host")
        if all(label.isdigit() for label in labels):
            raise ContactValidationError("invalid non-canonical URL address")
    else:
        if not address.is_global:
            raise ContactValidationError("non-public URL address")
        host = address.compressed
    return host


def _normalize_public_url(value: Any, *, field: str = "URL") -> str:
    text = _text(value, field=field, maximum=2048)
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ContactValidationError(f"invalid {field}") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ContactValidationError(f"invalid {field}")
    if parsed.username is not None or parsed.password is not None:
        raise ContactValidationError(f"{field} must not contain credentials")
    host = _normalized_host(parsed.hostname)
    if port is not None and not 1 <= int(port) <= 65535:
        raise ContactValidationError(f"invalid {field} port")
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if port is None or default_port else f"{netloc_host}:{port}"
    decoded_path = unquote(parsed.path or "/")
    if _CONTROL_RE.search(decoded_path):
        raise ContactValidationError(f"invalid {field} path")
    path = quote(decoded_path, safe="/:@-._~!$&'()*+,;=") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def _normalize_whatsapp(value: Any) -> str:
    text = _text(value, field="WhatsApp", maximum=2048)
    if text.lower().startswith(("http://", "https://")):
        try:
            parsed = urlsplit(text)
        except ValueError as exc:
            raise ContactValidationError("invalid WhatsApp URL") from exc
        if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
            raise ContactValidationError("invalid WhatsApp URL")
        host = _normalized_host(parsed.hostname or "")
        if host == "wa.me":
            candidate = (parsed.path or "").strip("/").split("/", 1)[0]
        elif host == "api.whatsapp.com" and parsed.path.rstrip("/") == "/send":
            values = parse_qs(parsed.query, keep_blank_values=True).get("phone", [])
            if len(values) != 1:
                raise ContactValidationError("invalid WhatsApp phone parameter")
            candidate = values[0]
        else:
            raise ContactValidationError("unsupported WhatsApp URL")
        if not candidate.startswith(("+", "00")):
            candidate = "+" + candidate
        return _normalize_phone(candidate)
    return _normalize_phone(text)


def _normalize_dm(channel: str, value: Any) -> str:
    text = _text(value, field="DM handle", maximum=512)
    if text.lower().startswith(("http://", "https://")):
        try:
            parsed = urlsplit(text)
            port = parsed.port
        except ValueError as exc:
            raise ContactValidationError("invalid DM URL") from exc
        if parsed.scheme.lower() != "https" or parsed.username or parsed.password or port not in (None, 443):
            raise ContactValidationError("invalid DM URL")
        host = _normalized_host(parsed.hostname or "")
        if host not in _DM_HOSTS[channel]:
            raise ContactValidationError("DM URL does not match channel")
        segments = [unquote(part) for part in (parsed.path or "").split("/") if part]
        if not segments:
            raise ContactValidationError("DM URL is missing a handle")
        handle = segments[0]
        if channel == "tiktok_dm" and handle.startswith("@"):
            handle = handle[1:]
        if channel == "facebook_dm" and handle in {"profile.php", "messages", "share"}:
            raise ContactValidationError("DM URL is not a public profile")
    else:
        handle = text[1:] if text.startswith("@") else text
    handle = unicodedata.normalize("NFKC", handle).strip().lower()
    if not _HANDLE_RE.fullmatch(handle):
        raise ContactValidationError("invalid DM handle")
    return "@" + handle


def normalize_contact(contact_type: Any, value: Any) -> NormalizedContact:
    """Return a channel and canonical value or reject malformed input.

    The returned value is sensitive application data.  It must never be used in
    logs, queue payloads, suppression rows, or eligibility responses.
    """

    kind = _text(contact_type, field="contact type", maximum=64).lower().replace("-", "_")
    if kind in _EMAIL_TYPES:
        return NormalizedContact("email", _normalize_email(value))
    if kind in _PHONE_TYPES:
        return NormalizedContact("phone", _normalize_phone(value))
    if kind in _WHATSAPP_TYPES:
        return NormalizedContact("whatsapp", _normalize_whatsapp(value))
    if kind in _DM_TYPE_TO_CHANNEL:
        channel = _DM_TYPE_TO_CHANNEL[kind]
        return NormalizedContact(channel, _normalize_dm(channel, value))
    if kind in _URL_TYPE_TO_CHANNEL:
        return NormalizedContact(_URL_TYPE_TO_CHANNEL[kind], _normalize_public_url(value))
    raise ContactValidationError("unsupported contact type")


def _bounded_token(value: Any, *, field: str, optional: bool = False) -> str:
    text = str(value or "").strip()
    if not text and optional:
        return ""
    if not _SAFE_TOKEN_RE.fullmatch(text):
        raise ContactValidationError(f"invalid {field}")
    return text.lower()


def _bounded_field(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _SAFE_FIELD_RE.fullmatch(text):
        raise ContactValidationError("invalid source field")
    return text.lower()


def _positive_id(value: Any, *, field: str, optional: bool = False) -> int | None:
    if value in (None, "") and optional:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ContactValidationError(f"invalid {field}") from exc
    if result <= 0:
        raise ContactValidationError(f"invalid {field}")
    return result


def _confidence(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContactValidationError("invalid confidence") from exc
    if not 0.0 <= result <= 1.0:
        raise ContactValidationError("invalid confidence")
    return result


def _strict_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ContactValidationError(f"invalid {field}")


def _timestamp(value: Any | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = _text(value, field="observed timestamp", maximum=64)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContactValidationError("invalid observed timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _qualifies_for_verified_public_business(
    *,
    source_type: str,
    confidence: float,
    is_public_declared: bool,
    source_url: str,
    source_field: str,
    staff_id: int | None,
    normalized_value: str = "",
    evidence_text: str = "",
) -> bool:
    if source_type in _RAW_ONLY_SOURCES or source_type not in _VERIFIABLE_PUBLIC_SOURCES:
        return False
    if not is_public_declared or confidence < VERIFIED_PUBLIC_MIN_CONFIDENCE:
        return False
    if source_type == "manual_verified_public_business" and staff_id is None:
        return False
    # Neither free text nor a provider run reference is a field-level public
    # proof.  Verification always needs the sanitized public page and the exact
    # field on that page; ordinary ``manual`` is intentionally not allowlisted.
    if not source_url or not source_field:
        return False
    try:
        host = (urlsplit(source_url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False

    def host_is(*allowed: str) -> bool:
        return any(host == item or host.endswith("." + item) for item in allowed)

    field_key = source_field.casefold()
    if source_type == "ig_business_profile":
        return host_is("instagram.com") and field_key in _IG_PUBLIC_FIELDS
    if source_type == "youtube_about_declared":
        return host_is("youtube.com", "youtu.be") and field_key in _YOUTUBE_PUBLIC_FIELDS
    if source_type == "bio_explicit_contact":
        public_text_field = bool(
            field_key in {
                "profile.bio",
                "profile.biography",
                "profile.about",
                "profile.description",
                "profile.channel_description",
                "profile.signature",
                "profile.snippet.description",
                "profile.items.0.snippet.description",
            }
            or field_key.endswith(
                (".bio", ".biography", ".signature", ".description")
            )
        )
        evidence = str(evidence_text or "").casefold()
        identity = str(normalized_value or "").casefold()
        return bool(
            host_is(*_PROFILE_HOSTS)
            and public_text_field
            and identity
            and identity in evidence
            and any(anchor in evidence for anchor in _BIO_CONTACT_ANCHORS)
        )
    if source_type == "website_declared":
        # L0 has no separately verified website-to-creator identity proof.
        return False
    # Manual verification still requires an actor plus an explicit locator;
    # its staff workflow supplies the human verification event.
    return source_type == "manual_verified_public_business"


def _evidence_fingerprint(
    *,
    source_type: str,
    source_url: str,
    source_field: str,
    provider_run_ref: str,
    evidence_text: str,
    consent_basis: str,
    consent_at: str,
) -> str:
    evidence_digest = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest() if evidence_text else ""
    payload = "\x1f".join(
        (
            source_type,
            source_url,
            source_field,
            provider_run_ref,
            evidence_digest,
            consent_basis,
            consent_at,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _find_existing_contact(
    db: Any,
    *,
    kol_pool_id: int,
    normalized: NormalizedContact,
) -> dict[str, Any] | None:
    rows = db.execute(
        """
        SELECT id, kol_pool_id, contact_type, contact_value, contact_source,
               source_url, consent_basis, is_public_declared,
               extracted_by_staff_id, apify_run_ref, confidence,
               first_seen_at, last_seen_at, normalized_value, channel,
               verification_status, verified_at, invalidated_at, revoked_at
        FROM vkpi_kol_pool_contacts
        WHERE kol_pool_id = ?
        ORDER BY id
        """,
        (kol_pool_id,),
    ).fetchall()
    for raw_row in rows:
        row = _row_dict(raw_row)
        if (
            str(row.get("channel") or "") == normalized.channel
            and str(row.get("normalized_value") or "") == normalized.normalized_value
        ):
            return row
        try:
            legacy = normalize_contact(row.get("contact_type"), row.get("contact_value"))
        except ContactValidationError:
            continue
        if legacy == normalized:
            return row
    return None


def ingest_contact(
    *,
    kol_pool_id: int,
    contact_type: str,
    contact_value: str,
    source_type: str,
    source_url: str = "",
    source_field: str = "",
    evidence_text: str = "",
    confidence: float = 0.0,
    is_public_declared: bool = False,
    verification_status: str = "observed",
    provider_run_ref: str = "",
    staff_id: int | None = None,
    actor_staff_id: int | None = None,
    consent_basis: str = "source_observation",
    consent_at: Any | None = None,
    observed_at: Any | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Ingest one contact and one source observation atomically.

    The result contains identifiers and state only; it intentionally excludes
    contact values and evidence text.
    """

    db = conn or get_conn()
    pool_id = _positive_id(kol_pool_id, field="KOL pool id")
    staff_actor_id = _positive_id(staff_id, field="staff id", optional=True)
    alias_actor_id = _positive_id(actor_staff_id, field="actor staff id", optional=True)
    if staff_actor_id is not None and alias_actor_id is not None and staff_actor_id != alias_actor_id:
        raise ContactValidationError("conflicting staff identifiers")
    actor_id = staff_actor_id if staff_actor_id is not None else alias_actor_id
    normalized = normalize_contact(contact_type, contact_value)
    source = _bounded_token(source_type, field="source type")
    if source in {"manual", "manual_verified_public_business"} and actor_id is None:
        raise ContactValidationError("manual contact evidence requires a staff actor")
    field = _bounded_field(source_field)
    provider_ref = _bounded_token(
        provider_run_ref, field="provider run reference", optional=True
    )
    basis = str(consent_basis or "").strip().lower()
    if basis not in _CONSENT_BASES:
        raise ContactValidationError("invalid consent basis")
    consent_timestamp = _timestamp(consent_at) if consent_at is not None else ""
    if basis in {"creator_opt_in", "platform_messaging_consent"} and not consent_timestamp:
        raise ContactValidationError("explicit consent basis requires consent timestamp")
    conf = _confidence(confidence)
    public_declared = _strict_bool(is_public_declared, field="public declaration flag")
    seen_at = _timestamp(observed_at)
    raw_evidence = str(evidence_text or "")
    if len(raw_evidence) > 16_384 or _CONTROL_RE.search(raw_evidence.replace("\n", "").replace("\r", "")):
        raise ContactValidationError("invalid evidence text")
    sanitized_source_url = _normalize_public_url(source_url, field="source URL") if source_url else ""
    requested_status = str(verification_status or "").strip().lower()
    if requested_status not in {"observed", "verified_public_business"}:
        raise ContactValidationError("ingest cannot set lifecycle status")
    qualifies = _qualifies_for_verified_public_business(
        source_type=source,
        confidence=conf,
        is_public_declared=public_declared,
        source_url=sanitized_source_url,
        source_field=field,
        staff_id=actor_id,
        normalized_value=normalized.normalized_value,
        evidence_text=raw_evidence,
    )
    candidate_status = (
        "verified_public_business"
        if requested_status == "verified_public_business" and qualifies
        else "observed"
    )
    evidence_fp = _evidence_fingerprint(
        source_type=source,
        source_url=sanitized_source_url,
        source_field=field,
        provider_run_ref=provider_ref,
        evidence_text=raw_evidence,
        consent_basis=basis,
        consent_at=consent_timestamp,
    )

    try:
        existing = _find_existing_contact(
            db, kol_pool_id=int(pool_id), normalized=normalized
        )
        inserted = existing is None
        if inserted:
            cursor = db.execute(
                """
                INSERT INTO vkpi_kol_pool_contacts
                    (kol_pool_id, contact_type, contact_value, contact_source,
                     source_url, consent_basis, is_public_declared,
                     extracted_by_staff_id, apify_run_ref, confidence,
                     first_seen_at, last_seen_at, normalized_value, channel,
                     verification_status, verified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    int(pool_id),
                    normalized.channel,
                    normalized.normalized_value,
                    source,
                    sanitized_source_url,
                    basis,
                    public_declared,
                    actor_id,
                    provider_ref,
                    conf,
                    seen_at,
                    seen_at,
                    normalized.normalized_value,
                    normalized.channel,
                    candidate_status,
                    seen_at if candidate_status == "verified_public_business" else None,
                ),
            )
            inserted_row = cursor.fetchone()
            if inserted_row is not None:
                contact_id = int(_row_dict(inserted_row).get("id") or inserted_row[0])
                existing = {
                    "id": contact_id,
                    "verification_status": candidate_status,
                    "contact_source": source,
                    "confidence": conf,
                }
            else:
                inserted = False
                existing = _find_existing_contact(
                    db, kol_pool_id=int(pool_id), normalized=normalized
                )
                if existing is None:
                    raise RuntimeError("canonical contact conflict could not be resolved")

        contact_id = int(existing["id"])
        previous_status = str(existing.get("verification_status") or "observed")
        if previous_status not in VERIFICATION_STATUSES:
            previous_status = "observed"
        if previous_status in {"invalid", "revoked", "verified_public_business"}:
            final_status = previous_status
        elif candidate_status == "verified_public_business":
            final_status = candidate_status
        else:
            final_status = previous_status
        previous_confidence = existing.get("confidence")
        try:
            canonical_confidence = max(conf, float(previous_confidence))
        except (TypeError, ValueError):
            canonical_confidence = conf
        promoted = final_status == "verified_public_business" and previous_status != final_status
        canonical_source = str(existing.get("contact_source") or source)
        if promoted:
            canonical_source = source

        db.execute(
            """
            UPDATE vkpi_kol_pool_contacts
            SET normalized_value=?, channel=?,
                last_seen_at=CASE
                    WHEN last_seen_at IS NULL OR last_seen_at<? THEN ?
                    ELSE last_seen_at
                END,
                first_seen_at=COALESCE(first_seen_at, ?),
                confidence=?,
                is_public_declared=CASE WHEN is_public_declared THEN TRUE ELSE ? END,
                extracted_by_staff_id=COALESCE(extracted_by_staff_id, ?),
                apify_run_ref=CASE WHEN COALESCE(apify_run_ref, '')='' THEN ? ELSE apify_run_ref END,
                source_url=CASE WHEN COALESCE(source_url, '')='' THEN ? ELSE source_url END,
                contact_source=?, verification_status=?,
                verified_at=CASE
                    WHEN ?='verified_public_business' THEN COALESCE(verified_at, ?)
                    ELSE verified_at
                END
            WHERE id=? AND kol_pool_id=?
            """,
            (
                normalized.normalized_value,
                normalized.channel,
                seen_at,
                seen_at,
                seen_at,
                canonical_confidence,
                public_declared,
                actor_id,
                provider_ref,
                sanitized_source_url,
                canonical_source,
                final_status,
                final_status,
                seen_at,
                contact_id,
                int(pool_id),
            ),
        )
        evidence_cursor = db.execute(
            """
            INSERT INTO vkpi_kol_contact_evidence
                (contact_id, kol_pool_id, source_type, source_url, source_field,
                 evidence_fingerprint, confidence, is_public_declared,
                 consent_basis, consent_at,
                 provider_run_ref, observed_by_staff_id, first_seen_at, last_seen_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(contact_id, evidence_fingerprint) DO UPDATE SET
                last_seen_at=CASE
                    WHEN vkpi_kol_contact_evidence.last_seen_at < excluded.last_seen_at
                    THEN excluded.last_seen_at
                    ELSE vkpi_kol_contact_evidence.last_seen_at
                END,
                confidence=CASE
                    WHEN vkpi_kol_contact_evidence.confidence IS NULL THEN excluded.confidence
                    WHEN excluded.confidence IS NULL THEN vkpi_kol_contact_evidence.confidence
                    WHEN excluded.confidence > vkpi_kol_contact_evidence.confidence THEN excluded.confidence
                    ELSE vkpi_kol_contact_evidence.confidence
                END,
                is_public_declared=(vkpi_kol_contact_evidence.is_public_declared OR excluded.is_public_declared),
                observed_by_staff_id=COALESCE(vkpi_kol_contact_evidence.observed_by_staff_id, excluded.observed_by_staff_id)
            RETURNING id
            """,
            (
                contact_id,
                int(pool_id),
                source,
                sanitized_source_url,
                field,
                evidence_fp,
                conf,
                public_declared,
                basis,
                consent_timestamp or None,
                provider_ref,
                actor_id,
                seen_at,
                seen_at,
            ),
        )
        evidence_row = evidence_cursor.fetchone()
        evidence_id = int(_row_dict(evidence_row).get("id") or evidence_row[0])
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "contact_id": contact_id,
        "kol_pool_id": int(pool_id),
        "channel": normalized.channel,
        "verification_status": final_status,
        "inserted": inserted,
        "evidence_id": evidence_id,
        "promoted": promoted,
    }


def set_contact_verification_status(
    contact_id: int,
    verification_status: str,
    *,
    staff_id: int | None = None,
    changed_at: Any | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Apply a durable verification lifecycle transition without returning PII."""

    db = conn or get_conn()
    cid = _positive_id(contact_id, field="contact id")
    actor_id = _positive_id(staff_id, field="staff id", optional=True)
    target = str(verification_status or "").strip().lower()
    if target not in VERIFICATION_STATUSES:
        raise ContactValidationError("invalid verification status")
    at = _timestamp(changed_at)
    try:
        raw_row = db.execute(
            """
            SELECT id, kol_pool_id, verification_status
            FROM vkpi_kol_pool_contacts WHERE id=?
            """,
            (int(cid),),
        ).fetchone()
        if raw_row is None:
            raise LookupError("contact not found")
        row = _row_dict(raw_row)
        current = str(row.get("verification_status") or "observed")
        if current in {"invalid", "revoked"} and target != current:
            raise ContactValidationError("terminal contact state cannot be reopened")
        if target in {"invalid", "revoked"} and actor_id is None:
            raise ContactValidationError("staff id is required for restrictive state changes")
        if target == "verified_public_business":
            evidence_rows = db.execute(
                """
                SELECT source_type, source_url, source_field, confidence,
                       is_public_declared, observed_by_staff_id
                FROM vkpi_kol_contact_evidence
                WHERE contact_id=? AND is_public_declared=TRUE
                  AND confidence>=?
                  AND source_type IN (
                      'youtube_about_declared', 'ig_business_profile',
                      'manual_verified_public_business'
                  )
                  AND COALESCE(source_url, '')<>''
                  AND COALESCE(source_field, '')<>''
                ORDER BY confidence DESC, last_seen_at DESC, id DESC
                """,
                (int(cid), VERIFIED_PUBLIC_MIN_CONFIDENCE),
            ).fetchall()
            verification_source = ""
            for raw_evidence_row in evidence_rows:
                evidence_row = _row_dict(raw_evidence_row)
                source_candidate = str(evidence_row.get("source_type") or "")
                if _qualifies_for_verified_public_business(
                    source_type=source_candidate,
                    confidence=float(evidence_row.get("confidence") or 0.0),
                    is_public_declared=bool(evidence_row.get("is_public_declared")),
                    source_url=str(evidence_row.get("source_url") or ""),
                    source_field=str(evidence_row.get("source_field") or ""),
                    staff_id=(
                        int(evidence_row.get("observed_by_staff_id"))
                        if evidence_row.get("observed_by_staff_id")
                        else None
                    ),
                ):
                    verification_source = source_candidate
                    break
            if not verification_source:
                raise ContactValidationError("verified status requires public business evidence")
        else:
            verification_source = ""
        db.execute(
            """
            UPDATE vkpi_kol_pool_contacts
            SET verification_status=?,
                contact_source=CASE
                    WHEN ?='verified_public_business' THEN ?
                    ELSE contact_source
                END,
                verified_at=CASE WHEN ?='verified_public_business' THEN COALESCE(verified_at, ?) ELSE verified_at END,
                invalidated_at=CASE WHEN ?='invalid' THEN COALESCE(invalidated_at, ?) ELSE invalidated_at END,
                revoked_at=CASE WHEN ?='revoked' THEN COALESCE(revoked_at, ?) ELSE revoked_at END,
                last_seen_at=CASE WHEN ? IN ('observed','verified_public_business') THEN ? ELSE last_seen_at END
            WHERE id=?
            """,
            (
                target,
                target,
                verification_source,
                target,
                at,
                target,
                at,
                target,
                at,
                target,
                at,
                int(cid),
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "contact_id": int(cid),
        "kol_pool_id": int(row["kol_pool_id"]),
        "verification_status": target,
        "changed_by_staff_id": actor_id,
    }


__all__ = [
    "ContactValidationError",
    "NormalizedContact",
    "VERIFICATION_STATUSES",
    "VERIFIED_PUBLIC_MIN_CONFIDENCE",
    "ingest_contact",
    "normalize_contact",
    "set_contact_verification_status",
]
