"""Explicit KOL contact reveal boundary.

读端默认脱敏(pool_common.mask_pool_item 在 list_pool/get_item 出口套用,掩码 e***@d***)。
真值仅经本模块 view_kol_contact 展开:必须 confirm=True 二次确认,且每次展开写
vkpi_sensitive_access_logs(audit/service.log_sensitive_access)+ 更新 vkpi_kol_pool 审计计数列
(118 迁移:contact_reveal_count / contact_last_revealed_at / contact_last_revealed_by_staff_id)。

Ordinary GET item/detail DTOs never call this module.  One confirmed POST does
one permission+audit decision, then evaluates every canonical contact through
the verification/suppression gate.  Errors and restricted results never carry
contact values.  No provider, website crawl or message send is performed here.

Every disclosed contact carries a ``tier``: ``verified`` (public-business
verified with evidence) or ``observed`` (pipeline scan / declaration, not yet
verified).  The response ``status`` contract stays ``full`` / ``restricted`` /
``empty``; verified rows are listed before observed ones.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime

from app.core.logging import get_logger

logger = get_logger(__name__)

ALLOWED_REVEAL_PURPOSES = frozenset({"kol_detail_view", "compose_outreach"})
CONTACT_TIER_VERIFIED = "verified"
CONTACT_TIER_OBSERVED = "observed"
_TIER_ORDER = {CONTACT_TIER_VERIFIED: 0, CONTACT_TIER_OBSERVED: 1}
# Source labels that may accompany a disclosed contact.  Anything else stays
# internal (the row is still disclosed when eligible; only the label is dropped).
_DISCLOSABLE_SOURCES = frozenset(
    {
        "raw_bio_scan",
        "raw_full_scan",
        "youtube_about_declared",
        "ig_business_profile",
        "bio_explicit_contact",
        "website_declared",
        "manual",
        "manual_verified_public_business",
    }
)
_GUARD_UNAVAILABLE_REASONS = frozenset(
    {
        "fingerprint_key_unavailable",
        "suppression_check_unavailable",
        "verification_evidence_missing",
        "verification_state_incomplete",
        "contact_identity_mismatch",
        "invalid_brand_scope",
        "contact_not_found",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_contact_audit_schema() -> None:
    """本地 SQLite 兼容:幂等补 118 迁移的审计列(Postgres 走正式迁移序列,不进此分支)。"""
    if is_postgres_runtime():
        return
    conn = get_conn()
    try:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(vkpi_kol_pool)").fetchall()}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return
    try:
        if "contact_reveal_count" not in cols:
            conn.execute("ALTER TABLE vkpi_kol_pool ADD COLUMN contact_reveal_count INTEGER NOT NULL DEFAULT 0")
        if "contact_last_revealed_at" not in cols:
            conn.execute("ALTER TABLE vkpi_kol_pool ADD COLUMN contact_last_revealed_at TEXT")
        if "contact_last_revealed_by_staff_id" not in cols:
            conn.execute("ALTER TABLE vkpi_kol_pool ADD COLUMN contact_last_revealed_by_staff_id INTEGER")
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass


def _restricted(kol_pool_id: int, reason: str) -> dict[str, Any]:
    return {
        "status": "restricted",
        "kol_pool_id": int(kol_pool_id),
        "contacts": [],
        "contact_masked": True,
        "reason": str(reason or "contact_access_restricted"),
    }


def _contact_tier(verdict: dict[str, Any]) -> str:
    """Map a verdict to the public two-tier label; unknown tiers degrade to observed."""

    tier = str(verdict.get("tier") or "").strip().lower()
    if tier in _TIER_ORDER:
        return tier
    status = str(verdict.get("verification_status") or "").strip().lower()
    return CONTACT_TIER_VERIFIED if status == "verified_public_business" else CONTACT_TIER_OBSERVED


def _brand_scope(staff: dict[str, Any] | None) -> str:
    context = staff if isinstance(staff, dict) else {}
    try:
        organization_id = int(context.get("organization_id") or 0)
    except (TypeError, ValueError):
        return ""
    return f"organization:{organization_id}" if organization_id > 0 else ""


def _canonical_contact_rows(conn: Any, kol_pool_id: int) -> list[dict[str, Any]] | None:
    """Load canonical rows only; legacy pool snapshots are never reveal truth."""

    try:
        rows = conn.execute(
            """
            SELECT id, contact_type, contact_value, contact_source, verified_at
            FROM vkpi_kol_pool_contacts
            WHERE kol_pool_id = ? AND COALESCE(contact_value, '') <> ''
            ORDER BY id
            """,
            (int(kol_pool_id),),
        ).fetchall()
    except Exception:
        logger.warning("canonical KOL contact lookup unavailable; reveal fails closed", exc_info=True)
        return None
    return [dict(row) for row in rows]


def _contact_staff_id(staff: dict[str, Any] | None) -> int:
    try:
        return int((staff or {}).get("staff_id") or (staff or {}).get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _eligible_contact_projection(
    contact: dict[str, Any],
    *,
    kol_pool_id: int,
    brand_scope: str,
    conn: Any,
    contact_eligibility: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    contact_id = int(contact.get("id") or 0)
    if not contact_id:
        return None, "contact_not_found"
    try:
        verdict = contact_eligibility(
            contact_id=contact_id,
            kol_pool_id=int(kol_pool_id),
            brand_scope=brand_scope,
            conn=conn,
        )
    except Exception:
        verdict = {
            "status": "restricted",
            "eligible": False,
            "reason": "suppression_check_unavailable",
        }
    if not (
        isinstance(verdict, dict)
        and verdict.get("eligible") is True
        and verdict.get("status") == "eligible"
    ):
        return None, str((verdict or {}).get("reason") or "verification_not_eligible")
    value = str(contact.get("contact_value") or "").strip()
    if not value:
        return None, "contact_not_found"
    channel = str(verdict.get("channel") or contact.get("contact_type") or "contact").strip().lower()
    tier = _contact_tier(verdict)
    item = {
        "id": contact_id,
        "channel": channel,
        "contact_type": channel,
        "value": value,
        "tier": tier,
        "verification_status": str(
            verdict.get("verification_status")
            or ("verified_public_business" if tier == CONTACT_TIER_VERIFIED else "observed")
        ),
    }
    source = str(contact.get("contact_source") or "").strip().lower()
    if source in _DISCLOSABLE_SOURCES:
        item["source_type"] = source
    if tier == CONTACT_TIER_VERIFIED and contact.get("verified_at"):
        item["verified_at"] = str(contact.get("verified_at"))
    return item, None


def _eligible_contact_rows(
    canonical_rows: list[dict[str, Any]],
    *,
    kol_pool_id: int,
    brand_scope: str,
    conn: Any,
    contact_eligibility: Any,
) -> tuple[list[dict[str, Any]], set[str]]:
    eligible_contacts: list[dict[str, Any]] = []
    restricted_reasons: set[str] = set()
    for contact in canonical_rows:
        item, reason = _eligible_contact_projection(
            contact,
            kol_pool_id=kol_pool_id,
            brand_scope=brand_scope,
            conn=conn,
            contact_eligibility=contact_eligibility,
        )
        if item is not None:
            eligible_contacts.append(item)
        elif reason:
            restricted_reasons.add(reason)
    eligible_contacts.sort(key=lambda entry: _TIER_ORDER.get(str(entry.get("tier")), 1))
    return eligible_contacts, restricted_reasons


def _contact_guard_reason(restricted_reasons: set[str]) -> str:
    if restricted_reasons == {"suppressed"}:
        return "suppressed"
    if restricted_reasons.intersection(_GUARD_UNAVAILABLE_REASONS):
        return "contact_guard_unavailable"
    if "verification_not_eligible" in restricted_reasons:
        return "verification_required"
    return "contact_guard_unavailable"


def _record_contact_reveal(conn: Any, kol_pool_id: int, staff_id: int) -> None:
    try:
        _ensure_contact_audit_schema()
        conn.execute(
            """
            UPDATE vkpi_kol_pool
            SET contact_reveal_count = COALESCE(contact_reveal_count, 0) + 1,
                contact_last_revealed_at = ?,
                contact_last_revealed_by_staff_id = ?
            WHERE id = ?
            """,
            (_utcnow(), staff_id or None, int(kol_pool_id)),
        )
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)


def view_kol_contact(
    kol_pool_id: int,
    *,
    confirm: bool = False,
    staff: dict[str, Any] | None = None,
    page_path: str = "",
    ip: str = "",
    user_agent: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    """Return ``full``/``restricted``/``empty`` without leaking on failures."""
    if not confirm:
        return _restricted(int(kol_pool_id), "confirmation_required")
    normalized_purpose = str(purpose or "").strip()
    if normalized_purpose not in ALLOWED_REVEAL_PURPOSES:
        return _restricted(int(kol_pool_id), "purpose_not_allowed")

    from app.core.permissions import check_kol_pool_employee_contact_permission

    staff_id = _contact_staff_id(staff)
    # Pure authorization comes first, but a plaintext audit is truthful only
    # after at least one canonical contact has passed verification and
    # organization-scoped suppression.  Empty/restricted/404 reads therefore
    # generate no ``contact_plaintext=true`` record.
    if not check_kol_pool_employee_contact_permission(staff) or staff_id <= 0:
        return _restricted(int(kol_pool_id), "contact_reveal_not_authorized")

    try:
        conn = get_conn()
        pool_row = conn.execute(
            "SELECT id FROM vkpi_kol_pool WHERE id=?",
            (int(kol_pool_id),),
        ).fetchone()
    except Exception:
        logger.warning("KOL contact store unavailable; reveal fails closed")
        return _restricted(int(kol_pool_id), "contact_store_unavailable")
    if not pool_row:
        raise LookupError("kol pool item not found")

    canonical_rows = _canonical_contact_rows(conn, int(kol_pool_id))
    if canonical_rows is None:
        return _restricted(int(kol_pool_id), "contact_store_unavailable")
    if not canonical_rows:
        return {
            "status": "empty",
            "kol_pool_id": int(kol_pool_id),
            "contacts": [],
            "contact_masked": False,
            "reason": "no_verified_contacts",
        }

    from app.domains.kol.contact_suppression import contact_eligibility

    eligible_contacts, restricted_reasons = _eligible_contact_rows(
        canonical_rows,
        kol_pool_id=int(kol_pool_id),
        brand_scope=_brand_scope(staff),
        conn=conn,
        contact_eligibility=contact_eligibility,
    )

    if not eligible_contacts:
        return _restricted(int(kol_pool_id), _contact_guard_reason(restricted_reasons))

    from app.domains.kol.contact_access import authorize_plaintext_contacts

    audited = authorize_plaintext_contacts(
        staff,
        resource_type="kol_pool",
        resource_id=int(kol_pool_id),
        page_path=page_path or f"/kol-pool/{int(kol_pool_id)}/contacts/reveal",
        ip=ip,
        user_agent=user_agent,
        metadata={
            "purpose": normalized_purpose,
            "ip_present": bool(ip),
            "user_agent_present": bool(user_agent),
        },
        permission_check=check_kol_pool_employee_contact_permission,
    )
    if not audited:
        return _restricted(int(kol_pool_id), "contact_audit_unavailable")

    # 更新展开计数留痕(118 列;SQLite 幂等建)。明文审计已成功，此处是辅助计数。
    _record_contact_reveal(conn, int(kol_pool_id), staff_id)

    return {
        "status": "full",
        "kol_pool_id": int(kol_pool_id),
        "contacts": eligible_contacts,
        "contact_masked": False,
        "reason": "eligible_contacts_available",
        "verified_count": sum(1 for entry in eligible_contacts if entry.get("tier") == CONTACT_TIER_VERIFIED),
        "observed_count": sum(1 for entry in eligible_contacts if entry.get("tier") == CONTACT_TIER_OBSERVED),
    }
