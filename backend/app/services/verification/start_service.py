"""
services/verification/start_service.py — verification start flow
"""
from __future__ import annotations

from app.db.repositories.users import upsert_pending_social_account
from app.db.repositories.verifications import build_verification_expiry, create_verification_request
from app.services.verification.comment_generator import generate_template_comment, generate_verification_code
from app.services.verification.viltrox_official import (
    VILTROX_OFFICIAL_ACCOUNTS,
    build_profile_url,
    get_viltrox_display_name,
    normalize_claimed_handle,
)


def start_verification_request(
    *,
    user_id: int,
    platform: str,
    handle: str,
    profile_url: str,
    note: str = "",
) -> dict:
    normalized_handle = normalize_claimed_handle(handle, platform)
    canonical_profile_url = profile_url or build_profile_url(platform, normalized_handle)
    code = generate_verification_code()
    expires_at = build_verification_expiry()
    comment_text, _ = generate_template_comment(code=code)
    social_account_id = upsert_pending_social_account(
        user_id=user_id,
        platform=platform,
        handle=normalized_handle,
        verify_code=code,
    )
    verification_id = create_verification_request(
        user_id=user_id,
        platform=platform,
        handle=normalized_handle,
        code=code,
        profile_url=canonical_profile_url,
        generated_comment=comment_text,
        expires_at=expires_at,
        note=note,
    )
    viltrox_url = VILTROX_OFFICIAL_ACCOUNTS.get(platform, "")
    viltrox_name = get_viltrox_display_name(platform)
    instructions = [
        "1. Copy the comment below",
        f"2. Open {viltrox_name}",
        "3. Paste it into the comments on any of the latest 10 posts",
        "4. Click 'I've Posted' below",
        "5. We'll auto-scan and approve as soon as the code is detected",
    ]
    return {
        "social_account_id": social_account_id,
        "verification_id": verification_id,
        "platform": platform,
        "handle": normalized_handle,
        "profile_url": canonical_profile_url,
        "code": code,
        "expires_at": expires_at,
        "generated_comment": comment_text,
        "viltrox_account_url": viltrox_url,
        "viltrox_account_name": viltrox_name,
        "instructions": instructions,
        "status": "pending",
        "comment_enrichment": "queued",
    }
