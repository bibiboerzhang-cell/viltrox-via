"""Durable, provider-fenced metric refreshes for existing KOL video evidence.

The enqueue boundary is safe for HTTP callers and never invokes a provider.
Provider work happens only through :func:`run_video_metric_refresh_for_job`,
which is called by the fenced ``apify_jobs`` worker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.permissions import check_tab_permission
from app.core.security import user_status_allows_auth
from app.db.connection import get_conn
from app.domains import content_metric_snapshots
from app.domains.kol.video_url_identity import (
    VideoUrlIdentityError,
    parse_supported_video_url,
)
from app.domains.projects.workflow_evidence import _fetch_video_metadata
from app.domains.tasks.apify_idempotency import (
    active_job_idempotency_key,
    enqueue_active_apify_job,
)
from app.platform.apify_budget import ApifyBudgetBlocked, ApifyProviderReplayBlocked


VIDEO_METRIC_REFRESH_JOB_TYPE = "kol_video_metric_refresh"
SUPPORTED_METRIC_PLATFORMS = frozenset({"youtube", "instagram", "tiktok"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _video_identity(value: Any) -> tuple[str, str] | None:
    try:
        identity = parse_supported_video_url(value)
    except VideoUrlIdentityError:
        return None
    return identity.platform, identity.video_id


def enqueue_video_metric_refresh(
    conn: Any,
    *,
    evidence: dict[str, Any],
    kol_pool_id: int,
    staff: dict[str, Any] | None,
    source: str = "my_kol_video_tracking",
    queue_lane: str = "interactive",
) -> dict[str, Any]:
    """Insert or reuse one active refresh job without making provider calls."""

    evidence_id = _int(evidence.get("id"))
    evidence_kol_id = _int(evidence.get("kol_pool_id"))
    platform = _text(evidence.get("platform")).lower()
    content_url = _text(evidence.get("content_url"))
    if evidence_id <= 0 or evidence_kol_id != int(kol_pool_id):
        raise ValueError("video_evidence_target_mismatch")
    if platform not in SUPPORTED_METRIC_PLATFORMS:
        raise ValueError("video_metric_platform_unsupported")
    identity = _video_identity(content_url)
    if not identity or identity[0] != platform:
        raise ValueError("video_evidence_identity_invalid")
    normalized_lane = _text(queue_lane).lower() or "interactive"
    if normalized_lane not in {"interactive", "batch"}:
        raise ValueError("video_metric_queue_lane_invalid")

    actor_id = _int((staff or {}).get("id") or (staff or {}).get("staff_id"))
    payload = {
        "queue_lane": normalized_lane,
        "target_type": "kol_video_evidence",
        "target_id": str(evidence_id),
        "evidence_id": evidence_id,
        "kol_pool_id": int(kol_pool_id),
        "platform": platform,
        "content_url": content_url,
        "derive_method": "content_metric_refresh_v1",
        "source": _text(source)[:80] or "my_kol_video_tracking",
        "staff_id": actor_id or None,
        "triggered_by_user_id": (staff or {}).get("user_id"),
    }
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=VIDEO_METRIC_REFRESH_JOB_TYPE,
        payload=payload,
        idempotency_key=active_job_idempotency_key(
            VIDEO_METRIC_REFRESH_JOB_TYPE,
            evidence_id,
        ),
    )
    return {
        "status": "queued" if inserted else "already_queued",
        "job_id": int(job["id"]),
        "job_type": VIDEO_METRIC_REFRESH_JOB_TYPE,
        "evidence_id": evidence_id,
        "provider_calls_performed": False,
    }


def _load_evidence(conn: Any, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, kol_pool_id, content_url, platform, evidence_type, is_active,
               channel_id
        FROM vkpi_kol_video_evidence
        WHERE id=?
        """,
        (int(evidence_id),),
    ).fetchone()
    return dict(row) if row else None


