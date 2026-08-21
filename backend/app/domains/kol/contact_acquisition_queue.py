"""Provider-free KOL contact acquisition orchestration.

The durable queue stores identifiers, states and counts only.  Reconciliation
is deliberately limited to L0: it reads already-persisted public profile data,
runs the pure contact extractor, writes canonical evidence through
``contact_ingest`` and refreshes contactability.  It never invokes a provider,
fetches a website, or sends a message.  States that require those activities
are hand-off labels for a future, separately authorized workflow.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.db.connection import get_conn


logger = get_logger(__name__)

QUEUE_STATUSES = frozenset(
    {
        "pending_l0",
        "ready",
        "needs_public_profile",
        "needs_website",
        "needs_marketplace_or_dm",
        "suppressed",
        "error",
    }
)
TRIGGER_SOURCES = frozenset(
    {"reconcile", "backfill", "import", "profile_materialization", "deep_crawl"}
)
_BRAND_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{1,127}$")
_INFRA_RESTRICTED_REASONS = frozenset(
    {
        "fingerprint_key_unavailable",
        "suppression_check_unavailable",
        "contact_identity_mismatch",
        "verification_state_incomplete",
        "verification_evidence_missing",
    }
)
_PUBLIC_VERIFICATION_SOURCES = frozenset(
    {
        "youtube_about_declared",
        "ig_business_profile",
        "bio_explicit_contact",
        "website_declared",
    }
)
_EXPLICIT_BIO_CONTACT_ANCHORS = (
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
_IG_PUBLIC_BUSINESS_FIELDS = frozenset(
    {
        "profile.public_email",
        "profile.publicemail",
        "profile.business_email",
        "profile.businessemail",
    }
)
_YOUTUBE_PUBLIC_BUSINESS_FIELDS = frozenset(
    {
        "about.business_email",
        "about.email",
        "profile.about.business_email",
        "profile.about.email",
    }
)
_SOURCE_FIELD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_PLATFORM_PROFILE_HOSTS: dict[str, frozenset[str]] = {
    "instagram": frozenset({"instagram.com"}),
    "ig": frozenset({"instagram.com"}),
    "youtube": frozenset({"youtube.com", "youtu.be"}),
    "yt": frozenset({"youtube.com", "youtu.be"}),
    "tiktok": frozenset({"tiktok.com"}),
    "x": frozenset({"x.com", "twitter.com"}),
    "twitter": frozenset({"x.com", "twitter.com"}),
    "facebook": frozenset({"facebook.com"}),
}
PRIORITY_TIERS = (
    "tier_verified_existing",
    "tier_a_high_fit_public_clue",
    "tier_b_unscored_video_public_clue",
    "tier_c_medium_fit_public_clue",
    "tier_d_other",
)
MAX_ERROR_ATTEMPTS = 5
ERROR_BACKOFF_BASE_SECONDS = 300
ERROR_BACKOFF_MAX_SECONDS = 3600


def _priority_case(pool_alias: str = "p") -> str:
    public_clue = (
        f"(COALESCE({pool_alias}.profile_url, '')<>'' "
        f"OR COALESCE({pool_alias}.bio, '')<>'' "
        f"OR COALESCE(CAST({pool_alias}.raw_platform_data AS TEXT), '') NOT IN ('', '{{}}', 'null'))"
    )
    active_video = (
        "EXISTS (SELECT 1 FROM vkpi_kol_video_evidence v "
        f"WHERE v.kol_pool_id={pool_alias}.id AND COALESCE(v.is_active, TRUE)=TRUE)"
    )
    verified = (
        "EXISTS (SELECT 1 FROM vkpi_kol_pool_contacts c "
        f"WHERE c.kol_pool_id={pool_alias}.id "
        "AND c.verification_status='verified_public_business' "
        "AND c.verified_at IS NOT NULL "
        "AND c.invalidated_at IS NULL AND c.revoked_at IS NULL)"
    )
    return f"""
        CASE
          WHEN {verified} THEN 'tier_verified_existing'
          WHEN {pool_alias}.viltrox_fit_score>=50 AND {public_clue}
            THEN 'tier_a_high_fit_public_clue'
          WHEN {pool_alias}.viltrox_fit_score IS NULL AND {active_video} AND {public_clue}
            THEN 'tier_b_unscored_video_public_clue'
          WHEN {pool_alias}.viltrox_fit_score>=30 AND {pool_alias}.viltrox_fit_score<50
               AND {public_clue}
            THEN 'tier_c_medium_fit_public_clue'
          ELSE 'tier_d_other'
        END
    """


def _priority_rank_case(pool_alias: str = "p") -> str:
    tier_case = _priority_case(pool_alias)
    return f"""
        CASE ({tier_case})
          WHEN 'tier_a_high_fit_public_clue' THEN 0
          WHEN 'tier_b_unscored_video_public_clue' THEN 1
          WHEN 'tier_c_medium_fit_public_clue' THEN 2
          WHEN 'tier_d_other' THEN 3
          ELSE 4
        END
    """


def _positive_id(value: Any, *, field: str = "KOL pool id") -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if result <= 0:
        raise ValueError(f"invalid {field}")
    return result


def _safe_limit(value: Any, *, default: int = 100, maximum: int = 500) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def _brand_scope(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not _BRAND_SCOPE_RE.fullmatch(normalized):
        raise ValueError("invalid brand scope")
    return normalized


def _trigger_source(value: Any) -> str:
    normalized = str(value or "reconcile").strip().lower()
    if normalized not in TRIGGER_SOURCES:
        raise ValueError("invalid contact acquisition trigger source")
    return normalized


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _raw_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_profile_url(value: Any) -> str:
    """Validate a stored profile locator without fetching it."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        from app.domains.kol.contact_ingest import normalize_contact

        return normalize_contact("website", raw).normalized_value
    except Exception:
        return ""


