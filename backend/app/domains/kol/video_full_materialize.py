"""P0-1: trigger a *full* video-metadata materialization for one KOL.

Goal: make "see all of a KOL's videos" real. Instead of only scanning the
evidence we already have, this enqueues the existing
``kol_profile_deep_crawl`` job (reusing the worker's account-deep channel→videos
crawl path) so the crawler back-fills ``vkpi_kol_video_evidence`` with the
KOL's full video metadata. Metadata only — cheap, never burns LLM, never
writes viltrox_fit_score / rule_v0.

Boundary (honest, see recon):
  * The crawler kernel can page a channel to thousands, but the existing
    ``kol_profile_deep_crawl`` history-materialization path is clamped to
    120 evidence rows / run (``_profile_history_video_limit`` = min(120, ...)).
  * ``enqueue_profile_deep_crawl_job`` additionally clamps ``max_posts`` to 12
    (lazy-backfill scope). We deliberately *bypass that 12 clamp* by writing
    the queued job row ourselves with ``max_posts``/``history_video_limit`` set
    up to the honest 120 ceiling, keeping the same payload shape, the same URL
    idempotency, and the same worker handler. True unbounded full-scan would
    need ``history_video_limit`` raised inside the existing channel, which is
    out of this track's scope.

The only DB write is one ``INSERT INTO apify_jobs``. That write is wrapped in
the same viltrox_fit_score snapshot/rollback guard used by
``video_analysis_enqueue`` (read-only compare; rolls back + raises if the score
moved), plus CHECK-style payload assertions before commit.
"""
from __future__ import annotations

import json as _json
from typing import Any
from urllib.parse import urlparse

from app.db.connection import get_conn
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job
from app.domains.kol import video_evidence_sources
from app.domains.kol.url_deep_crawl import DEEP_CRAWL_JOB_TYPE

