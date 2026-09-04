"""Side-effect helpers for the KOL profile deep-crawl worker handler."""
from __future__ import annotations

from typing import Any, Callable


def resolve_job_staff(
    conn: Any,
    payload: dict[str, Any],
    *,
    int_or_none: Callable[[Any], int | None],
    row_factory: Any,
) -> dict[str, Any]:
    """Resolve users.id to staff.id while retaining the original actor id.

    LLM-call attribution stores a staff FK, while worker payloads carry users.id.
    A non-staff actor must resolve to null rather than leaking users.id into that FK.
    """
    user_id = int_or_none(payload.get("triggered_by_user_id"))
    resolved = int_or_none(payload.get("staff_id"))
    if resolved is None and user_id is not None:
        with conn.cursor(row_factory=row_factory) as cur:
            cur.execute(
                "SELECT id FROM staff WHERE user_id=%s ORDER BY id LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone()
        if row:
            resolved = int_or_none(row.get("id"))
    if resolved is None:
        return {"id": None, "staff_id": None, "user_id": None}
    return {"id": resolved, "staff_id": resolved, "user_id": user_id}


def terminalize_write_fence_error(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    exc: Any,
    *,
    terminal_codes: set[str] | frozenset[str],
    db_connection_sync_scope: Callable[..., Any],
    json_dump: Callable[[Any], str],
    logger: Any,
) -> bool:
    has_durable_target_fence = (
        isinstance(payload.get("target_write_fence"), dict)
        or isinstance(payload.get("maintenance_target_fence"), dict)
        or payload.get("maintenance_refresh") is True
    )
    if not (has_durable_target_fence and exc.code in terminal_codes):
        return False
    provider_calls_performed = getattr(exc, "provider_calls_performed", None)
    payload["provider_calls_performed"] = (
        provider_calls_performed
        if isinstance(provider_calls_performed, bool)
        else None
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='blocked', last_error=%s, payload=%s::jsonb, updated_at=NOW()
                WHERE id=%s
                """,
                (str(exc.code)[:300], json_dump(payload), int(job["id"])),
            )
    try:
        with db_connection_sync_scope():
            from app.domains.kol import content_monitoring

            content_monitoring.record_monitor_job_terminal(
                payload,
                job_id=int(job["id"]),
                status="blocked",
            )
    except Exception:
        logger.warning("content monitor blocked receipt update failed job_id=%s", job.get("id"))
    return True


def crawl_outcome(result: Any) -> tuple[bool, str]:
    value = result or {}
    status = str(value.get("status") or "")
    ok = status in ("", "ok", "ready", "done", "executed") or bool(
        value.get("execution")
    )
    profile_flow = value.get("profile_flow") or {}
    flow_status = str(profile_flow.get("status") or "")
    url_type = str(value.get("url_type") or "")
    if flow_status in {"crawl_failed", "profile_crawl_failed", "provider_error", "timeout"}:
        crawl_status = str(profile_flow.get("crawl_status") or flow_status)
        raise RuntimeError(f"profile_provider_unavailable:{crawl_status}")
    if ok and flow_status in ("unsupported", "needs_human_choice") and not value.get("video_flow"):
        return False, f"url_{url_type or 'unknown'}_{flow_status}"
    return ok, status


def persist_crawl_outcome(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    *,
    ok: bool,
    status: str,
    json_dump: Callable[[Any], str],
) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (status or "deep_crawl_not_executed")[:300],
                    json_dump(payload),
                    int(job["id"]),
                ),
            )


def record_monitor_terminal(
    job: dict[str, Any],
    payload: dict[str, Any],
    *,
    ok: bool,
    db_connection_sync_scope: Callable[..., Any],
    logger: Any,
) -> None:
    try:
        with db_connection_sync_scope():
            from app.domains.kol import content_monitoring

            content_monitoring.record_monitor_job_terminal(
                payload,
                job_id=int(job["id"]),
                status="done" if ok else "blocked",
            )
    except Exception:
        logger.warning("content monitor terminal receipt update failed job_id=%s", job.get("id"))


def crawl_kol_pool_id(
    payload: dict[str, Any],
    result: Any,
    *,
    int_or_none: Callable[[Any], int | None],
) -> int | None:
    value = result or {}
    return int_or_none(
        payload.get("kol_pool_id")
        or (value.get("profile_flow") or {}).get("kol_pool_id")
        or value.get("matched_kol_pool_id")
    )


def run_success_followups(
    kol_pool_id: int,
    payload: dict[str, Any],
    staff: dict[str, Any],
    *,
    db_connection_sync_scope: Callable[..., Any],
    logger: Any,
) -> None:
    try:
        from app.domains.sync import refresh_tier

        refresh_tier.mark_kol_refreshed(int(kol_pool_id), status="ready")
    except Exception:
        logger.warning(
            "profile refresh freshness ledger failed kol_pool_id=%s",
            kol_pool_id,
            exc_info=True,
        )
    if payload.get("suppress_contact_followup") is True:
        return
    try:
        from app.domains.kol.contact_acquisition_queue import (
            enqueue_contact_acquisition,
            reconcile_contact_acquisition,
        )

        with db_connection_sync_scope():
            enqueue_contact_acquisition(
                int(kol_pool_id),
                trigger_source="deep_crawl",
            )
        try:
            organization_id = int((staff or {}).get("organization_id") or 0)
        except (TypeError, ValueError):
            organization_id = 0
        if organization_id > 0:
            with db_connection_sync_scope():
                reconcile_contact_acquisition(
                    int(kol_pool_id),
                    brand_scope=f"organization:{organization_id}",
                )
    except Exception as exc:
        logger.warning(
            "provider-free contact L0 after deep_crawl failed (non-fatal) | kol_pool_id=%s error_type=%s",
            kol_pool_id,
            type(exc).__name__,
        )
