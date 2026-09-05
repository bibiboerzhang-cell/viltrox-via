"""Bounded daily refresh for the KOL inventory used by Smart Search.

The interactive search path must not depend on an operator typing an exact SKU
or manually refreshing every profile.  This module keeps the *evidence supply*
alive: it selects profiles with no/old video evidence (then stale profile
metadata) and queues the existing one-post profile refresh lane with every LLM
and derived follow-up suppressed.

The scheduler never calls a provider directly, never invokes an LLM and never
changes ranking scores.  Provider work remains inside the existing durable
worker and its budget/authorization fences.  A source-scoped local-calendar
daily cap plus a seven-day attempt cooldown make retries predictable even when
a profile is private, unsupported, or never yields video evidence.
"""
from __future__ import annotations

import secrets
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.kol import url_deep_crawl
from app.domains.kol import search_inventory_scan_state as scan_state
from app.domains.kol.url_deep_crawl_queue import DEEP_CRAWL_JOB_TYPE


logger = get_logger(__name__)

TASK_KEY = "kol_profile_incremental_refresh"
JOB_TYPE = DEEP_CRAWL_JOB_TYPE
REFRESH_SOURCE = "kol_search_inventory_daily"
DEFAULT_DAILY_LIMIT = 5
# Initial rollout safety invariant. This is a hard cross-run daily job cap,
# not merely the default value accepted by ``enqueue_daily_refresh``. Raising
# it requires a reviewed code + database migration change.
MAX_DAILY_LIMIT = 5
DAILY_CAP_UNIT = "new_maintenance_jobs_not_provider_calls"
DAILY_CAP_NOTICE = (
    "5 maintenance jobs are not 5 provider calls; "
    "one job may perform multiple provider calls"
)
DAILY_SLOT_TABLE = "vkpi_kol_search_inventory_daily_slots"
PROFILE_STALE_DAYS = 7
VIDEO_STALE_DAYS = 45
ATTEMPT_COOLDOWN_HOURS = 7 * 24
from app.domains.kol.search_platform_policy import STRICT_DISCOVERY_PLATFORMS
SUPPORTED_PLATFORMS = STRICT_DISCOVERY_PLATFORMS
DAILY_BUDGET_TIMEZONE = "America/New_York"
SCAN_PAGE_SIZE = 500
MAX_SCAN_ROWS = 2_000


class RefreshSelectionUnavailable(RuntimeError):
    """The inventory could not be evaluated; this is not an empty result."""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _row(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    # Supported DB adapters expose mapping-compatible rows.  If that contract
    # changes, fail visibly so the scheduler run is recorded as failed instead
    # of silently pretending that the inventory contained no usable rows.
    return dict(value)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text_value = str(value or "").strip()
        if not text_value:
            return None
        try:
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _calendar_day_bounds(as_of: datetime | None = None) -> tuple[datetime, datetime, str]:
    zone = ZoneInfo(DAILY_BUDGET_TIMEZONE)
    current = as_of or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_day = current.astimezone(zone).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=zone)
    end_local = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=zone)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), local_day.isoformat()