def enqueue_contact_acquisition(
    kol_pool_id: int,
    *,
    trigger_source: str = "reconcile",
    conn: Any | None = None,
) -> dict[str, Any]:
    """Create or re-arm one PII-free queue row.

    A suppressed row is not reactivated by profile updates.  Suppression must
    be released through its own explicit workflow first.
    """

    pool_id = _positive_id(kol_pool_id)
    trigger = _trigger_source(trigger_source)
    db = conn or get_conn()
    try:
        db.execute(
            """
            INSERT INTO vkpi_kol_contact_acquisition_queue
                (kol_pool_id, status, trigger_source, reason_code,
                 attempt_count, next_attempt_at, created_at, updated_at)
            VALUES (?, 'pending_l0', ?, 'queued_for_l0', 0, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(kol_pool_id) DO UPDATE SET
                status=CASE
                    WHEN vkpi_kol_contact_acquisition_queue.status='suppressed'
                    THEN 'suppressed'
                    ELSE 'pending_l0'
                END,
                trigger_source=excluded.trigger_source,
                reason_code=CASE
                    WHEN vkpi_kol_contact_acquisition_queue.status='suppressed'
                    THEN vkpi_kol_contact_acquisition_queue.reason_code
                    ELSE 'queued_for_l0'
                END,
                attempt_count=CASE
                    WHEN vkpi_kol_contact_acquisition_queue.status='suppressed'
                    THEN vkpi_kol_contact_acquisition_queue.attempt_count
                    ELSE 0
                END,
                next_attempt_at=NULL,
                updated_at=CURRENT_TIMESTAMP
            """,
            (pool_id, trigger),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    row = db.execute(
        """
        SELECT status, trigger_source, reason_code, attempt_count
        FROM vkpi_kol_contact_acquisition_queue WHERE kol_pool_id=?
        """,
        (pool_id,),
    ).fetchone()
    current = _row_dict(row)
    return {
        "status": str(current.get("status") or "pending_l0"),
        "kol_pool_id": pool_id,
        "trigger_source": str(current.get("trigger_source") or trigger),
        "reason_code": str(current.get("reason_code") or "queued_for_l0"),
        "attempt_count": int(current.get("attempt_count") or 0),
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def enqueue_contact_acquisitions(
    kol_pool_ids: Iterable[int],
    *,
    trigger_source: str,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Bounded id-only batch enqueue used by materialization/import paths."""

    trigger = _trigger_source(trigger_source)
    unique_ids = sorted({_positive_id(value) for value in kol_pool_ids})
    queued = 0
    for pool_id in unique_ids:
        enqueue_contact_acquisition(pool_id, trigger_source=trigger, conn=conn)
        queued += 1
    return {
        "status": "queued",
        "queued": queued,
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def seed_existing_contact_acquisition_queue(
    *, limit: int = 100, conn: Any | None = None
) -> dict[str, Any]:
    """Queue existing KOLs that have never entered the acquisition pipeline."""

    db = conn or get_conn()
    safe_limit = _safe_limit(limit)
    priority_case = _priority_case("p")
    priority_rank = _priority_rank_case("p")
    rows = db.execute(
        f"""
        SELECT p.id, {priority_case} AS priority_tier
        FROM vkpi_kol_pool p
        LEFT JOIN vkpi_kol_contact_acquisition_queue q
          ON q.kol_pool_id=p.id
        WHERE q.id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM vkpi_kol_pool_contacts verified_contact
              WHERE verified_contact.kol_pool_id=p.id
                AND verified_contact.verification_status='verified_public_business'
                AND verified_contact.verified_at IS NOT NULL
                AND verified_contact.invalidated_at IS NULL
                AND verified_contact.revoked_at IS NULL
          )
        ORDER BY {priority_rank}, p.id
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    pool_ids = [_positive_id(_row_dict(row).get("id")) for row in rows]
    tier_counts = {tier: 0 for tier in PRIORITY_TIERS}
    for row in rows:
        tier = str(_row_dict(row).get("priority_tier") or "tier_d_other")
        tier_counts[tier if tier in tier_counts else "tier_d_other"] += 1
    result = enqueue_contact_acquisitions(
        pool_ids, trigger_source="backfill", conn=db
    ) if pool_ids else {"queued": 0}
    return {
        "status": "seeded",
        "queued": int(result.get("queued") or 0),
        "priority_tier_counts": tier_counts,
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def _candidate_source(
    candidate: dict[str, Any], *, platform: Any = "", source_url: Any = ""
) -> tuple[str, bool, str]:
    source = str(candidate.get("source_type") or "raw_full_scan").strip().lower()
    platform_key = str(platform or "").strip().casefold()
    contact_type = str(candidate.get("contact_type") or "").strip().lower()
    try:
        confidence = float(candidate.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = str(candidate.get("evidence_text") or "")[:280].casefold()
    value = str(candidate.get("contact_value") or "").strip().casefold()
    raw_source_field = str(candidate.get("source_field") or "").strip()
    source_field = (
        raw_source_field
        if _SOURCE_FIELD_RE.fullmatch(raw_source_field)
        else "raw_platform_data"
    )
    field_key = source_field.casefold()
    try:
        source_host = (urlsplit(str(source_url or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        source_host = ""
    expected_hosts = _PLATFORM_PROFILE_HOSTS.get(platform_key, frozenset())
    platform_host_matches = bool(
        source_host
        and expected_hosts
        and any(source_host == host or source_host.endswith("." + host) for host in expected_hosts)
    )
    bounded_identity_proof = bool(
        value
        and value in evidence
        and any(anchor in evidence for anchor in _EXPLICIT_BIO_CONTACT_ANCHORS)
    )
    field_identity_proof = bool(value and value in evidence)
    public_bio_field = bool(
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
    if source == "ig_business_profile" and not (
        platform_key in {"instagram", "ig"}
        and platform_host_matches
        and field_key in _IG_PUBLIC_BUSINESS_FIELDS
        and field_identity_proof
    ):
        source = "raw_bio_scan"
    if source == "youtube_about_declared" and not (
        platform_key in {"youtube", "yt"}
        and platform_host_matches
        and field_key in _YOUTUBE_PUBLIC_BUSINESS_FIELDS
        and field_identity_proof
    ):
        source = "raw_bio_scan"
    # A zero-provider L0 cycle cannot prove a website contact page, even if an
    # untrusted candidate labels itself ``website_declared``.
    if source == "website_declared":
        source = "raw_bio_scan"
    # ``_email_confidence`` historically raises the whole bio to .9 when an
    # unrelated business word occurs far from an email.  Promotion therefore
    # also requires bounded evidence containing both this exact email and an
    # explicit nearby contact anchor.  Confidence alone is never identity proof.
    if source == "raw_bio_scan" and platform_host_matches and public_bio_field and contact_type in {
        "email", "business_email", "public_email", "contact_email"
    } and confidence >= 0.85 and bounded_identity_proof:
        source = "bio_explicit_contact"
    if source == "bio_explicit_contact" and not (
        platform_host_matches
        and public_bio_field
        and confidence >= 0.85
        and bounded_identity_proof
    ):
        source = "raw_bio_scan"
    public_declared = source in _PUBLIC_VERIFICATION_SOURCES and confidence >= 0.85
    return source, public_declared, source_field


def _queue_update(
    db: Any,
    *,
    kol_pool_id: int,
    status: str,
    reason_code: str,
    contactability_score: float | None,
) -> None:
    if status not in QUEUE_STATUSES:
        raise ValueError("invalid contact acquisition status")
    current = db.execute(
        "SELECT attempt_count FROM vkpi_kol_contact_acquisition_queue WHERE kol_pool_id=?",
        (int(kol_pool_id),),
    ).fetchone()
    current_attempt = int(_row_dict(current).get("attempt_count") or 0)
    next_attempt_at: str | None = None
    if status == "error" and current_attempt + 1 < MAX_ERROR_ATTEMPTS:
        delay_seconds = min(
            ERROR_BACKOFF_MAX_SECONDS,
            ERROR_BACKOFF_BASE_SECONDS * (2 ** max(0, current_attempt)),
        )
        next_attempt_at = (
            datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    db.execute(
        """
        UPDATE vkpi_kol_contact_acquisition_queue
        SET status=?, reason_code=?,
            attempt_count=COALESCE(attempt_count, 0)+1,
            contactability_score=?, last_reconciled_at=CURRENT_TIMESTAMP,
            next_attempt_at=?, updated_at=CURRENT_TIMESTAMP
        WHERE kol_pool_id=?
        """,
        (
            status,
            str(reason_code or "")[:120],
            contactability_score,
            next_attempt_at,
            int(kol_pool_id),
        ),
    )
    db.commit()


def _next_manual_state(*, has_profile: bool, has_website: bool) -> tuple[str, str]:
    if not has_profile:
        return "needs_public_profile", "profile_url_missing"
    if has_website:
        return "needs_website", "public_website_review_required"
    return "needs_marketplace_or_dm", "marketplace_or_dm_required"


def _pool_priority_tier(db: Any, kol_pool_id: int) -> str:
    try:
        row = db.execute(
            f"SELECT {_priority_case('p')} AS priority_tier FROM vkpi_kol_pool p WHERE p.id=?",
            (int(kol_pool_id),),
        ).fetchone()
        tier = str(_row_dict(row).get("priority_tier") or "tier_d_other")
    except Exception:
        tier = "tier_d_other"
    return tier if tier in PRIORITY_TIERS else "tier_d_other"


def reconcile_contact_acquisition(
    kol_pool_id: int,
    *,
    brand_scope: str,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Run one stored-data-only L0 reconciliation and persist its next state."""

    pool_id = _positive_id(kol_pool_id)
    scope = _brand_scope(brand_scope)
    db = conn or get_conn()
    candidates: list[dict[str, Any]] = []
    ingested = 0
    rejected = 0
    eligible_count = 0
    final_status = "error"
    reason_code = "reconcile_failed"
    durable_state_written = True
    contactability_score: float | None = None
    priority_tier = _pool_priority_tier(db, pool_id)
    try:
        queue_row = db.execute(
            "SELECT status FROM vkpi_kol_contact_acquisition_queue WHERE kol_pool_id=?",
            (pool_id,),
        ).fetchone()
        if queue_row is None:
            enqueue_contact_acquisition(pool_id, trigger_source="reconcile", conn=db)

        pool_row = db.execute(
            """
            SELECT id, platform, profile_url, raw_platform_data
            FROM vkpi_kol_pool WHERE id=?
            """,
            (pool_id,),
        ).fetchone()
        if pool_row is None:
            final_status, reason_code = "error", "kol_not_found"
            _queue_update(
                db,
                kol_pool_id=pool_id,
                status=final_status,
                reason_code=reason_code,
                contactability_score=None,
            )
            return _reconcile_result(
                pool_id,
                final_status,
                reason_code,
                candidates=0,
                ingested=0,
                rejected=0,
                eligible=0,
                contactability_score=None,
                priority_tier=priority_tier,
            )

        pool = _row_dict(pool_row)
        profile_url = _safe_profile_url(pool.get("profile_url"))
        from app.domains.kol.business_contact_extract import extract_contacts_multi_source
        from app.domains.kol.contact_ingest import ContactValidationError, ingest_contact

        candidates = extract_contacts_multi_source(
            _raw_payload(pool.get("raw_platform_data")),
            platform=str(pool.get("platform") or ""),
            source_url=profile_url,
        )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                rejected += 1
                continue
            source, public_declared, source_field = _candidate_source(
                candidate,
                platform=pool.get("platform"),
                source_url=profile_url,
            )
            try:
                ingest_contact(
                    kol_pool_id=pool_id,
                    contact_type=str(candidate.get("contact_type") or ""),
                    contact_value=str(candidate.get("contact_value") or ""),
                    source_type=source,
                    source_url=profile_url,
                    source_field=source_field,
                    evidence_text=str(candidate.get("evidence_text") or ""),
                    confidence=float(candidate.get("confidence") or 0.0),
                    is_public_declared=public_declared,
                    verification_status=(
                        "verified_public_business" if public_declared else "observed"
                    ),
                    consent_basis=(
                        "legitimate_interest_public_business"
                        if public_declared
                        else "source_observation"
                    ),
                    conn=db,
                )
                ingested += 1
            except (ContactValidationError, TypeError, ValueError):
                # Candidate values and evidence are never logged or returned.
                rejected += 1

        from app.domains.kol.contact_system import refresh_contactability

        refresh = refresh_contactability(pool_id, conn=db)
        if refresh.get("written") is not True:
            raise RuntimeError("contactability refresh unavailable")
        try:
            contactability_score = float(refresh.get("score") or 0.0)
        except (TypeError, ValueError):
            contactability_score = 0.0

        contact_rows = db.execute(
            """
            SELECT id, COALESCE(NULLIF(channel, ''), contact_type) AS channel
            FROM vkpi_kol_pool_contacts WHERE kol_pool_id=? ORDER BY id
            """,
            (pool_id,),
        ).fetchall()
        from app.domains.kol.contact_suppression import contact_eligibility

        reasons: list[str] = []
        for contact_row in contact_rows:
            contact_id = _positive_id(_row_dict(contact_row).get("id"), field="contact id")
            verdict = contact_eligibility(
                contact_id=contact_id,
                kol_pool_id=pool_id,
                brand_scope=scope,
                conn=db,
            )
            if verdict.get("eligible") is True and verdict.get("status") == "eligible":
                eligible_count += 1
            else:
                reasons.append(str(verdict.get("reason") or "verification_not_eligible"))

        if eligible_count:
            final_status, reason_code = "ready", "verified_contact_ready"
        elif contact_rows and reasons and all(reason == "suppressed" for reason in reasons):
            final_status, reason_code = "suppressed", "all_contacts_suppressed"
        elif any(reason in _INFRA_RESTRICTED_REASONS for reason in reasons):
            final_status, reason_code = "error", "eligibility_gate_unavailable"
        else:
            has_website = any(
                str(candidate.get("contact_type") or "").strip().lower()
                in {"website", "link_hub"}
                for candidate in candidates
                if isinstance(candidate, dict)
            ) or any(
                str(_row_dict(contact_row).get("channel") or "").strip().lower()
                in {"website", "link_hub"}
                for contact_row in contact_rows
            )
            final_status, reason_code = _next_manual_state(
                has_profile=bool(profile_url), has_website=has_website
            )

        _queue_update(
            db,
            kol_pool_id=pool_id,
            status=final_status,
            reason_code=reason_code,
            contactability_score=contactability_score,
        )
    except Exception as exc:
        rollback_ok = True
        try:
            db.rollback()
        except Exception as rollback_exc:
            rollback_ok = False
            logger.warning(
                "contact acquisition rollback failed kol=%s error_type=%s",
                pool_id,
                type(rollback_exc).__name__,
            )
        logger.warning(
            "contact acquisition L0 failed kol=%s error_type=%s",
            pool_id,
            type(exc).__name__,
        )
        durable_state_written = False
        if rollback_ok:
            try:
                _queue_update(
                    db,
                    kol_pool_id=pool_id,
                    status="error",
                    reason_code="reconcile_failed",
                    contactability_score=contactability_score,
                )
                durable_state_written = True
            except Exception as update_exc:
                logger.warning(
                    "contact acquisition error-state update failed kol=%s error_type=%s",
                    pool_id,
                    type(update_exc).__name__,
                )
        final_status, reason_code = "error", "reconcile_failed"

    return _reconcile_result(
        pool_id,
        final_status,
        reason_code,
        candidates=len(candidates),
        ingested=ingested,
        rejected=rejected,
        eligible=eligible_count,
        contactability_score=contactability_score,
        priority_tier=priority_tier,
        durable_state_written=durable_state_written,
    )


def _reconcile_result(
    kol_pool_id: int,
    status: str,
    reason_code: str,
    *,
    candidates: int,
    ingested: int,
    rejected: int,
    eligible: int,
    contactability_score: float | None,
    priority_tier: str,
    durable_state_written: bool = True,
) -> dict[str, Any]:
    """Build a count-only result; never include contact or evidence values."""

    return {
        "status": status,
        "kol_pool_id": int(kol_pool_id),
        "reason_code": reason_code,
        "priority_tier": priority_tier if priority_tier in PRIORITY_TIERS else "tier_d_other",
        "l0_candidate_count": int(candidates),
        "ingested_count": int(ingested),
        "rejected_count": int(rejected),
        "eligible_contact_count": int(eligible),
        "contactability_score": contactability_score,
        "contactability_score_kind": "contact_clue_score" if contactability_score is not None else None,
        "durable_state_written": bool(durable_state_written),
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def reconcile_pending_contact_acquisition(
    *,
    brand_scope: str,
    limit: int = 100,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Run a bounded worker cycle over durable L0 rows only."""

    scope = _brand_scope(brand_scope)
    db = conn or get_conn()
    priority_case = _priority_case("p")
    priority_rank = _priority_rank_case("p")
    rows = db.execute(
        f"""
        SELECT q.kol_pool_id, {priority_case} AS priority_tier
        FROM vkpi_kol_contact_acquisition_queue q
        JOIN vkpi_kol_pool p ON p.id=q.kol_pool_id
        WHERE (
            q.status='pending_l0'
            OR (
                q.status='error'
                AND q.attempt_count<?
                AND q.next_attempt_at IS NOT NULL
                AND q.next_attempt_at<=CURRENT_TIMESTAMP
            )
        )
        ORDER BY {priority_rank}, q.id
        LIMIT ?
        """,
        (MAX_ERROR_ATTEMPTS, _safe_limit(limit)),
    ).fetchall()
    totals = {status: 0 for status in QUEUE_STATUSES}
    tier_counts = {tier: 0 for tier in PRIORITY_TIERS}
    for row in rows:
        selected_tier = str(_row_dict(row).get("priority_tier") or "tier_d_other")
        tier_counts[selected_tier if selected_tier in tier_counts else "tier_d_other"] += 1
        result = reconcile_contact_acquisition(
            _positive_id(_row_dict(row).get("kol_pool_id")),
            brand_scope=scope,
            conn=db,
        )
        if result.get("durable_state_written") is False:
            raise RuntimeError("contact acquisition durable state unavailable")
        state = str(result.get("status") or "error")
        totals[state if state in totals else "error"] += 1
    return {
        "status": "completed",
        "processed": len(rows),
        "state_counts": totals,
        "priority_tier_counts": tier_counts,
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


__all__ = [
    "QUEUE_STATUSES",
    "TRIGGER_SOURCES",
    "PRIORITY_TIERS",
    "MAX_ERROR_ATTEMPTS",
    "enqueue_contact_acquisition",
    "enqueue_contact_acquisitions",
    "seed_existing_contact_acquisition_queue",
    "reconcile_contact_acquisition",
    "reconcile_pending_contact_acquisition",
]
