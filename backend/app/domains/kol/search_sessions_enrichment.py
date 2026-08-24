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
    job_ids: set[int] = set()
    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        profile_execute = payload.get("profile_execute") if isinstance(payload.get("profile_execute"), dict) else {}
        for key in ("contact_enrichment", "audience_enrichment"):
            enrichment = profile_execute.get(key) if isinstance(profile_execute.get(key), dict) else {}
            job_id = _int_or_none(enrichment.get("job_id"))
            if job_id:
                job_ids.add(job_id)
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
    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        profile_execute = payload.get("profile_execute") if isinstance(payload.get("profile_execute"), dict) else {}
        for key in ("contact_enrichment", "audience_enrichment"):
            enrichment = profile_execute.get(key) if isinstance(profile_execute.get(key), dict) else None
            if enrichment is None:
                continue
            queue_status = job_statuses.get(_int_or_none(enrichment.get("job_id")) or 0)
            if not queue_status:
                continue
            enrichment["queue_status"] = queue_status
            if _text(enrichment.get("status")).lower() in PENDING_ENRICHMENT_STATUSES:
                if queue_status == "done":
                    enrichment["status"] = "empty"
                elif queue_status in {"failed", "blocked", "cancelled"}:
                    enrichment["status"] = "partial"