def _reserve_daily_job_slots(
    conn: Any,
    *,
    batch_date: str,
    requested: int,
    actual_jobs: int,
) -> dict[str, Any]:
    """Atomically reserve unique daily job slots across scheduler/manual runs.

    The table primary key is ``(batch_date, slot_no)``. Concurrent callers may
    wait on the same first free slot, but they cannot jointly create more than
    ``MAX_DAILY_LIMIT`` rows. Reservations are committed before any provider
    job is inserted, so a process crash fails closed (temporary underfill) and
    can never reopen spend capacity.
    """

    safe_requested = max(0, min(_int(requested), MAX_DAILY_LIMIT))
    existing_rows = conn.execute(
        "SELECT slot_no FROM vkpi_kol_search_inventory_daily_slots "
        "WHERE batch_date=? ORDER BY slot_no",
        (batch_date,),
    ).fetchall()
    occupied = {
        _int(_row(item).get("slot_no"))
        for item in existing_rows
        if _int(_row(item).get("slot_no")) > 0
    }
    # Deploying the ledger during an already-active day must account for source
    # jobs created before the table existed. Fill anonymous legacy slots first.
    legacy_target = min(MAX_DAILY_LIMIT, max(0, _int(actual_jobs)))
    for slot_no in range(1, MAX_DAILY_LIMIT + 1):
        if len(occupied) >= legacy_target:
            break
        if slot_no in occupied:
            continue
        inserted = conn.execute(
            """
            INSERT INTO vkpi_kol_search_inventory_daily_slots
                (batch_date, slot_no, reservation_token, job_id, updated_at)
            VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT (batch_date, slot_no) DO NOTHING
            RETURNING slot_no
            """,
            (batch_date, slot_no, f"legacy:{batch_date}"),
        ).fetchone()
        if inserted:
            occupied.add(slot_no)
        else:
            occupied.add(slot_no)

    token = f"refresh:{batch_date}:{secrets.token_hex(12)}"
    reserved: list[int] = []
    used_before = len(occupied)
    for slot_no in range(1, MAX_DAILY_LIMIT + 1):
        if len(reserved) >= safe_requested:
            break
        if slot_no in occupied:
            continue
        inserted = conn.execute(
            """
            INSERT INTO vkpi_kol_search_inventory_daily_slots
                (batch_date, slot_no, reservation_token, job_id, updated_at)
            VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT (batch_date, slot_no) DO NOTHING
            RETURNING slot_no
            """,
            (batch_date, slot_no, token),
        ).fetchone()
        if inserted:
            inserted_slot = _int(_row(inserted).get("slot_no"), slot_no)
            occupied.add(inserted_slot)
            reserved.append(inserted_slot)
        else:
            occupied.add(slot_no)
    conn.commit()
    return {
        "reservation_token": token,
        "reserved_slots": reserved,
        "used_before": used_before,
        "used_after_reservation": len(occupied),
        "hard_limit": MAX_DAILY_LIMIT,
    }


