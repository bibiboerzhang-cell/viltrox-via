"""Synchronous helper functions shared by audit router paths."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.db.repositories.assets import (
    attach_uploaded_asset_to_submission,
    register_submission_asset,
    set_submission_video_r2_key,
)
from app.db.repositories.submissions import create_submission_stub
from app.services.ai.analyzers.claude_text import check_content_similarity
from app.services.media.storage import normalize_uploaded_video_payload

logger = get_logger(__name__)


def _resolve_uploaded_video_payload_sync(uploaded_video) -> dict | None:
    if not uploaded_video:
        return None
    payload = uploaded_video.model_dump() if hasattr(uploaded_video, "model_dump") else dict(uploaded_video)
    asset_id = int(payload.get("asset_id") or 0)
    if asset_id <= 0:
        return payload
    row = get_conn().execute(
        """
        SELECT id, storage_key, mime_type, size_bytes
        FROM submission_assets
        WHERE id=?
        """,
        (asset_id,),
    ).fetchone()
    if not row:
        return normalize_uploaded_video_payload(payload)
    storage_key = str(row["storage_key"] or "").strip()
    payload["asset_id"] = int(row["id"] or asset_id)
    payload["storage_key"] = storage_key
    if storage_key.startswith("videos/"):
        payload["r2_key"] = payload.get("r2_key") or storage_key
    elif storage_key and not payload.get("path"):
        payload["path"] = storage_key
    payload["mime_type"] = payload.get("mime_type") or str(row["mime_type"] or "")
    if not payload.get("size_mb") and row["size_bytes"]:
        payload["size_mb"] = round(int(row["size_bytes"]) / (1024 * 1024), 2)
    return normalize_uploaded_video_payload(payload)


def _check_similarity_sync(handle_for_sim: str, title: str, platform: str, url: str):
    sim_conn = get_conn()
    return check_content_similarity(
        sim_conn,
        handle_for_sim,
        title,
        platform,
        url=url,
    )


def _auto_create_verification_sync(platform_for_ver: str, handle_for_ver: str) -> None:
    conn_v = get_conn()
    cv = conn_v.cursor()
    plat_key = platform_for_ver.lower()
    handle_clean = handle_for_ver.lstrip("@")
    existing = cv.execute(
        "SELECT id, status FROM verifications WHERE platform=? AND handle=?",
        (plat_key, handle_clean),
    ).fetchone()
    if existing:
        return
    import random

    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    auto_code = "VLTRX-" + "".join(random.choices(chars, k=6))
    cv.execute(
        "INSERT INTO verifications (created_at,platform,handle,code,status,note) VALUES (?,?,?,?,?,?)",
        (
            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            plat_key,
            handle_clean,
            auto_code,
            "pending",
            f"Auto-created from submission. Ask creator to DM {auto_code} to @viltrox.official",
        ),
    )
    conn_v.commit()


def _create_submission_stub_sync(
    current_uid: int | None,
    request_url: str,
    title: str,
    caption: str,
    raw_text: str,
    platform: str,
    extracted_handle: str,
    uploaded_video_path: str,
    uploaded_video_filename: str,
) -> int:
    return create_submission_stub(
        user_id=current_uid,
        url=request_url,
        title=title,
        caption=caption,
        raw_text=raw_text,
        platform=platform,
        extracted_handle=extracted_handle,
        uploaded_video_path=uploaded_video_path,
        uploaded_video_filename=uploaded_video_filename,
    )


def _emit_submission_to_party_layer(
    *,
    submission_id: int,
    user_id: int | None,
    platform: str,
    url: str,
    extracted_handle: str,
    title: str,
) -> None:
    """
    Phase 1 wire: submission create → party (via user_id) → creator.submission_created event.

    Silently no-ops on SQLite dev or if party layer not applied.
    """
    from app.db.connection import is_postgres_runtime

    if not is_postgres_runtime():
        return

    from app.services.party.party_service import get_or_create_by_user_id, _touch_party
    from app.services.party.event_writer import emit_creator_submission_created

    party_id: str | None = None
    creator_code = ""
    if user_id:
        party_id = get_or_create_by_user_id(user_id, origin_source="creator_api")
        if party_id:
            _touch_party(party_id, is_creator=True, lifecycle_stage="creator")

        # Opportunistic: pick creator_code from users if available
        try:
            from app.db.connection import get_conn
            row = get_conn().execute(
                "SELECT creator_code FROM users WHERE id = ? LIMIT 1",
                (int(user_id),),
            ).fetchone()
            if row:
                creator_code = str(row["creator_code"] or "")
        except Exception:
            logger.debug("party.creator_code_lookup_failed", exc_info=True)

    emit_creator_submission_created(
        party_id=party_id,
        submission_id=submission_id,
        platform=platform or "",
        url=url or "",
        creator_code=creator_code,
        handle=extracted_handle or "",
        extra={"title": title or ""},
    )


def _bind_uploaded_asset_sync(
    submission_id: int,
    resolved_uploaded_video: dict | None,
    uploaded_video_path: str,
):
    asset = attach_uploaded_asset_to_submission(
        submission_id=submission_id,
        asset_id=int((resolved_uploaded_video or {}).get("asset_id") or 0),
        r2_key=str((resolved_uploaded_video or {}).get("r2_key") or ""),
        local_path=uploaded_video_path,
    )
    if asset is None and (str((resolved_uploaded_video or {}).get("r2_key") or "") or uploaded_video_path):
        asset_id = register_submission_asset(
            submission_id=submission_id,
            asset_role="uploaded_video",
            storage_key=str((resolved_uploaded_video or {}).get("r2_key") or "") or uploaded_video_path,
            mime_type=str((resolved_uploaded_video or {}).get("mime_type") or "application/octet-stream"),
            size_bytes=int(float((resolved_uploaded_video or {}).get("size_mb") or 0) * 1024 * 1024),
        )
        asset = {
            "id": asset_id,
            "submission_id": submission_id,
            "storage_key": str((resolved_uploaded_video or {}).get("r2_key") or "") or uploaded_video_path,
        }
    storage_key = str((asset or {}).get("storage_key") or "").strip()
    if storage_key.startswith("videos/"):
        set_submission_video_r2_key(submission_id, storage_key, force=False)
    return asset
