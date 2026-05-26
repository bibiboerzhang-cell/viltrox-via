"""
services/student_identity.py — compatibility exports for QR-first student identity runtime.
"""
from __future__ import annotations

from app.db.repositories.student_identity import create_or_update_school, get_school
from app.services.student_identity_common import (
    STUDENT_COMMISSION_RATE,
    STUDENT_PASS_TTL_SEC,
    _creator_code_candidates,
    _creator_code_path_url,
    _csv_rows_from_text,
    _derive_student_display_name,
    _is_creator_code_conflict,
    _load_json,
    _normalize_public_vid,
    _normalize_school_student_id,
    _parse_timestamp,
    _public_student_claim_id,
    _public_vid_url,
    _safe_slug,
    _shop_url_for_creator,
    _sign_claim,
    _sign_pass_token,
    _student_email_domains_for_school,
    _to_upload_url,
    _utcnow,
    _validate_student_email_domain,
)
from app.services.student_identity_claims import (
    _generate_student_id_code,
    _validate_static_claim,
    build_student_pass,
    claim_student_identity_for_user,
    consume_student_pass,
    create_student_qr_batch,
    get_student_claim_metadata,
    reissue_student_qr,
    resolve_student_identity_code,
    revoke_student_qr,
    signup_student_from_qr,
    validate_student_identity_email,
)
from app.services.student_identity_dashboard import (
    _recent_student_anomalies,
    _recent_student_events,
    _school_stats,
    _student_batch_progress,
    _student_school_funnels,
    build_student_batch_detail,
    build_student_detail,
    build_student_funnel_snapshot,
    build_student_overview,
    build_student_roster,
    list_student_schools_with_stats,
)
from app.services.student_identity_defaults import (
    _ensure_student_identity_registry_schema,
    _school_from_student_id_code,
    ensure_student_identity_registry_defaults,
    ensure_student_school_defaults,
)
from app.services.student_identity_public import (
    _find_creator_user_by_code,
    _find_student_qr_by_vid,
    _load_public_creator_activity,
    _public_creator_profile_payload,
    _public_vid_for_qr,
    _student_signup_url,
    build_public_vid_profile,
    build_public_vid_qr_png,
    build_public_vid_share_card,
    resolve_student_qr_scan_destination,
)

__all__ = [name for name in globals() if not name.startswith("__")]