def _bind_daily_job_slot(
    conn: Any,
    *,
    batch_date: str,
    reservation_token: str,
    slot_no: int,
    job_id: int,
) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_search_inventory_daily_slots
        SET job_id=?, updated_at=CURRENT_TIMESTAMP
        WHERE batch_date=? AND slot_no=? AND reservation_token=?
        """,
        (int(job_id), batch_date, int(slot_no), reservation_token),
    )
    conn.commit()


def _release_daily_job_slots(
    conn: Any,
    *,
    batch_date: str,
    reservation_token: str,
    slot_numbers: list[int],
) -> int:
    released = 0
    for slot_no in slot_numbers:
        cursor = conn.execute(
            """
            DELETE FROM vkpi_kol_search_inventory_daily_slots
            WHERE batch_date=? AND slot_no=? AND reservation_token=? AND job_id IS NULL
            """,
            (batch_date, int(slot_no), reservation_token),
        )
        released += max(0, _int(getattr(cursor, "rowcount", 0)))
    conn.commit()
    return released


def select_refresh_candidates(
    limit: int = DEFAULT_DAILY_LIMIT,
    *,
    as_of: datetime | None = None,
    diagnostics: dict[str, Any] | None = None,
    start_offset: int = 0,
    progress: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the stalest searchable profiles, never more than ``limit``.

    Profiles without video evidence come first.  We deliberately do not infer
    inactivity from missing evidence: missing/old evidence means *our crawler*
    needs work, not that the creator should disappear from search.
    """

    safe_limit = max(1, min(_int(limit, DEFAULT_DAILY_LIMIT), MAX_DAILY_LIMIT))
    required_tables = {
        "vkpi_kol_pool",
        "vkpi_kol_url_deep_crawl_runs",
        "apify_jobs",
    }
    missing_tables = sorted(name for name in required_tables if not table_exists(name))
    if missing_tables:
        if diagnostics is not None:
            diagnostics.update({"status": "unavailable", "missing_tables": missing_tables})
        raise RefreshSelectionUnavailable(
            "required_tables_missing:" + ",".join(missing_tables)
        )
    now = _as_datetime(as_of) or datetime.now(timezone.utc)
    profile_cutoff = now - timedelta(days=PROFILE_STALE_DAYS)
    video_cutoff = now - timedelta(days=VIDEO_STALE_DAYS)
    attempt_cutoff = now - timedelta(hours=ATTEMPT_COOLDOWN_HOURS)
    conn = get_conn()

    if table_exists("vkpi_kol_video_evidence"):
        sql = f"""
            WITH latest_video AS (
                SELECT kol_pool_id,
                       MAX(
                           CASE WHEN is_active IS TRUE THEN
                               COALESCE(
                                   published_at_norm,
                                   CAST(posted_at AS TIMESTAMPTZ),
                                   publish_date
                               )
                           ELSE NULL END
                       ) AS latest_video_at
                FROM vkpi_kol_video_evidence e
                GROUP BY kol_pool_id
            ), latest_ready_refresh AS (
                SELECT kol_pool_id, MAX(created_at) AS last_ready_refresh_at
                FROM vkpi_kol_url_deep_crawl_runs
                WHERE status='ready' AND dry_run IS FALSE
                GROUP BY kol_pool_id
            ), latest_inventory_attempt AS (
                SELECT payload ->> 'kol_pool_id' AS kol_pool_id_text,
                       MAX(created_at) AS last_refresh_attempt_at
                FROM apify_jobs
                WHERE job_type=? AND payload ->> 'source'=?
                  AND NOT (
                        status='blocked'
                        AND COALESCE(last_error, '') LIKE ?
                      )
                GROUP BY payload ->> 'kol_pool_id'
            )
            SELECT
                p.id,
                p.handle,
                p.platform,
                p.profile_url,
                p.display_name,
                p.last_seen_at,
                v.latest_video_at,
                r.last_ready_refresh_at,
                a.last_refresh_attempt_at
            FROM vkpi_kol_pool p
            LEFT JOIN latest_video v ON v.kol_pool_id=p.id
            LEFT JOIN latest_ready_refresh r ON r.kol_pool_id=p.id
            LEFT JOIN latest_inventory_attempt a ON a.kol_pool_id_text=CAST(p.id AS TEXT)
            WHERE COALESCE(p.profile_url, '') <> ''
              AND p.duplicate_of_id IS NULL
              AND LOWER(COALESCE(p.platform, '')) IN ({', '.join('?' for _ in SUPPORTED_PLATFORMS)})
              AND (
                    v.latest_video_at IS NULL
                    OR v.latest_video_at < ?
                    OR r.last_ready_refresh_at IS NULL
                    OR r.last_ready_refresh_at < ?
                  )
              AND (
                    a.last_refresh_attempt_at IS NULL
                    OR a.last_refresh_attempt_at < ?
                  )
            ORDER BY
                CASE WHEN a.last_refresh_attempt_at IS NULL THEN 0 ELSE 1 END,
                CASE WHEN v.latest_video_at IS NULL THEN 0 ELSE 1 END,
                r.last_ready_refresh_at ASC NULLS FIRST,
                v.latest_video_at ASC NULLS FIRST,
                a.last_refresh_attempt_at ASC NULLS FIRST,
                p.id ASC
            LIMIT ? OFFSET ?
        """
        base_params: tuple[Any, ...] = (
            JOB_TYPE,
            REFRESH_SOURCE,
            "maintenance_refresh_%",
            *SUPPORTED_PLATFORMS,
            video_cutoff,
            profile_cutoff,
            attempt_cutoff,
        )
    else:
        sql = f"""
            WITH latest_ready_refresh AS (
                SELECT kol_pool_id, MAX(created_at) AS last_ready_refresh_at
                FROM vkpi_kol_url_deep_crawl_runs
                WHERE status='ready' AND dry_run IS FALSE
                GROUP BY kol_pool_id
            ), latest_inventory_attempt AS (
                SELECT payload ->> 'kol_pool_id' AS kol_pool_id_text,
                       MAX(created_at) AS last_refresh_attempt_at
                FROM apify_jobs
                WHERE job_type=? AND payload ->> 'source'=?
                  AND NOT (
                        status='blocked'
                        AND COALESCE(last_error, '') LIKE ?
                      )
                GROUP BY payload ->> 'kol_pool_id'
            )
            SELECT p.id, p.handle, p.platform, p.profile_url, p.display_name,
                   p.last_seen_at, NULL AS latest_video_at,
                   r.last_ready_refresh_at, a.last_refresh_attempt_at
            FROM vkpi_kol_pool p
            LEFT JOIN latest_ready_refresh r ON r.kol_pool_id=p.id
            LEFT JOIN latest_inventory_attempt a ON a.kol_pool_id_text=CAST(p.id AS TEXT)
            WHERE COALESCE(p.profile_url, '') <> ''
              AND p.duplicate_of_id IS NULL
              AND LOWER(COALESCE(p.platform, '')) IN ({', '.join('?' for _ in SUPPORTED_PLATFORMS)})
              AND (r.last_ready_refresh_at IS NULL OR r.last_ready_refresh_at < ?)
              AND (a.last_refresh_attempt_at IS NULL OR a.last_refresh_attempt_at < ?)
            ORDER BY CASE WHEN a.last_refresh_attempt_at IS NULL THEN 0 ELSE 1 END,
                     r.last_ready_refresh_at ASC NULLS FIRST,
                     a.last_refresh_attempt_at ASC NULLS FIRST,
                     p.id ASC
            LIMIT ? OFFSET ?
        """
        base_params = (
            JOB_TYPE,
            REFRESH_SOURCE,
            "maintenance_refresh_%",
            *SUPPORTED_PLATFORMS,
            profile_cutoff,
            attempt_cutoff,
        )

    output: list[dict[str, Any]] = []
    scanned_rows = 0
    invalid_profile_urls = 0
    scan_exhausted = False
    query_offset = scan_state.bounded_offset(start_offset)
    wrapped = query_offset == 0
    next_offset = query_offset
    while len(output) < safe_limit and scanned_rows < MAX_SCAN_ROWS:
        page_limit = min(SCAN_PAGE_SIZE, MAX_SCAN_ROWS - scanned_rows)
        try:
            rows = conn.execute(
                sql,
                (*base_params, page_limit, query_offset),
            ).fetchall()
        except Exception as exc:
            if diagnostics is not None:
                diagnostics.update(
                    {
                        "status": "unavailable",
                        "scanned_rows": scanned_rows,
                        "invalid_profile_urls": invalid_profile_urls,
                        "error_code": type(exc).__name__.lower()[:80],
                    }
                )
            logger.warning("vkpi.kol.search_inventory_refresh.select_failed", exc_info=True)
            raise RefreshSelectionUnavailable("candidate_query_failed") from exc

        page_rows = list(rows)
        scanned_rows += len(page_rows)
        query_offset += len(page_rows)
        for raw in page_rows:
            item = _row(raw)
            kol_pool_id = _int(item.get("id"))
            if kol_pool_id <= 0:
                continue
            profile_url = str(item.get("profile_url") or "").strip()
            try:
                classified = url_deep_crawl.classify_url(profile_url)
            except ValueError:
                # Malformed URL syntax (for example an unmatched IPv6 bracket)
                # is a bad row, not a reason to fail every profile in the batch.
                invalid_profile_urls += 1
                logger.warning(
                    "vkpi.kol.search_inventory_refresh.malformed_profile_url "
                    "kol_pool_id=%s",
                    kol_pool_id,
                )
                continue
            if (
                classified.url_type != "profile"
                or classified.platform not in SUPPORTED_PLATFORMS
                or classified.platform != str(item.get("platform") or "").strip().lower()
            ):
                invalid_profile_urls += 1
                logger.warning(
                    "vkpi.kol.search_inventory_refresh.invalid_profile_url "
                    "kol_pool_id=%s url_type=%s platform=%s",
                    kol_pool_id,
                    classified.url_type,
                    classified.platform,
                )
                continue
            latest_video_at = item.get("latest_video_at")
            last_ready_refresh_at = item.get("last_ready_refresh_at")
            reasons: list[str] = []
            if _as_datetime(last_ready_refresh_at) is None:
                reasons.append("never_profile_refreshed")
            elif _as_datetime(last_ready_refresh_at) < profile_cutoff:
                reasons.append("profile_stale")
            if _as_datetime(latest_video_at) is None:
                reasons.append("no_video_evidence")
            elif _as_datetime(latest_video_at) < video_cutoff:
                reasons.append("video_evidence_stale")
            output.append(
                {
                    "kol_pool_id": kol_pool_id,
                    "handle": item.get("handle"),
                    "platform": item.get("platform"),
                    "profile_url": classified.normalized_url,
                    "display_name": item.get("display_name"),
                    "last_seen_at": _iso(item.get("last_seen_at")),
                    "last_ready_refresh_at": _iso(last_ready_refresh_at),
                    "last_refresh_attempt_at": _iso(item.get("last_refresh_attempt_at")),
                    "latest_video_at": _iso(latest_video_at),
                    "priority_reasons": reasons,
                }
            )
            if len(output) >= safe_limit:
                break
        next_offset = query_offset
        if len(page_rows) < page_limit:
            next_offset = 0
            if not output and not wrapped and scanned_rows < MAX_SCAN_ROWS:
                # The eligible population may shrink between runs; a stale
                # offset must wrap without falsely reporting an empty pool.
                query_offset = 0
                wrapped = True
                continue
            break
    if scanned_rows >= MAX_SCAN_ROWS and len(output) < safe_limit:
        scan_exhausted = True
        logger.warning(
            "vkpi.kol.search_inventory_refresh.scan_exhausted scanned_rows=%s "
            "invalid_profile_urls=%s selected=%s requested=%s",
            scanned_rows,
            invalid_profile_urls,
            len(output),
            safe_limit,
        )
    if diagnostics is not None:
        diagnostics.update(
            {
                "status": "scan_exhausted" if scan_exhausted else "ok",
                "scanned_rows": scanned_rows,
                "invalid_profile_urls": invalid_profile_urls,
                "scan_exhausted": scan_exhausted,
                "scan_limit": MAX_SCAN_ROWS,
            }
        )
    if progress is not None:
        progress.update({"next_offset": next_offset, "wrapped": wrapped})
    return output


