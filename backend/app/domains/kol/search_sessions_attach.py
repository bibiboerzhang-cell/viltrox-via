"""Attach-result builders for KOL search sessions.

Behavior-preserving move out of ``search_sessions.py``. Holds the URL / recall /
new-discovery result attachers plus their pure item-shaping helpers and the
apify-job payload linker. These build session items from orchestration results
and persist them via ``record_items`` (lazy-imported to avoid a circular import
with ``search_sessions``).

This module never writes ``viltrox_fit_score`` (no fit writes whatsoever); it
only mirrors the upstream ``viltrox_fit_score_untouched`` flags into summaries.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.tasks.search_session_lineage import with_search_session_lineage

from app.domains.kol.search_sessions_serde import (
    _compact_flow,
    _dict,
    _float_or_none,
    _int_or_none,
    _json_dumps,
    _list,
    _loads,
    _normalize_status,
    _text,
)


logger = get_logger(__name__)

# Confirmed from the real apify_jobs table.  Keep this contract narrow so an
# unknown future state cannot accidentally terminalize a live search session.
_TERMINAL_LINKED_JOB_STATUSES = frozenset({"done", "failed", "blocked", "triage"})


def attach_url_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    from app.domains.kol.search_sessions import record_items

    item = _url_result_item(int(session_id), result)
    session_status = _session_status_from_url_result(result)
    summary = {
        "kind": "url_deep_crawl",
        "url_type": result.get("url_type"),
        "platform": result.get("platform"),
        "execute": bool(result.get("execute")),
        "in_pool": bool(result.get("in_pool")),
        "matched_kol_pool_id": result.get("matched_kol_pool_id"),
        "item_status": item.get("status"),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }
    # 已解析出的创作者/视频信息随摘要落库(紧凑公开字段),历史列表不用翻 items 也有据可查。
    video_flow = _dict(result.get("video_flow"))
    identity = _dict(result.get("creator_identity") or video_flow.get("creator_identity"))
    if any(_text(identity.get(key)) for key in ("handle", "channel_id", "display_name")):
        summary["creator_identity"] = {
            "platform": identity.get("platform"),
            "handle": identity.get("handle"),
            "channel_id": identity.get("channel_id"),
            "display_name": identity.get("display_name"),
        }
    metadata = _dict(result.get("video_metadata") or video_flow.get("video_metadata"))
    if _text(metadata.get("title")) or _text(metadata.get("channel_name")):
        summary["video_title"] = metadata.get("title")
        summary["video_channel"] = metadata.get("channel_name")
    if result.get("url_type") not in {"profile", "video"}:
        summary["message"] = "暂不支持该平台的链接，目前支持 YouTube / Instagram / TikTok。"
    recorded = record_items(int(session_id), [item], status=session_status, summary=summary)
    recorded["jobs_linked"] = _link_job_payloads(int(session_id), recorded.get("items") or [])
    return recorded


def attach_recall_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    from app.domains.kol.search_sessions import record_items

    items: list[dict[str, Any]] = []
    rank = 1
    buckets = _dict(result.get("buckets"))
    for bucket_name in ("creator", "reviewer"):
        for raw in _list(buckets.get(bucket_name)):
            if not isinstance(raw, dict):
                continue
            kol_pool_id = _int_or_none(raw.get("kol_pool_id") or raw.get("id"))
            source_url = _text(raw.get("profile_url") or raw.get("url"))
            score = _float_or_none(raw.get("recall_rank_score") or raw.get("vector_score"))
            items.append(
                {
                    "dedupe_key": f"recall:{kol_pool_id or source_url or rank}",
                    "item_type": "recall_candidate",
                    "status": "matched",
                    "stage": "identified",
                    "rank": rank,
                    "score": score,
                    "kol_pool_id": kol_pool_id,
                    "source_url": source_url,
                    "payload": {
                        "bucket": bucket_name,
                        "handle": raw.get("handle"),
                        "display_name": raw.get("display_name"),
                        "platform": raw.get("platform"),
                        "profile_type": raw.get("profile_type"),
                        "followers": raw.get("followers"),
                        # 问题1 头像修:recall_candidate 会话项此前漏写 avatar_url(new_creator/existing_kol 都写了),
                        # 致历史回填掉头像。透传 _build_item 已填的 avatar_url。
                        "avatar_url": raw.get("avatar_url"),
                        "recall_rank_score": raw.get("recall_rank_score"),
                        "vector_score": raw.get("vector_score"),
                        "type_score": raw.get("type_score"),
                        "evidence": raw.get("evidence"),
                    },
                }
            )
            rank += 1
    pipeline_running = bool(result.get("_session_pipeline_running"))
    pipeline_progress = _dict(result.get("_session_progress"))
    summary = {
        "kind": "kol_recall",
        "items_written": len(items),
        "diagnostics": result.get("diagnostics"),
        "query": result.get("query"),
    }
    if pipeline_running:
        summary.update({"phase": "base", "progress": pipeline_progress})
    return record_items(
        int(session_id),
        items,
        status="running" if pipeline_running else "ready",
        summary=summary,
    )


def attach_new_discovery_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Attach platform-discovery candidates to an existing smart-search session."""
    from app.domains.kol.search_sessions import get_session, record_items

    items: list[dict[str, Any]] = []
    rank = 1
    for raw in _list(result.get("existing_matches")):
        if not isinstance(raw, dict):
            continue
        kol_pool_id = _int_or_none(raw.get("history_kol_pool_id") or _dict(raw.get("historical_match")).get("kol_pool_id"))
        source_url = _text(raw.get("channel_url") or raw.get("source_url"))
        items.append(
            {
                "dedupe_key": f"existing:{kol_pool_id or source_url or rank}",
                "item_type": "existing_kol",
                "status": "matched",
                "stage": "identified",
                "rank": rank,
                "score": _float_or_none(raw.get("history_match_confidence") or _dict(raw.get("historical_match")).get("match_confidence")),
                "kol_pool_id": kol_pool_id,
                "source_url": source_url,
                "payload": {
                    "source": "platform_discovery",
                    "platform": raw.get("platform"),
                    "handle": raw.get("handle"),
                    "channel_name": raw.get("channel_name"),
                    "sample_title": raw.get("sample_title"),
                    "source_url": raw.get("source_url"),
                    "channel_url": raw.get("channel_url"),
                    "avatar_url": raw.get("avatar_url"),
                    "historical_match": raw.get("historical_match"),
                },
            }
        )
        rank += 1
    for raw in _list(result.get("new_creators")):
        if not isinstance(raw, dict):
            continue
        source_url = _text(raw.get("channel_url") or raw.get("source_url"))
        handle = _text(raw.get("handle") or raw.get("channel_name"))
        platform = _text(raw.get("platform") or (result.get("platforms") or [""])[0])
        items.append(
            {
                "dedupe_key": f"new:{platform}:{handle or source_url or rank}",
                "item_type": "new_creator",
                "status": "identified",
                "stage": "identified",
                "rank": rank,
                "score": _float_or_none(raw.get("score") or raw.get("relevance_score") or raw.get("vector_score")),
                "source_url": source_url,
                "payload": {
                    "source": "platform_discovery",
                    "platform": platform,
                    "handle": raw.get("handle"),
                    "channel_name": raw.get("channel_name"),
                    "sample_title": raw.get("sample_title"),
                    "source_url": raw.get("source_url"),
                    "channel_url": raw.get("channel_url"),
                    "avatar_url": raw.get("avatar_url"),
                    "thumbnail_url": raw.get("thumbnail_url"),
                    "views": raw.get("views"),
                    "likes": raw.get("likes"),
                    "comments": raw.get("comments"),
                    "avg_views": raw.get("avg_views"),
                    "published": raw.get("published"),
                    "search_query": raw.get("search_query") or result.get("query"),
                    "market": raw.get("market") or result.get("market"),
                    # 独立展示信号(绝不并入 viltrox_fit_score):persona 相关度 + 可解释命中。
                    "relevance_score": raw.get("relevance_score"),
                    "relevance_tier": raw.get("relevance_tier"),
                    "relevance_hits": raw.get("relevance_hits"),
                    # 触达三态(2026-07-12 第二道闸):analyzing=followers 未知、已入库点火补全,
                    # 读端(get_session 展示闸)折叠为「分析中 ×N」;仅观测透传,展示以读端实时判据为准。
                    "reach_status": raw.get("reach_status"),
                },
            }
        )
        rank += 1

    existing_summary: dict[str, Any] = {}
    try:
        existing_summary = _dict(get_session(int(session_id)).get("result_summary"))
    except Exception:
        existing_summary = {}
    pipeline_running = bool(result.get("_session_pipeline_running"))
    pipeline_progress = _dict(result.get("_session_progress"))
    discovery_summary = {
        "kind": "platform_discovery",
        "query": result.get("query"),
        "status": result.get("status"),
        "platforms": result.get("platforms"),
        "counts": result.get("counts"),
        "provider_calls": result.get("provider_calls"),
        "platform_results": result.get("platform_results"),
        "errors": result.get("errors"),
        "viltrox_fit_score_untouched": True,
    }
    summary = {
        **existing_summary,
        "new_discovery": discovery_summary,
    }
    if pipeline_running:
        summary.update({"phase": "base", "progress": pipeline_progress})
    status = "running" if pipeline_running else "ready"
    if not pipeline_running and result.get("status") in {"partial", "failed"}:
        status = "partial"
    recorded = record_items(int(session_id), items, status=status, summary=summary)
    recorded["new_discovery"] = discovery_summary
    return recorded