# Honest history-materialization ceiling of the existing kol_profile_deep_crawl
# channel (``_profile_history_video_limit`` = min(120, ...)). Requests above
# this are clamped and annotated in the returned note.
HISTORY_MATERIALIZE_CEILING = 120


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _triggered_user_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("user_id", "id", "staff_id"):
        parsed = _int_or_none(staff.get(key))
        if parsed:
            return parsed
    return None


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    return _int_or_none(staff.get("id")) or _int_or_none(staff.get("staff_id"))


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _fit_snapshot(conn: Any, kol_pool_id: int) -> Any:
    # Read-only audit snapshot of viltrox_fit_score. Never assigns it.
    row = conn.execute(
        "SELECT viltrox_fit_score FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row).get("viltrox_fit_score") if row else None


def enqueue_full_materialize(
    kol_pool_id: int,
    *,
    target_n: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue a full video-metadata materialization for one KOL.

    Reuses the existing ``kol_profile_deep_crawl`` channel (worker already
    wired) by INSERTing one queued ``apify_jobs`` row whose payload mirrors
    ``enqueue_profile_deep_crawl_job`` but lifts ``max_posts`` past the 12
    lazy-backfill clamp up to the honest 120 history ceiling. Never burns LLM,
    never writes viltrox_fit_score.
    """

    kol_pool_id = int(kol_pool_id)
    conn = get_conn()

    kol = conn.execute(
        """
        SELECT id, profile_url, COALESCE(handle, display_name, '') AS kol_handle
        FROM vkpi_kol_pool
        WHERE id=?
        """,
        (kol_pool_id,),
    ).fetchone()
    if not kol:
        raise LookupError(f"kol_pool_id not found: {kol_pool_id}")
    kol = dict(kol)

    profile_url = str(kol.get("profile_url") or "").strip()
    if not profile_url or not _is_http_url(profile_url):
        return {
            "status": "no_profile_url",
            "kol_pool_id": kol_pool_id,
            "profile_url": profile_url or None,
            "budget_gate": "record_only",
            "provider_calls": False,
            "write_db": False,
            "writes": [],
            "note": "KOL has no usable http(s) profile_url; cannot crawl channel videos.",
        }

    # Honest clamp to the existing channel's 120/run history-materialization
    # ceiling. >120 is requested-but-clamped, annotated below.
    requested_raw = _int_or_none(target_n) or HISTORY_MATERIALIZE_CEILING
    safe_target = max(1, min(HISTORY_MATERIALIZE_CEILING, requested_raw))
    clamped = requested_raw > HISTORY_MATERIALIZE_CEILING

    # Budget preflight: record-only telemetry. Metadata crawl never calls an
    # LLM provider, so we must NOT hard-stop here. require_configured=False
    # (avoids the "require_configured=True hard-blocks all" trap, see MEMORY:
    # video-budget-gate). The result is captured for telemetry only.
    budget_telemetry: dict[str, Any]
    try:
        from app.platform import llm_gateway

        preflight = llm_gateway.budget_preflight(
            f"video_full_materialize kol:{kol_pool_id} n:{safe_target}",
            purpose="vkpi_analysis_worker",
            require_configured=False,
        )
        budget_telemetry = {
            "monthly_remaining_cents": preflight.get("monthly_remaining_cents"),
            "provider_gate_reason": preflight.get("provider_gate_reason"),
            "cost_scope": preflight.get("cost_scope"),
        }
    except Exception:
        # Telemetry must never break a metadata-only enqueue.
        budget_telemetry = {"preflight": "unavailable"}

    # Idempotency: same URL already queued/running on this channel → reuse it
    # (mirrors enqueue_profile_deep_crawl_job dedupe).
    active = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND payload->>'url'=? LIMIT 1
        """,
        (DEEP_CRAWL_JOB_TYPE, profile_url),
    ).fetchone()
    if active:
        return {
            "status": "already_queued",
            "kol_pool_id": kol_pool_id,
            "job_id": int(dict(active)["id"]),
            "requested_n": safe_target,
            "budget_gate": "record_only",
            "budget": budget_telemetry,
            "provider_calls": False,
            "write_db": False,
            "writes": [],
            "note": _channel_note(clamped),
        }

    triggered_by_user_id = _triggered_user_id(staff)
    staff_id = _staff_id(staff)

    # Payload mirrors enqueue_profile_deep_crawl_job's fields but lifts the
    # 12-clamp: max_posts / history_video_limit = safe_target (≤120), and
    # explicitly carries the account-deep switches the worker needs to walk
    # the channel and materialize history evidence.
    scrape_source = video_evidence_sources.validate_source_value(
        "scrape_source",
        f"video_full_materialize:n={safe_target}",
    )
    payload: dict[str, Any] = {
        "url": profile_url,
        "kol_pool_id": kol_pool_id,
        "max_posts": safe_target,
        "history_video_limit": safe_target,
        "mode": "account_deep",
        "full_history": True,
        "query_text": profile_url[:96],
        "target_type": "kol_profile",
        "target_id": kol_pool_id,
        "triggered_by_user_id": triggered_by_user_id,
        "staff_id": staff_id,
        "scrape_source": scrape_source,
    }

    # CHECK-style payload guards before any write (cheap fail-fast tripwires).
    _assert_payload(payload, kol_pool_id=kol_pool_id, safe_target=safe_target)

    # Audit guard: snapshot viltrox_fit_score before/after the only write; if
    # it moved, roll back and raise (the INSERT must never perturb scoring).
    before_fit = _fit_snapshot(conn, kol_pool_id)
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=DEEP_CRAWL_JOB_TYPE,
        payload=payload,
        idempotency_key=active_job_idempotency_key(DEEP_CRAWL_JOB_TYPE, profile_url),
    )
    after_fit = _fit_snapshot(conn, kol_pool_id)
    changed_ids = [kol_pool_id] if before_fit != after_fit else []
    if changed_ids:
        conn.rollback()
        raise RuntimeError(f"viltrox_fit_score_changed_ids={changed_ids}; rolled back")
    conn.commit()

    return {
        "status": "queued" if inserted else "already_queued",
        "kol_pool_id": kol_pool_id,
        "job_id": int(job["id"]),
        "requested_n": safe_target,
        "history_video_limit": safe_target,
        "budget_gate": "record_only",
        "budget": budget_telemetry,
        "viltrox_fit_score_changed_ids": changed_ids,
        "provider_calls": False,
        "write_db": inserted,
        "writes": ["apify_jobs"] if inserted else [],
        "note": _channel_note(clamped),
    }


def _channel_note(clamped: bool) -> str:
    base = (
        "Reuses the existing kol_profile_deep_crawl channel; subject to its "
        f"{HISTORY_MATERIALIZE_CEILING}-row/run history-materialization ceiling. "
        "Multiple runs (or raising history_video_limit inside that channel) are "
        "needed for a truly unbounded full-scan."
    )
    if clamped:
        return (
            f"requested target_n > {HISTORY_MATERIALIZE_CEILING}; clamped to the "
            f"existing channel's {HISTORY_MATERIALIZE_CEILING}/run ceiling. " + base
        )
    return base


def _assert_payload(payload: dict[str, Any], *, kol_pool_id: int, safe_target: int) -> None:
    """CHECK-style fail-fast tripwires on the job payload before any DB write."""

    assert isinstance(payload.get("url"), str) and _is_http_url(payload["url"]), "payload.url must be http(s)"
    assert payload.get("kol_pool_id") == kol_pool_id, "payload.kol_pool_id mismatch"
    assert payload.get("mode") == "account_deep", "payload.mode must be account_deep"
    mp = payload.get("max_posts")
    hl = payload.get("history_video_limit")
    assert isinstance(mp, int) and 1 <= mp <= HISTORY_MATERIALIZE_CEILING, "max_posts out of [1,120]"
    assert isinstance(hl, int) and 1 <= hl <= HISTORY_MATERIALIZE_CEILING, "history_video_limit out of [1,120]"
    assert mp == safe_target and hl == safe_target, "max_posts/history_video_limit must equal safe_target"
    assert payload.get("target_type") == "kol_profile", "payload.target_type must be kol_profile"
    # scrape_source already length-checked via validate_source_value; re-assert type.
    assert isinstance(payload.get("scrape_source"), str), "payload.scrape_source must be str"