def enqueue_daily_refresh(
    limit: int = DEFAULT_DAILY_LIMIT,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Queue a bounded, idempotent daily evidence refresh batch.

    ``limit`` is only a caller-requested run size. It can never raise the
    America/New_York daily ceiling above ``MAX_DAILY_LIMIT`` maintenance jobs.
    A maintenance job may make multiple provider calls; this cap deliberately
    does not describe or replace provider-specific budgets.
    """

    safe_limit = max(1, min(_int(limit, DEFAULT_DAILY_LIMIT), MAX_DAILY_LIMIT))
    missing_runtime_tables = [
        name
        for name in ("apify_jobs", DAILY_SLOT_TABLE)
        if not table_exists(name)
    ]
    if missing_runtime_tables:
        return {
            "status": (
                "queue_unavailable"
                if "apify_jobs" in missing_runtime_tables
                else "budget_ledger_unavailable"
            ),
            "missing_tables": missing_runtime_tables,
            "task_key": TASK_KEY,
            "job_type": JOB_TYPE,
            "candidate_count": 0,
            "daily_limit": MAX_DAILY_LIMIT,
            "run_limit": safe_limit,
            "daily_cap_unit": DAILY_CAP_UNIT,
            "daily_cap_notice": DAILY_CAP_NOTICE,
            "daily_used": None,
            "queued": 0,
            "already_queued": 0,
            "failed": 0,
            "provider_calls_performed": False,
            "llm_calls_performed": False,
            "viltrox_fit_score_untouched": True,
        }
    conn = get_conn()
    day_start, day_end, batch_date = _calendar_day_bounds(as_of)
    used_row = conn.execute(
        """
        SELECT COUNT(*) AS used
        FROM apify_jobs
        WHERE job_type=?
          AND payload ->> 'source'=?
          AND created_at >= ?
          AND created_at < ?
        """,
        (JOB_TYPE, REFRESH_SOURCE, day_start, day_end),
    ).fetchone()
    daily_used = _int(_row(used_row).get("used")) if used_row else 0
    remaining = max(0, min(safe_limit, MAX_DAILY_LIMIT - daily_used))
    selection_diagnostics: dict[str, Any] = {}
    progress: dict[str, Any] = {}
    cursor_fields: dict[str, Any] = {}
    start_offset = 0
    cursor_available = bool(remaining and table_exists("persistent_cache"))
    selection_time = _as_datetime(as_of) or datetime.now(timezone.utc)
    if cursor_available:
        start_offset, cursor_status = scan_state.load_offset(conn, as_of=selection_time)
        cursor_fields["selection_cursor_load_status"] = cursor_status
    candidates = (
        select_refresh_candidates(
            remaining,
            as_of=as_of,
            diagnostics=selection_diagnostics,
            start_offset=start_offset,
            progress=progress,
        )
        if remaining
        else []
    )
    scan_exhausted = selection_diagnostics.get("scan_exhausted") is True
    if cursor_available:
        # Continue only an unusable prefix. Successful selection restarts the
        # oldest-first policy; the durable attempt cooldown advances that pool.
        next_offset = _int(progress.get("next_offset")) if not candidates else 0
        cursor_fields.update(
            {
                "selection_start_offset": start_offset,
                "selection_next_offset": next_offset,
                "selection_cursor_status": scan_state.save_offset(
                    conn, next_offset, as_of=selection_time
                ),
            }
        )
    selected_candidate_count = len(candidates)
    reservation = {
        "reservation_token": "",
        "reserved_slots": [],
        "used_before": min(MAX_DAILY_LIMIT, daily_used),
        "used_after_reservation": min(MAX_DAILY_LIMIT, daily_used),
        "hard_limit": MAX_DAILY_LIMIT,
    }
    if candidates:
        reservation = _reserve_daily_job_slots(
            conn,
            batch_date=batch_date,
            requested=len(candidates),
            actual_jobs=daily_used,
        )
        granted_slots = list(reservation.get("reserved_slots") or [])
        candidates = candidates[: len(granted_slots)]
    else:
        granted_slots = []
    base: dict[str, Any] = {
        "status": (
            "budget_exhausted"
            if not remaining or (selected_candidate_count and not granted_slots)
            else "selection_exhausted"
            if not candidates and scan_exhausted
            else "empty"
            if not candidates
            else "ok"
        ),
        "task_key": TASK_KEY,
        "job_type": JOB_TYPE,
        "candidate_count": len(candidates),
        "selected_candidate_count": selected_candidate_count,
        "daily_limit": MAX_DAILY_LIMIT,
        "run_limit": safe_limit,
        "default_canary_limit": DEFAULT_DAILY_LIMIT,
        "daily_cap_unit": DAILY_CAP_UNIT,
        "daily_cap_notice": DAILY_CAP_NOTICE,
        "daily_used": daily_used,
        "daily_reserved_before_run": _int(reservation.get("used_before")),
        "remaining_before_run": max(
            0,
            MAX_DAILY_LIMIT - _int(reservation.get("used_before")),
        ),
        "reservation_slots_granted": len(granted_slots),
        "batch_date": batch_date,
        "budget_timezone": DAILY_BUDGET_TIMEZONE,
        "budget_window_start": day_start.isoformat(),
        "budget_window_end": day_end.isoformat(),
        "selection_status": selection_diagnostics.get("status") or "not_run",
        "selection_scanned_rows": _int(selection_diagnostics.get("scanned_rows")),
        "selection_invalid_profile_urls": _int(
            selection_diagnostics.get("invalid_profile_urls")
        ),
        "selection_scan_exhausted": scan_exhausted,
        "refresh_source": REFRESH_SOURCE,
        "refresh_mode": "account_deep_one_post_no_followups",
        "provider_calls_performed": False,
        "llm_calls_performed": False,
        "viltrox_fit_score_untouched": True,
        **cursor_fields,
    }
    if not candidates:
        return {
            **base,
            "queued": 0,
            "already_queued": 0,
            "failed": 0,
            "reservation_slots_released": 0,
            "reservation_slots_held": 0,
        }
    queued = 0
    already_queued = 0
    failed = 0
    releasable_slots: list[int] = []
    reservation_token = str(reservation.get("reservation_token") or "")
    for candidate, slot_no in zip(candidates, granted_slots):
        kol_pool_id = _int(candidate.get("kol_pool_id"))
        if kol_pool_id <= 0:
            releasable_slots.append(_int(slot_no))
            continue
        try:
            result = url_deep_crawl.enqueue_profile_deep_crawl_job(
                str(candidate.get("profile_url") or ""),
                kol_pool_id=kol_pool_id,
                max_posts=1,
                mode="account_deep",
                representative_video_limit=1,
                staff=None,
                source=REFRESH_SOURCE,
                queue_lane="batch",
                suppress_final_v1=True,
                suppress_contact_followup=True,
                suppress_profile_followups=True,
                maintenance_refresh=True,
                maintenance_batch_date=batch_date,
            )
            status = str(result.get("status") or "")
            if status == "queued":
                queued += 1
                job_id = _int(result.get("job_id"))
                if job_id > 0:
                    try:
                        _bind_daily_job_slot(
                            conn,
                            batch_date=batch_date,
                            reservation_token=reservation_token,
                            slot_no=_int(slot_no),
                            job_id=job_id,
                        )
                    except Exception:
                        # Keep the committed reservation without a job binding.
                        # That may underfill this day but cannot reopen spend.
                        logger.warning(
                            "vkpi.kol.search_inventory_refresh.slot_bind_failed "
                            "kol_pool_id=%s slot_no=%s",
                            kol_pool_id,
                            slot_no,
                            exc_info=True,
                        )
            elif status == "already_queued":
                already_queued += 1
                releasable_slots.append(_int(slot_no))
            else:
                failed += 1
                logger.warning(
                    "vkpi.kol.search_inventory_refresh.unexpected_enqueue_status "
                    "kol_pool_id=%s status=%s",
                    kol_pool_id,
                    status,
                )
        except Exception:
            failed += 1
            try:
                conn.rollback()
            except Exception:
                logger.warning(
                    "vkpi.kol.search_inventory_refresh.rollback_failed kol_pool_id=%s",
                    kol_pool_id,
                    exc_info=True,
                )
            logger.warning(
                "vkpi.kol.search_inventory_refresh.enqueue_failed kol_pool_id=%s",
                kol_pool_id,
                exc_info=True,
            )
    released_slots = 0
    if releasable_slots:
        try:
            released_slots = _release_daily_job_slots(
                conn,
                batch_date=batch_date,
                reservation_token=reservation_token,
                slot_numbers=releasable_slots,
            )
        except Exception:
            # Fail closed: leaked reservations expire naturally with the local
            # calendar day and never permit more paid work.
            logger.warning(
                "vkpi.kol.search_inventory_refresh.slot_release_failed "
                "batch_date=%s slots=%s",
                batch_date,
                releasable_slots,
                exc_info=True,
            )
    status = (
        "partial"
        if scan_exhausted or (failed and (queued or already_queued))
        else "failed"
        if failed
        else "ok"
    )
    return {
        **base,
        "status": status,
        "queued": queued,
        "already_queued": already_queued,
        "failed": failed,
        "reservation_slots_released": released_slots,
        "reservation_slots_held": len(granted_slots) - released_slots,
    }


__all__ = [
    "DEFAULT_DAILY_LIMIT",
    "DAILY_CAP_NOTICE",
    "DAILY_CAP_UNIT",
    "JOB_TYPE",
    "MAX_DAILY_LIMIT",
    "MAX_SCAN_ROWS",
    "PROFILE_STALE_DAYS",
    "ATTEMPT_COOLDOWN_HOURS",
    "REFRESH_SOURCE",
    "SUPPORTED_PLATFORMS",
    "TASK_KEY",
    "VIDEO_STALE_DAYS",
    "RefreshSelectionUnavailable",
    "_calendar_day_bounds",
    "enqueue_daily_refresh",
    "select_refresh_candidates",
]