def _session_status_from_url_result(result: dict[str, Any]) -> str:
    if not result.get("execute"):
        return "ready"
    video_flow = _dict(result.get("video_flow"))
    profile_flow = _dict(result.get("profile_flow"))
    status = _text(video_flow.get("status") or profile_flow.get("status") or result.get("status")).lower()
    if status == "queued":
        return "running"
    if status in {"already_queued"}:
        return "running"
    if status in {"already_analyzed", "ready", "ai_disabled", "not_requested", "official_channel_video", "cn_platform_video"}:
        return "ready"
    if status in {"failed", "creator_unresolved", "profile_crawl_failed", "crawl_failed"}:
        return "failed"
    return "partial" if result.get("execute") else "ready"


def _url_result_item(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    del session_id
    url = _dict(result.get("url"))
    video_flow = _dict(result.get("video_flow"))
    profile_flow = _dict(result.get("profile_flow"))
    evidence_result = _dict(video_flow.get("evidence_result"))
    enqueue_result = _dict(video_flow.get("enqueue_result"))
    ai_analysis = _dict(video_flow.get("ai_analysis") or enqueue_result.get("ai_analysis"))
    enqueue_job = _dict(enqueue_result.get("job"))
    normalized_url = _text(url.get("normalized") or url.get("input") or result.get("source_url"))
    url_type = _text(result.get("url_type"))
    item_type = "url_video" if url_type == "video" else "url_profile" if url_type == "profile" else "unknown"
    kol_pool_id = _int_or_none(video_flow.get("kol_pool_id") or profile_flow.get("kol_pool_id") or result.get("matched_kol_pool_id"))
    evidence_id = _int_or_none(video_flow.get("evidence_id") or evidence_result.get("evidence_id"))
    job_id = _int_or_none(
        enqueue_job.get("id")
        or enqueue_result.get("id")
        or enqueue_result.get("job_id")
        or video_flow.get("job_id")
        or profile_flow.get("job_id")
    )
    status = _text(video_flow.get("status") or profile_flow.get("status"))
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
    if _text(video_flow.get("operation")) == "video_url_resolve_queue":
        stage = "identified"
    if profile_flow and item_type == "url_profile":
        stage = "profile"
    return {
        "dedupe_key": f"{item_type}:{normalized_url or result.get('video_id') or result.get('handle') or 'unknown'}",
        "item_type": item_type,
        "status": _normalize_status(status, item=True),
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
            "profile_flow": _compact_flow(profile_flow),
            "video_flow": _compact_flow(video_flow),
            "ai_analysis": ai_analysis or None,
            "in_pool": result.get("in_pool"),
            "matched_kol_pool_id": result.get("matched_kol_pool_id"),
            "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched") or video_flow.get("viltrox_fit_score_untouched") or profile_flow.get("viltrox_fit_score_untouched"),
        },
    }


