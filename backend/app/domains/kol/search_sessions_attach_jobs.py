"""URL-item shaping and durable job linkage for KOL search sessions.

This module contains the mechanical implementation details extracted from
``search_sessions_attach``.  The compatibility module keeps thin wrappers so
existing imports and monkeypatch points continue to work.
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger
from app.domains.kol import search_session_job_sync
from app.domains.tasks.search_session_lineage import with_search_session_lineage


logger = get_logger(__name__)


def url_result_item(
    session_id: int,
    result: dict[str, Any],
    *,
    dict_value: Callable[[Any], dict[str, Any]],
    int_or_none: Callable[[Any], int | None],
    text: Callable[[Any], str],
    normalize_status: Callable[..., str],
    compact_flow: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    del session_id
    url = dict_value(result.get("url"))
    video_flow = dict_value(result.get("video_flow"))
    profile_flow = dict_value(result.get("profile_flow"))
    evidence_result = dict_value(video_flow.get("evidence_result"))
    enqueue_result = dict_value(video_flow.get("enqueue_result"))
    ai_analysis = dict_value(video_flow.get("ai_analysis") or enqueue_result.get("ai_analysis"))
    enqueue_job = dict_value(enqueue_result.get("job"))
    normalized_url = text(url.get("normalized") or url.get("input") or result.get("source_url"))
    url_type = text(result.get("url_type"))
    item_type = "url_video" if url_type == "video" else "url_profile" if url_type == "profile" else "unknown"
    kol_pool_id = int_or_none(video_flow.get("kol_pool_id") or profile_flow.get("kol_pool_id") or result.get("matched_kol_pool_id"))
    evidence_id = int_or_none(video_flow.get("evidence_id") or evidence_result.get("evidence_id"))
    job_id = int_or_none(
        enqueue_job.get("id")
        or enqueue_result.get("id")
        or enqueue_result.get("job_id")
        or video_flow.get("job_id")
        or profile_flow.get("job_id")
    )
    status = text(video_flow.get("status") or profile_flow.get("status"))
    if not status:
        status = "matched" if result.get("in_pool") else "identified"
    if status == "ready" and not result.get("execute"):
        status = "identified"
    # ``stage`` is the durable pipeline phase and is constrained by migration
    # 103 to identified/profile/evidence/analysis/summary.  AI availability is
    # a terminal status/reason inside the analysis phase, never a new stage.
    stage = (
        "analysis"
        if status in {"queued", "already_queued", "already_analyzed", "ai_disabled", "not_requested"}
        else "identified"
    )
    if text(video_flow.get("operation")) == "video_url_resolve_queue":
        stage = "identified"
    if profile_flow and item_type == "url_profile":
        stage = "profile"
    return {
        "dedupe_key": f"{item_type}:{normalized_url or result.get('video_id') or result.get('handle') or 'unknown'}",
        "item_type": item_type,
        "status": normalize_status(status, item=True),
        "stage": stage,
        "rank": 1,
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "job_id": job_id,
        "source_url": normalized_url,
        "payload": {
            "url_type": result.get("url_type"),
            "platform": result.get("platform"),
            "video_id": result.get("video_id"),
            "handle": result.get("handle"),
            "channel_id": result.get("channel_id"),
            "creator_identity": result.get("creator_identity") or video_flow.get("creator_identity"),
            "video_metadata": result.get("video_metadata") or video_flow.get("video_metadata"),
            "profile_flow": compact_flow(profile_flow),
            "video_flow": compact_flow(video_flow),
            "ai_analysis": ai_analysis or None,
            "in_pool": result.get("in_pool"),
            "matched_kol_pool_id": result.get("matched_kol_pool_id"),
            "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched") or video_flow.get("viltrox_fit_score_untouched") or profile_flow.get("viltrox_fit_score_untouched"),
        },
    }


def link_job_payloads(
    session_id: int,
    items: list[dict[str, Any]],
    *,
    get_connection: Callable[[], Any],
    postgres_runtime: Callable[[], bool],
    int_or_none: Callable[[Any], int | None],
    text: Callable[[Any], str],
    loads: Callable[[Any, Any], Any],
    json_dumps: Callable[[Any], str],
    terminal_statuses: frozenset[str],
    sync_linked_terminal_job: Callable[..., bool],
) -> int:
    conn = get_connection()
    linked = 0
    linked_job_ids: list[int] = []
    for item in items:
        job_id = int_or_none(item.get("job_id"))
        item_id = int_or_none(item.get("id"))
        if not job_id:
            continue
        # Production PostgreSQL must serialize read/merge/write of shared-job
        # lineage.  Without the row lock, concurrent session attaches can both
        # read the old payload and the last writer silently drops the other's
        # lineage edge.  SQLite compatibility stays on its native writer lock.
        select_sql = "SELECT id, payload, status, last_error FROM apify_jobs WHERE id=?"
        if postgres_runtime():
            select_sql += " FOR UPDATE"
        row = conn.execute(
            select_sql,
            (int(job_id),),
        ).fetchone()
        if not row:
            continue
        row_data = dict(row)
        payload = loads(row_data.get("payload"), {})
        if not isinstance(payload, dict):
            payload = {}
        if item_id and text(item.get("item_type")).lower() == "url_video":
            # The video-analysis job may finish before the URL session item is
            # written.  Persist a proper role-bearing lineage edge so the
            # worker reducer can discover this relationship on a replay.
            lineage_role = (
                "resolver"
                if text(payload.get("derive_method")).lower() == "video_url_resolve_v1"
                or text(payload.get("target_type")).lower() == "video_url"
                else "video"
            )
            payload = with_search_session_lineage(
                payload,
                search_session_id=int(session_id),
                search_session_item_id=int(item_id),
                role=lineage_role,
            )
            # Older late-linked rows carried scalar session/item ids without a
            # role.  with_search_session_lineage faithfully imports that legacy
            # edge; drop only the empty-role copy now that the explicit video
            # edge exists, otherwise reducers see two aliases for one job.
            legacy_lineages = payload.get("search_session_lineage")
            if isinstance(legacy_lineages, list):
                payload["search_session_lineage"] = [
                    entry
                    for entry in legacy_lineages
                    if isinstance(entry, dict) and text(entry.get("role"))
                ]
        else:
            # Preserve the legacy scalar link for profile queue jobs.  Their
            # terminal reducer is intentionally the non-progressive path.
            payload["search_session_id"] = int(session_id)
            if item_id:
                payload["search_session_item_id"] = int(item_id)
        payload["search_session_item_status"] = item.get("status")
        payload["search_session_stage"] = item.get("stage")
        conn.execute(
            "UPDATE apify_jobs SET payload=?::jsonb WHERE id=?",
            (json_dumps(payload), int(job_id)),
        )
        if item_id:
            linked_job_ids.append(int(job_id))
        linked += 1
    if linked:
        conn.commit()
        # Re-read after the lineage commit.  Reading status before the payload
        # update is racy: a worker can finish between that read and this commit,
        # observe no lineage, and leave the session item queued forever.
        terminal_jobs: list[tuple[int, str, str]] = []
        for job_id in dict.fromkeys(linked_job_ids):
            current = conn.execute(
                "SELECT id, status, last_error FROM apify_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            if not current:
                continue
            current_data = dict(current)
            current_status = text(current_data.get("status")).lower()
            if current_status in terminal_statuses:
                terminal_jobs.append(
                    (
                        int(job_id),
                        current_status,
                        str(current_data.get("last_error") or "")[:2000],
                    )
                )
        # Close the read transaction before the worker synchronizer starts its
        # own transaction/savepoints on the same raw psycopg connection.
        conn.commit()
        for job_id, job_status, last_error in terminal_jobs:
            sync_linked_terminal_job(
                conn,
                job_id=job_id,
                status=job_status,
                last_error=last_error,
            )
    return linked


def sync_linked_terminal_job(
    conn: Any,
    *,
    job_id: int,
    status: str,
    last_error: str = "",
) -> bool:
    """Replay an already-terminal job after lineage attach, without providers."""

    try:
        # The compat connection wraps the same psycopg connection; the
        # domain-owned synchronizer needs the raw cursor API.  Workers import
        # the same implementation through a compatibility facade.
        sync_conn = getattr(conn, "_raw", conn)
        synced = search_session_job_sync.sync_search_session_job(
            sync_conn,
            int(job_id),
            raw_status=str(status or "").strip().lower(),
            reason=str(last_error or "")[:2000],
        )
        if synced is not True:
            # The worker-level wrapper deliberately catches sync errors so a
            # provider job is not failed by an observability write.  Its bool
            # result prevents this late-replay caller from falsely committing
            # and reporting reconciliation success after that catch.
            rollback = getattr(sync_conn, "rollback", None)
            if callable(rollback):
                rollback()
            logger.warning(
                "search session terminal replay not applied | job_id=%s status=%s",
                job_id,
                status,
            )
            return False
        # psycopg SELECTs open an implicit outer transaction.  The worker
        # synchronizer's nested transaction blocks are savepoints in that
        # transaction, not a final commit.  Explicitly commit here or a later
        # compat-connection close rolls the replay back (the session-1085 bug).
        sync_conn.commit()
        return True
    except Exception as exc:
        rollback = getattr(locals().get("sync_conn"), "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                logger.debug("search session terminal replay rollback failed", exc_info=True)
        # Session reconciliation is an observability repair and must not roll
        # back the already-valid URL result/linkage write.
        logger.warning(
            "search session terminal replay failed | job_id=%s status=%s error=%s",
            job_id,
            status,
            exc,
        )
        return False