def authorize_video_metric_refresh_actor(
    conn: Any,
    *,
    staff_id: int,
    kol_pool_id: int,
) -> tuple[dict[str, Any] | None, str]:
    """Revalidate the durable job actor immediately before paid work."""

    if int(staff_id) <= 0:
        return None, "video_refresh_actor_missing"
    row = conn.execute(
        """
        SELECT s.*, u.status AS user_status, u.email AS email
        FROM staff s
        JOIN users u ON u.id=s.user_id
        WHERE s.id=?
        LIMIT 1
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        return None, "video_refresh_actor_inactive"
    actor = dict(row)
    active = actor.get("active")
    if active not in (True, 1, "1") or _text(actor.get("suspended_at")):
        return None, "video_refresh_actor_inactive"
    if not user_status_allows_auth(actor.get("user_status"), production=True):
        return None, "video_refresh_actor_inactive"
    if not check_tab_permission(actor, "vkpi", "write"):
        return None, "video_refresh_actor_permission_revoked"
    try:
        # Local import avoids the module-level enqueue dependency cycle.
        from app.domains.kol.video_tracking import (
            VideoTrackingError,
            _assert_target_writable,
        )

        _assert_target_writable(
            conn,
            kol_pool_id=int(kol_pool_id),
            staff=actor,
        )
    except VideoTrackingError:
        return None, "video_refresh_target_permission_revoked"
    return actor, ""


def _failure(
    conn: Any,
    *,
    evidence_id: int,
    fetched_at: str,
    error_code: str,
    provider: str,
    run_id: str | None = None,
    quality_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    result = content_metric_snapshots.record_failed_refresh(
        conn,
        evidence_id=evidence_id,
        provider=provider,
        fetched_at=fetched_at,
        error_code=error_code[:80],
        run_id=run_id,
        quality_flags=quality_flags,
    )
    conn.commit()
    return {
        "status": "failed",
        "evidence_id": evidence_id,
        "error_code": error_code[:80],
        "snapshot_id": (result.get("snapshot") or {}).get("id"),
        "provider_calls_performed": True,
    }


def _identity_rejection(
    *,
    evidence_id: int,
    error_code: str,
    provider_calls_performed: bool = True,
) -> dict[str, Any]:
    """Reject provider data that cannot be bound to this evidence, without writes."""

    return {
        "status": "blocked",
        "evidence_id": evidence_id,
        "error_code": error_code[:80],
        "snapshot_id": None,
        "provider_calls_performed": bool(provider_calls_performed),
    }


def run_video_metric_refresh_for_job(
    payload: dict[str, Any],
    *,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Refresh one evidence row inside the worker's provider execution fence."""

    db = conn or get_conn()
    evidence_id = _int(payload.get("evidence_id") or payload.get("target_id"))
    expected_kol_id = _int(payload.get("kol_pool_id"))
    if evidence_id <= 0 or expected_kol_id <= 0:
        raise ValueError("video_metric_refresh_payload_invalid")
    evidence = _load_evidence(db, evidence_id)
    if not evidence:
        raise LookupError("video_evidence_not_found")
    if _int(evidence.get("kol_pool_id")) != expected_kol_id:
        raise PermissionError("video_evidence_target_mismatch")
    if evidence.get("is_active") in (False, 0):
        raise ValueError("video_evidence_inactive")
    if _text(evidence.get("evidence_type") or "video").lower() != "video":
        raise ValueError("video_evidence_not_video")
    platform = _text(evidence.get("platform")).lower()
    if platform not in SUPPORTED_METRIC_PLATFORMS:
        raise ValueError("video_metric_platform_unsupported")
    stored_identity = _video_identity(evidence.get("content_url"))
    if not stored_identity or stored_identity[0] != platform:
        return _identity_rejection(
            evidence_id=evidence_id,
            error_code="video_evidence_identity_invalid",
            provider_calls_performed=False,
        )
    queued_identity = _video_identity(payload.get("content_url"))
    queued_platform = _text(payload.get("platform")).lower()
    if queued_identity != stored_identity or queued_platform != platform:
        return _identity_rejection(
            evidence_id=evidence_id,
            error_code="video_evidence_identity_changed_after_enqueue",
            provider_calls_performed=False,
        )
    actor, actor_error = authorize_video_metric_refresh_actor(
        db,
        staff_id=_int(payload.get("staff_id")),
        kol_pool_id=expected_kol_id,
    )
    queued_user_id = _int(payload.get("triggered_by_user_id"))
    if actor is None or (
        queued_user_id > 0 and queued_user_id != _int(actor.get("user_id"))
    ):
        return _identity_rejection(
            evidence_id=evidence_id,
            error_code=(
                "video_refresh_actor_identity_changed"
                if actor is not None
                else actor_error
            ),
            provider_calls_performed=False,
        )

    fetched_at = _utcnow()
    try:
        metadata = dict(_fetch_video_metadata(_text(evidence.get("content_url"))) or {})
    except (ApifyBudgetBlocked, ApifyProviderReplayBlocked):
        # Preserve the worker's typed hard-stop/replay-fence handling.  These
        # exceptions do not prove that a provider observation exists and must
        # never be flattened into a normal failed snapshot.
        raise
    except Exception as exc:
        return _failure(
            db,
            evidence_id=evidence_id,
            fetched_at=fetched_at,
            error_code=content_metric_snapshots.error_code_from_exception(exc),
            provider=platform,
            quality_flags=("provider_exception",),
        )

    provider = _text(metadata.get("scrape_source") or platform).lower()[:120]
    run_id = _text(metadata.get("apify_run_id")) or None
    if _text(metadata.get("scrape_status")).lower() != "success":
        return _failure(
            db,
            evidence_id=evidence_id,
            fetched_at=fetched_at,
            error_code="provider_refresh_not_successful",
            provider=provider,
            run_id=run_id,
            quality_flags=("provider_response_not_success",),
        )
    returned_platform = _text(metadata.get("platform")).lower()
    returned_identity = _video_identity(metadata.get("content_url"))
    if returned_platform != platform or returned_identity != stored_identity:
        return _identity_rejection(
            evidence_id=evidence_id,
            error_code="provider_video_mismatch",
        )
    stored_channel_id = _text(evidence.get("channel_id"))
    returned_channel_id = _text(metadata.get("channel_id"))
    if stored_channel_id and returned_channel_id and stored_channel_id != returned_channel_id:
        return _identity_rejection(
            evidence_id=evidence_id,
            error_code="provider_creator_mismatch",
        )
    media_kind = _text(metadata.get("media_kind")).lower()
    if media_kind and media_kind != "video":
        return _failure(
            db,
            evidence_id=evidence_id,
            fetched_at=fetched_at,
            error_code="provider_media_kind_mismatch",
            provider=provider,
            run_id=run_id,
            quality_flags=("provider_non_video_response",),
        )

    metrics = {
        "views": metadata.get("view_count"),
        "likes": metadata.get("like_count"),
        "comments": metadata.get("comment_count"),
        "shares": metadata.get("share_count"),
    }
    if not content_metric_snapshots.has_any_metric(**metrics):
        return _failure(
            db,
            evidence_id=evidence_id,
            fetched_at=fetched_at,
            error_code="all_metrics_missing",
            provider=provider,
            run_id=run_id,
            quality_flags=("provider_response_returned",),
        )

    result = content_metric_snapshots.record_successful_refresh(
        db,
        evidence_id=evidence_id,
        provider=provider,
        fetched_at=fetched_at,
        source_observed_at=fetched_at,
        run_id=run_id,
        **metrics,
    )
    db.commit()
    return {
        "status": "success",
        "evidence_id": evidence_id,
        "snapshot_id": (result.get("snapshot") or {}).get("id"),
        "latest_updated": bool(result.get("latest_updated")),
        "provider": provider,
        "provider_calls_performed": True,
    }