def _link_job_payloads(session_id: int, items: list[dict[str, Any]]) -> int:
    conn = get_conn()
    linked = 0
    linked_job_ids: list[int] = []
    for item in items:
        job_id = _int_or_none(item.get("job_id"))
        item_id = _int_or_none(item.get("id"))
        if not job_id:
            continue
        # Production PostgreSQL must serialize read/merge/write of shared-job
        # lineage.  Without the row lock, concurrent session attaches can both
        # read the old payload and the last writer silently drops the other's
        # lineage edge.  SQLite compatibility stays on its native writer lock.
        select_sql = "SELECT id, payload, status, last_error FROM apify_jobs WHERE id=?"
        if is_postgres_runtime():
            select_sql += " FOR UPDATE"
        row = conn.execute(
            select_sql,
            (int(job_id),),
        ).fetchone()
        if not row:
            continue
        row_data = dict(row)
        payload = _loads(row_data.get("payload"), {})
        if not isinstance(payload, dict):
            payload = {}
        if item_id and _text(item.get("item_type")).lower() == "url_video":
            # The video-analysis job may finish before the URL session item is
            # written.  Persist a proper role-bearing lineage edge so the
            # worker reducer can discover this relationship on a replay.
            lineage_role = (
                "resolver"
                if _text(payload.get("derive_method")).lower() == "video_url_resolve_v1"
                or _text(payload.get("target_type")).lower() == "video_url"
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
                    if isinstance(entry, dict) and _text(entry.get("role"))
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
            (_json_dumps(payload), int(job_id)),
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
            current_status = _text(current_data.get("status")).lower()
            if current_status in _TERMINAL_LINKED_JOB_STATUSES:
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
            _sync_linked_terminal_job(
                conn,
                job_id=job_id,
                status=job_status,
                last_error=last_error,
            )
    return linked


def _sync_linked_terminal_job(
    conn: Any,
    *,
    job_id: int,
    status: str,
    last_error: str = "",
) -> bool:
    """Replay an already-terminal job after lineage attach, without providers."""

    try:
        # Lazy import avoids search_sessions -> search_sessions_attach -> worker
        # module import cycles.  The compat connection wraps the same psycopg
        # connection; the worker synchronizer needs the raw cursor API.
        from app.workers.apify_jobs_worker_session import _sync_search_session_job

        sync_conn = getattr(conn, "_raw", conn)
        synced = _sync_search_session_job(
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
