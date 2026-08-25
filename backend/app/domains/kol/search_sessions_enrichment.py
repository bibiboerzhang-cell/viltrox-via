"""Read-only enrichment status projection for KOL search sessions."""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.kol.search_sessions_schema import PENDING_ENRICHMENT_STATUSES
from app.domains.kol.search_sessions_serde import _int_or_none, _text

logger = get_logger(__name__)


def _enrichment_preview_status(item: dict[str, Any], key: str, *, ready: bool) -> str:
    if ready:
        return "ready"
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    profile_execute = payload.get("profile_execute") if isinstance(payload.get("profile_execute"), dict) else {}
    explicit = profile_execute.get(key) if isinstance(profile_execute.get(key), dict) else {}
    explicit_status = _text(explicit.get("status")).lower()
    if explicit_status in PENDING_ENRICHMENT_STATUSES:
        return "pending"
    if explicit_status in {"ok", "ready", "done"}:
        return "ready"
    if explicit_status in {"no_contacts", "no_commenters", "no_posts", "no_raw", "not_found"}:
        return "empty"
    if explicit_status in {"error", "failed", "partial"}:
        return "partial"
    job = payload.get("profile_advance_job") if isinstance(payload.get("profile_advance_job"), dict) else {}
    item_status = _text(item.get("status")).lower()
    job_status = _text(job.get("status")).lower()
    if item_status in {"queued", "running"} or job_status in PENDING_ENRICHMENT_STATUSES:
        return "pending"
    return "missing"


def _refresh_enrichment_queue_states(conn: Any, items: list[dict[str, Any]]) -> None:
    """Attach current queue truth to the in-memory search-session projection.

    Session items can outlive the worker snapshot stored in ``profile_flow`` or
    ``profile_advance_job``.  Refresh every concrete profile/enrichment job
    reference before the progress reducer runs so a terminal apify job can
    close an old ``queued`` marker, while a genuinely queued/running retry is
    still preserved.  This function mutates only the DTOs loaded for the GET;
    it never writes the historical session rows.
    """

    job_ids: set[int] = set()
    queue_targets: list[tuple[dict[str, Any], int, str]] = []
    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        profile_containers = [
            value
            for value in (payload.get("profile_flow"), payload.get("profile_execute"))
            if isinstance(value, dict)
        ]

        for profile in profile_containers:
            profile_job_id = _int_or_none(profile.get("job_id"))
            if profile_job_id:
                job_ids.add(profile_job_id)
                queue_targets.append((profile, profile_job_id, "profile"))
            for key in ("contact_enrichment", "audience_enrichment"):
                enrichment = profile.get(key) if isinstance(profile.get(key), dict) else None
                job_id = _int_or_none(enrichment.get("job_id")) if enrichment else None
                if job_id and enrichment is not None:
                    job_ids.add(job_id)
                    queue_targets.append((enrichment, job_id, "enrichment"))

        advance = (
            payload.get("profile_advance_job")
            if isinstance(payload.get("profile_advance_job"), dict)
            else None
        )
        advance_job_id = _int_or_none(advance.get("job_id")) if advance else None
        if advance_job_id and advance is not None:
            job_ids.add(advance_job_id)
            queue_targets.append((advance, advance_job_id, "profile"))

        # Older URL-profile/session-advance items sometimes retained only the
        # top-level job_id.  Reconstruct a read-only target for that specific
        # profile shape; never reinterpret a URL-video job as profile work.
        direct_job_id = _int_or_none(item.get("job_id"))
        profile_shaped = bool(
            str(item.get("item_type") or "").strip().lower() == "url_profile"
            or str(item.get("stage") or "").strip().lower() == "profile"
            or advance is not None
        )
        if direct_job_id and profile_shaped:
            if advance is None:
                advance = {"job_id": direct_job_id}
                payload["profile_advance_job"] = advance
            job_ids.add(direct_job_id)
            queue_targets.append((advance, direct_job_id, "profile"))
    if not job_ids:
        return
    placeholders = ",".join(["?"] * len(job_ids))
    try:
        rows = conn.execute(
            f"SELECT id, status FROM apify_jobs WHERE id IN ({placeholders})",
            tuple(sorted(job_ids)),
        ).fetchall()
    except Exception:
        logger.warning("search_sessions.enrichment_job_lookup_failed", exc_info=True)
        return
    job_statuses = {int(dict(row)["id"]): _text(dict(row).get("status")).lower() for row in rows}
    seen_targets: set[tuple[int, int, str]] = set()
    for target, job_id, target_kind in queue_targets:
        target_key = (id(target), job_id, target_kind)
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)
        queue_status = job_statuses.get(job_id)
        if not queue_status:
            continue
        target["queue_status"] = queue_status
        if (
            target_kind == "enrichment"
            and _text(target.get("status")).lower() in PENDING_ENRICHMENT_STATUSES
        ):
            if queue_status == "done":
                target["status"] = "empty"
            elif queue_status in {"failed", "blocked", "cancelled"}:
                target["status"] = "partial"
