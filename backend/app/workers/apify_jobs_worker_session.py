"""Search-session sync + final_v1 followup enqueue cluster for apify_jobs_worker.

行为不变,从 apify_jobs_worker.py 整簇 move 出来(函数体逐字不变)。
原文件用 `from app.workers.apify_jobs_worker_session import (...)` re-export 兜住所有调用点。
本模块顶层绝不 import 原文件(避免循环);依赖只指向 barrel helpers + final_v1_extract。
红线:本模块零 fit 写。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.domains.comments.job_identity import comments_freshness_hours, comments_job_identity, normalize_evidence_ids
from app.domains.kol.final_v1_extract import upsert_deep_analysis_from_final_v1_cache
from app.domains.kol.search_progress_contract import completion_contract
from app.domains.tasks.apify_idempotency import active_job_idempotency_key
from app.domains.tasks.search_session_lineage import search_session_lineages
from app.workers.apify_jobs_worker_helpers import (
    _as_dict,
    _derive_method,
    _int_or_none,
    _json,
    _loads,
    _target,
)
from app.workers.apify_jobs_worker_lineage import (
    _LINEAGE_STAGE_ROLES,
    _item_profile_state,
    _lineage_item_state,
    _lineage_jobs_for_item,
    _lineage_role_state,
)
from app.workers.apify_jobs_worker_session_analysis import (
    _score_entry,
    _search_session_analysis_summary_from_result,
)


logger = get_logger(__name__)


def _search_session_job_state(raw_status: str, reason: str = "") -> tuple[str, str]:
    status = str(raw_status or "").strip().lower()
    reason_text = str(reason or "").strip().lower()
    if status == "running":
        return "running", "analysis"
    if status == "queued":
        return "queued", "analysis"
    if status == "done":
        if "skipped_existing_analysis_cache" in reason_text:
            return "already_analyzed", "summary"
        return "ready", "summary"
    if status in {"failed", "blocked", "triage"}:
        return "failed", "analysis"
    return "unknown", "analysis"


def _session_url_enrichment_error(payload: dict[str, Any]) -> str:
    """Return a compact error when account/video enrichment partially failed."""

    def _flow_error(flow: dict[str, Any], label: str) -> str:
        status = str(flow.get("status") or "").strip()
        errors = _int_or_none(flow.get("errors")) or 0
        if errors <= 0 and "error" not in status:
            return ""
        messages: list[str] = []
        for item in flow.get("items") or []:
            if not isinstance(item, dict):
                continue
            error = str(item.get("error") or "").strip()
            if error:
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                title = str(
                    metadata.get("title")
                    or metadata.get("content_url")
                    or item.get("title")
                    or item.get("content_url")
                    or "video"
                ).strip()
                messages.append(f"{title}: {error}")
            if len(messages) >= 3:
                break
        detail = "; ".join(messages) if messages else status or "partial_failure"
        return f"{label}: {detail}"

    profile_flow = payload.get("profile_flow") if isinstance(payload.get("profile_flow"), dict) else {}
    video_flow = payload.get("video_flow") if isinstance(payload.get("video_flow"), dict) else {}
    representative = profile_flow.get("representative_video_analysis") or video_flow.get("representative_video_analysis")
    history = profile_flow.get("history_video_evidence") or video_flow.get("history_video_evidence")
    parts = []
    if isinstance(representative, dict):
        error = _flow_error(representative, "代表视频分析")
        if error:
            parts.append(error)
    if isinstance(history, dict):
        error = _flow_error(history, "历史视频物化")
        if error:
            parts.append(error)
    return " | ".join(parts)[:1000]


def _search_session_status_from_items(items: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "").strip() for item in items}
    if statuses.intersection({"queued", "running", "already_queued"}):
        return "running"
    if statuses.intersection({"failed"}):
        return "partial"
    if statuses.intersection({"partial"}):
        return "partial"
    if statuses:
        return "ready"
    return "ready"


def _search_session_item_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    ready = errors = skipped = executed = 0
    for item in items:
        status = str(item.get("status") or "unknown").strip()
        stage = str(item.get("stage") or "identified").strip()
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if status in {"ready", "already_analyzed"}:
            ready += 1
        if status in {"failed", "partial"}:
            errors += 1
        if status == "skipped":
            skipped += 1
        if status not in {"planned", "identified", "matched", "queued", "running", "unknown"}:
            executed += 1
    return {
        "by_status": by_status,
        "by_stage": by_stage,
        "ready": ready,
        "errors": errors,
        "skipped": skipped,
        "executed": executed,
    }


def _rebuild_search_session_summary(
    cur: Any,
    *,
    session_id: int,
    session_status: str,
) -> None:
    cur.execute(
        """
        SELECT result_summary_json
        FROM vkpi_kol_search_sessions
        WHERE id=%s
        LIMIT 1
        """,
        (int(session_id),),
    )
    session_row = cur.fetchone() or {}
    current_summary = session_row.get("result_summary_json")
    current_summary = current_summary if isinstance(current_summary, dict) else _loads(current_summary, {})
    if not isinstance(current_summary, dict):
        current_summary = {}
    cur.execute(
        """
        SELECT id, item_type, status, stage, rank, score, kol_pool_id, evidence_id, job_id, source_url, payload_json, updated_at
        FROM vkpi_kol_search_session_items
        WHERE session_id=%s
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id),),
    )
    item_rows = cur.fetchall() or []
    items: list[dict[str, Any]] = []
    for row in item_rows:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else _loads(row.get("payload_json"), {})
        item = {
            "id": row.get("id"),
            "item_type": row.get("item_type"),
            "status": row.get("status"),
            "stage": row.get("stage"),
            "rank": row.get("rank"),
            "score": row.get("score"),
            "kol_pool_id": row.get("kol_pool_id"),
            "evidence_id": row.get("evidence_id"),
            "job_id": row.get("job_id"),
            "source_url": row.get("source_url"),
            "job_status": payload.get("job_status") if isinstance(payload, dict) else None,
            "job_last_error": payload.get("job_last_error") if isinstance(payload, dict) else None,
            "analysis": payload.get("analysis") if isinstance(payload, dict) else None,
            "profile_status": str(_item_profile_state(payload).get("status") or "").strip().lower()
            if isinstance(payload, dict)
            else "",
            "downstream": payload.get("downstream_jobs") if isinstance(payload, dict) else None,
            "updated_at": row.get("updated_at").isoformat() if hasattr(row.get("updated_at"), "isoformat") else row.get("updated_at"),
        }
        items.append(item)
    counts = _search_session_item_counts(items)
    primary = next((item for item in items if str(item.get("item_type") or "").startswith("url_")), items[0] if items else {})
    progress = current_summary.get("progress") if isinstance(current_summary.get("progress"), dict) else {}
    has_progressive_contract = any(item.get("profile_status") or item.get("downstream") for item in items)
    profile_ready = sum(1 for item in items if item.get("profile_status") in {"ready", "already_analyzed"})
    profile_failed = sum(
        1
        for item in items
        if "failed" in str(item.get("profile_status") or "")
        or item.get("profile_status") in {"error", "crawl_failed", "unsupported"}
    )
    if not has_progressive_contract:
        profile_ready = int(counts.get("ready") or 0)
        profile_failed = int((counts.get("by_status") or {}).get("failed") or 0)
    stage_progress: dict[str, dict[str, int]] = {}
    total = int(progress.get("total") or len(items))
    for role in _LINEAGE_STAGE_ROLES:
        role_states: list[str] = []
        for item in items:
            downstream = item.get("downstream") if isinstance(item.get("downstream"), dict) else {}
            state = str(_as_dict(downstream.get(role)).get("state") or "not_requested").strip().lower()
            role_states.append(state if state in {"ready", "active", "failed", "not_requested"} else "failed")
        stage_progress[role] = {
            "ready": sum(1 for state in role_states if state == "ready"),
            "active": sum(1 for state in role_states if state == "active"),
            "failed": sum(1 for state in role_states if state == "failed"),
            "not_requested": max(0, total - sum(1 for state in role_states if state != "not_requested")),
        }
    active_downstream = sum(stage["active"] for stage in stage_progress.values())
    terminal_item_count = sum(
        int((counts.get("by_status") or {}).get(status) or 0)
        for status in ("ready", "already_analyzed", "partial", "failed", "skipped", "blocked", "cancelled", "canceled")
    )
    profile_active_states = {"", "unknown", "planned", "pending", "queued", "running", "retrying", "processing"}
    item_terminal_states = {"ready", "already_analyzed", "partial", "failed", "skipped", "blocked", "cancelled", "canceled"}
    profile_terminal_count = terminal_item_count
    profile_succeeded_count = profile_ready
    if has_progressive_contract:
        profile_terminal_count = sum(
            1
            for item in items
            if str(item.get("profile_status") or "").strip().lower() not in profile_active_states
            or (
                not str(item.get("profile_status") or "").strip()
                and str(item.get("status") or "").strip().lower() in item_terminal_states
            )
        )
        profile_succeeded_count = sum(
            1
            for item in items
            if (
                str(item.get("profile_status") or "").strip().lower() not in profile_active_states
                and "failed" not in str(item.get("profile_status") or "").strip().lower()
                and str(item.get("profile_status") or "").strip().lower()
                not in {"error", "crawl_failed", "unsupported"}
            )
            or (
                not str(item.get("profile_status") or "").strip()
                and str(item.get("status") or "").strip().lower() in {"ready", "already_analyzed", "partial"}
            )
        )
    profile_terminal_count = min(
        total,
        max(profile_terminal_count, int(progress.get("profile_completed") or 0)),
    )
    profile_succeeded_count = max(
        profile_succeeded_count,
        min(profile_terminal_count, int(progress.get("profile_succeeded") or 0)),
    )
    progress = {
        **progress,
        "base": max(int(progress.get("base") or 0), len(items)),
        "total": total,
        "profile_ready": profile_ready,
        "profile_failed": profile_failed,
        "profile_completed": profile_terminal_count,
        "profile_succeeded": profile_succeeded_count,
        "profile_remaining": max(0, total - profile_terminal_count),
        "complete_ready": int(counts.get("ready") or 0),
        "complete_partial": int((counts.get("by_status") or {}).get("partial") or 0),
    }
    if has_progressive_contract:
        progress.update(
            {
                "video": stage_progress["video"],
                "comments": stage_progress["comments"],
                "audience": stage_progress["audience"],
            }
        )
    phase = "profile" if session_status == "running" else ("complete" if session_status == "ready" else "partial")
    contract = completion_contract(
        base_count=int(progress.get("base") or 0),
        total=total,
        terminal_count=terminal_item_count,
        ready_count=profile_ready,
        profile_failed=profile_failed,
        active_tasks=active_downstream,
        stage_progress=stage_progress if has_progressive_contract else None,
    )
    progress.update(contract)
    summary = {
        **current_summary,
        "phase": phase,
        "progress": progress,
        "item_status": primary.get("status"),
        "job_status": primary.get("job_status"),
        "job_last_error": primary.get("job_last_error"),
        "analysis": primary.get("analysis"),
        "counts": counts,
        "items_written": len(items),
        **contract,
    }
    if session_status != "running":
        summary["terminal_synced_at"] = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET status=%s,
            result_summary_json=%s::jsonb,
            updated_at=NOW()
        WHERE id=%s
        """,
        (session_status, _json(summary), int(session_id)),
    )


def _search_session_analysis_summary_from_ready_cache(
    conn: psycopg.Connection[Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    target_type, target_id = _target(payload)
    derive_method = _derive_method(payload)
    if derive_method != "video_analysis_final_v1" or target_type != "video" or not target_id:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, result, cost
            FROM vkpi_analysis_cache
            WHERE target_type=%s
              AND target_id=%s
              AND derive_method=%s
              AND status='ready'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (target_type, target_id, derive_method),
        )
        cache = cur.fetchone()
        cur.execute(
            """
            SELECT id, kol_pool_id, content_url, title, video_title
            FROM vkpi_kol_video_evidence
            WHERE id=%s
            LIMIT 1
            """,
            (_int_or_none(target_id),),
        )
        evidence = cur.fetchone() or {}
    if not cache:
        return None
    result = cache.get("result") if isinstance(cache.get("result"), dict) else _loads(cache.get("result"), {})
    return _search_session_analysis_summary_from_result(
        cache_id=_int_or_none(cache.get("id")),
        derive_method=derive_method,
        target_type=target_type,
        target_id=target_id,
        evidence=dict(evidence),
        result=result if isinstance(result, dict) else {},
        cost=float(cache.get("cost") or 0.0),
    )


def _sync_deep_analysis_result_from_cache(
    conn: psycopg.Connection[Any],
    *,
    cache_id: int | None,
    derive_method: str,
    job_id: int,
) -> dict[str, Any] | None:
    if derive_method != "video_analysis_final_v1" or not cache_id:
        return None
    try:
        result = upsert_deep_analysis_from_final_v1_cache(conn, int(cache_id))
    except Exception as exc:
        logger.warning("final_v1 deep-result sync failed | job_id=%s cache_id=%s error=%s", job_id, cache_id, exc)
        return {"status": "failed", "reason": str(exc)[:500], "source_cache_id": cache_id}
    logger.info(
        "final_v1 deep-result sync | job_id=%s cache_id=%s status=%s action=%s deep_result_id=%s",
        job_id,
        cache_id,
        result.get("status"),
        result.get("action"),
        result.get("deep_result_id"),
    )
    return {
        key: result.get(key)
        for key in (
            "status",
            "action",
            "reason",
            "deep_result_id",
            "source_cache_id",
            "source_evidence_id",
            "kol_pool_id",
            "llm_v6_fit", "score_status", "score_missing_reason",
            "viltrox_fit_score_changed_ids",
        )
        if key in result
    }


def _enqueue_content_fit_after_final_v1(
    conn: psycopg.Connection[Any],
    *,
    job_id: int,
    deep_result: dict[str, Any] | None,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from app.workers.content_fit_followup_enqueue import (
        enqueue_content_fit_after_final_v1,
    )

    return enqueue_content_fit_after_final_v1(
        conn,
        job_id=job_id,
        deep_result=deep_result,
        source_payload=source_payload,
    )


def _enqueue_account_dossier_extract_after_final_v1(
    conn: psycopg.Connection[Any],
    *,
    job_id: int,
    deep_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not deep_result or deep_result.get("status") != "ready":
        return None
    kol_pool_id = _int_or_none(deep_result.get("kol_pool_id"))
    if not kol_pool_id:
        return None
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            # 幂等补 done 判断:同 KOL 多条视频接连触发 followup 时,前一条 dossier 秒级 done 后
            # 第二条照旧入队(E2E 实测重复 2 对)——1 小时内已 done 的直接复用,不重跑。
            cur.execute(
                """
                SELECT id, status, created_at, updated_at
                FROM apify_jobs
                WHERE job_type='account_dossier_extract'
                  AND payload->>'target_type'='kol_pool'
                  AND payload->>'target_id'=%s
                  AND (
                        status IN ('queued', 'running')
                        OR (status = 'done' AND updated_at >= NOW() - make_interval(hours => 1))
                      )
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (str(kol_pool_id),),
            )
            existing = cur.fetchone()
            if existing:
                _status = str(existing["status"])
                return {
                    "status": "recently_done" if _status == "done" else ("already_queued" if _status == "queued" else "already_running"),
                    "job_id": int(existing["id"]),
                    "kol_pool_id": kol_pool_id,
                }
            payload = {
                "queue_lane": "batch",
                "target_type": "kol_pool",
                "target_id": str(kol_pool_id),
                "derive_method": "kol_account_dossier_extract_v1",
                "analysis_kind": "profile_llm",
                "source": "final_v1_worker_followup",
                "trigger": "final_v1_done",
                "source_job_id": int(job_id),
                "source_cache_id": deep_result.get("source_cache_id"),
                "source_evidence_id": deep_result.get("source_evidence_id"),
                "query_text": f"account dossier - kol_pool #{kol_pool_id}",
            }
            idempotency_key = active_job_idempotency_key("account_dossier_extract", kol_pool_id)
            cur.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, idempotency_key, status, created_at, updated_at)
                VALUES ('account_dossier_extract', %s::jsonb, %s, 'queued', NOW(), NOW())
                ON CONFLICT (idempotency_key)
                  WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
                    AND status IN ('queued', 'running')
                DO NOTHING
                RETURNING id, status, created_at, updated_at
                """,
                (_json(payload), idempotency_key),
            )
            row = cur.fetchone() or {}
            inserted = bool(row)
            if not row:
                cur.execute(
                    """SELECT id, status, created_at, updated_at FROM apify_jobs
                       WHERE idempotency_key=%s AND status IN ('queued', 'running')
                       ORDER BY id DESC LIMIT 1""",
                    (idempotency_key,),
                )
                row = cur.fetchone() or {}
    return {
        "status": "queued" if inserted else ("already_running" if row.get("status") == "running" else "already_queued"),
        "job_id": int(row["id"]) if row.get("id") is not None else None,
        "kol_pool_id": kol_pool_id,
    }


def _kol_pool_id_from_evidence(conn: psycopg.Connection[Any], evidence_id: int) -> int | None:
    """C2:从 vkpi_kol_video_evidence 反查 kol_pool_id。final_v1 deep_result 偶发 kol_pool_id 空
    (evidence 未回填池号)但 source_evidence_id 在 → 据此补全,避免评论链静默断掉。"""
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT kol_pool_id FROM vkpi_kol_video_evidence WHERE id=%s LIMIT 1",
                (int(evidence_id),),
            )
            row = cur.fetchone() or {}
        return _int_or_none(row.get("kol_pool_id"))
    except Exception as exc:
        logger.warning("C2 evidence→kol_pool_id 反查失败 | evidence_id=%s error=%s", evidence_id, exc)
        return None


def _enqueue_comments_collect_after_final_v1(
    conn: psycopg.Connection[Any],
    *,
    job_id: int,
    deep_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """2026-06-16:final_v1 视频深析就绪 → 链式入队该 KOL 的评论采集(用户要求:评论也要抓)。
    幂等:同 KOL 已有活跃 kol_pool_comments_collect 任务则复用,不重复入队。
    C2(2026-06-16):两处前置失败原本静默 return None(评论链 0 产出无从诊断)→ 补 warning;
    kol_pool_id 空时增加 evidence 反查 fallback。"""
    if not deep_result or deep_result.get("status") != "ready":
        logger.warning(
            "C2 评论链跳过 | job_id=%s reason=deep_result_not_ready status=%s",
            job_id,
            (deep_result or {}).get("status"),
        )
        return None
    kol_pool_id = _int_or_none(deep_result.get("kol_pool_id"))
    if not kol_pool_id:
        evidence_id = _int_or_none(deep_result.get("source_evidence_id"))
        if evidence_id:
            kol_pool_id = _kol_pool_id_from_evidence(conn, evidence_id)
        if not kol_pool_id:
            logger.warning(
                "C2 评论链跳过 | job_id=%s reason=no_kol_pool_id source_evidence_id=%s",
                job_id,
                deep_result.get("source_evidence_id"),
            )
            return None
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id FROM vkpi_kol_video_evidence
                WHERE kol_pool_id=%s AND is_active IS NOT FALSE
                  AND COALESCE(evidence_type,'video')='video'
                ORDER BY COALESCE(view_count,0) DESC, id DESC LIMIT 20
                """,
                (int(kol_pool_id),),
            )
            evidence_ids = normalize_evidence_ids((row or {}).get("id") for row in (cur.fetchall() or []))
            cur.execute("SELECT payload FROM apify_jobs WHERE id=%s LIMIT 1", (int(job_id),))
            source_row = cur.fetchone() or {}
            source_payload = (
                source_row.get("payload")
                if isinstance(source_row.get("payload"), dict)
                else _loads(source_row.get("payload"), {})
            )
            target_fence = None
            if isinstance(source_payload, dict):
                from app.domains.kol.my_kol_paid_action_access import FENCE_KEY

                source_fence = source_payload.get(FENCE_KEY)
                if isinstance(source_fence, dict):
                    # A final_v1 cache hit can immediately schedule comments.
                    # Carry a fresh evidence-set snapshot so revocation or row
                    # drift still blocks the later comments worker.
                    from app.db.connection import db_connection_sync_scope, get_conn
                    from app.domains.kol.my_kol_paid_action_access import (
                        build_target_fence,
                        revalidate_target_fence,
                    )

                    with db_connection_sync_scope():
                        actor = revalidate_target_fence(
                            get_conn(),
                            source_payload,
                            expected_action="video_analysis",
                        )
                        if actor is not None:
                            target_fence = build_target_fence(
                                get_conn(),
                                action="comments_collect",
                                kol_pool_id=int(kol_pool_id),
                                staff=actor,
                                evidence_ids=evidence_ids,
                            )
            idempotency_key, data_version = comments_job_identity(int(kol_pool_id), evidence_ids)
            cur.execute(
                """
                SELECT id, status FROM apify_jobs
                WHERE job_type='kol_pool_comments_collect'
                  AND status IN ('queued', 'running')
                  AND (
                    idempotency_key=%s
                    OR (idempotency_key IS NULL AND payload->>'kol_pool_id'=%s)
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (idempotency_key, str(kol_pool_id)),
            )
            existing = cur.fetchone()
            if existing:
                return {
                    "status": "already_queued" if existing["status"] == "queued" else "already_running",
                    "job_id": int(existing["id"]),
                    "kol_pool_id": kol_pool_id,
                }
            freshness_hours = comments_freshness_hours()
            cur.execute(
                """
                SELECT id, status FROM apify_jobs
                WHERE idempotency_key=%s AND status='done'
                  AND updated_at >= NOW() - make_interval(hours => %s)
                ORDER BY updated_at DESC, id DESC LIMIT 1
                """,
                (idempotency_key, freshness_hours),
            )
            fresh = cur.fetchone()
            if fresh:
                return {
                    "status": "recently_done",
                    "job_id": int(fresh["id"]),
                    "kol_pool_id": kol_pool_id,
                    "data_version": data_version,
                    "freshness_hours": freshness_hours,
                }
            payload = {
                "queue_lane": "batch",
                "kol_pool_id": int(kol_pool_id),
                "target_type": "kol_profile",
                "target_id": int(kol_pool_id),
                "evidence_ids": evidence_ids,
                "evidence_set_hash": data_version,
                "freshness_hours": freshness_hours,
                "force_refresh": False,
                "max_comments": None,
                "query_text": f"评论采集 · kol_pool #{kol_pool_id}",
                "source": "final_v1_worker_followup",
                "trigger": "final_v1_done",
                "source_job_id": int(job_id),
            }
            if target_fence is not None:
                payload["my_kol_paid_action_fence"] = target_fence
            cur.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, idempotency_key, status, created_at, updated_at)
                VALUES ('kol_pool_comments_collect', %s::jsonb, %s, 'queued', NOW(), NOW())
                ON CONFLICT (idempotency_key)
                  WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
                    AND status IN ('queued', 'running')
                DO NOTHING
                RETURNING id, status
                """,
                (_json(payload), idempotency_key),
            )
            row = cur.fetchone() or {}
            inserted = bool(row)
            if not row:
                cur.execute(
                    """SELECT id, status FROM apify_jobs
                       WHERE idempotency_key=%s AND status IN ('queued', 'running')
                       ORDER BY id DESC LIMIT 1""",
                    (idempotency_key,),
                )
                row = cur.fetchone() or {}
    return {
        "status": "queued" if inserted else ("already_running" if row.get("status") == "running" else "already_queued"),
        "job_id": int(row["id"]) if row.get("id") is not None else None,
        "kol_pool_id": kol_pool_id,
    }


def _sync_search_session_job(
    conn: psycopg.Connection[Any],
    job_id: int,
    *,
    raw_status: str,
    reason: str = "",
    analysis_summary: dict[str, Any] | None = None,
) -> bool:
    try:
        synced_count = _sync_search_session_job_impl(
            conn,
            job_id,
            raw_status=raw_status,
            reason=reason,
            analysis_summary=analysis_summary,
        )
        return int(synced_count or 0) > 0
    except Exception as exc:
        logger.warning("search session job sync failed | job_id=%s status=%s error=%s", job_id, raw_status, exc)
        return False


def _sync_search_session_job_impl(
    conn: psycopg.Connection[Any],
    job_id: int,
    *,
    raw_status: str,
    reason: str = "",
    analysis_summary: dict[str, Any] | None = None,
) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, payload, last_error FROM apify_jobs WHERE id=%s", (int(job_id),))
        row = cur.fetchone()
    if not row:
        return 0
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else _loads(row.get("payload"), {})
    if not isinstance(payload, dict):
        return 0
    lineages = search_session_lineages(payload)
    if not lineages:
        return 0
    unique_lineages: dict[tuple[int, int], set[str]] = {}
    for entry in lineages:
        session_id = _int_or_none(entry.get("search_session_id"))
        item_id = _int_or_none(entry.get("search_session_item_id"))
        if not session_id or not item_id:
            continue
        unique_lineages.setdefault((int(session_id), int(item_id)), set()).add(
            str(entry.get("role") or "").strip().lower()
        )
    if not unique_lineages:
        return 0

    synced_items: list[dict[str, Any]] = []
    current_analysis_summary = analysis_summary
    for (session_id, item_id), roles in unique_lineages.items():
        resolver_projection: dict[str, Any] = {}
        if "resolver" in roles:
            from app.domains.kol.video_url_resolver import video_url_session_sync_projection

            resolver_projection = video_url_session_sync_projection(payload)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT payload_json
                FROM vkpi_kol_search_session_items
                WHERE id=%s
                  AND session_id=%s
                LIMIT 1
                """,
                (int(item_id), int(session_id)),
            )
            item_row = cur.fetchone() or {}
        if not item_row:
            continue
        existing_payload = item_row.get("payload_json") if isinstance(item_row.get("payload_json"), dict) else _loads(item_row.get("payload_json"), {})
        if not isinstance(existing_payload, dict):
            existing_payload = {}
        enrichment_error = _session_url_enrichment_error(existing_payload)
        progressive = any(roles)
        downstream: dict[str, Any] | None = None
        required_tasks_complete = False
        if progressive:
            state = _lineage_item_state(
                existing_payload,
                _lineage_jobs_for_item(conn, session_id=int(session_id), item_id=int(item_id)),
            )
            item_status = str(state.get("item_status") or "partial")
            stage = str(state.get("stage") or "analysis")
            downstream = state.get("downstream") if isinstance(state.get("downstream"), dict) else {}
            required_tasks_complete = bool(state.get("required_tasks_complete"))
        else:
            item_status, stage = _search_session_job_state(raw_status, reason or row.get("last_error") or "")
        if item_status in {"ready", "already_analyzed"} and enrichment_error:
            item_status = "partial"
            stage = "summary"
            required_tasks_complete = False
        if current_analysis_summary is None and item_status in {"ready", "already_analyzed", "partial"}:
            current_analysis_summary = _search_session_analysis_summary_from_ready_cache(conn, payload)
        item_error = str(enrichment_error or reason or row.get("last_error") or "")[:1000]
        item_patch: dict[str, Any] = {
            "job_status": raw_status,
            "job_last_error": item_error,
            "job_updated_at": datetime.now(timezone.utc).isoformat(),
            "required_tasks_complete": required_tasks_complete,
        }
        if downstream is not None:
            item_patch["downstream_jobs"] = downstream
        if current_analysis_summary:
            item_patch["analysis"] = current_analysis_summary
        if resolver_projection:
            item_patch.update(resolver_projection.get("payload_patch") or {})
        if downstream is not None:
            from app.domains.kol.video_url_resolver import reconcile_video_url_ai_progress

            progress_source = {**existing_payload, **(resolver_projection.get("payload_patch") or {})}
            reconciled_progress = reconcile_video_url_ai_progress(progress_source, downstream)
            if reconciled_progress:
                item_patch["video_url_resolution"] = reconciled_progress
                compact_video_flow = _as_dict(progress_source.get("video_flow"))
                item_patch["video_flow"] = {**compact_video_flow, "resolution_progress": reconciled_progress}
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE vkpi_kol_search_session_items
                    SET status=%s,
                        stage=%s,
                        kol_pool_id=COALESCE(%s, kol_pool_id),
                        evidence_id=COALESCE(%s, evidence_id),
                        payload_json = payload_json || %s::jsonb,
                        updated_at=NOW()
                    WHERE id=%s
                      AND session_id=%s
                    """,
                    (
                        item_status,
                        stage,
                        resolver_projection.get("kol_pool_id"),
                        resolver_projection.get("evidence_id"),
                        _json(item_patch),
                        int(item_id),
                        int(session_id),
                    ),
                )
                cur.execute(
                    """
                    SELECT status, stage
                    FROM vkpi_kol_search_session_items
                    WHERE session_id=%s
                    """,
                    (int(session_id),),
                )
                session_status = _search_session_status_from_items([dict(item) for item in (cur.fetchall() or [])])
                _rebuild_search_session_summary(cur, session_id=int(session_id), session_status=session_status)
        synced_items.append(
            {
                "search_session_id": int(session_id),
                "search_session_item_id": int(item_id),
                "status": item_status,
                "stage": stage,
            }
        )

    if not synced_items:
        return 0
    payload["search_session_item_statuses"] = synced_items
    payload["search_session_last_job_status"] = raw_status
    payload["search_session_last_error"] = str(reason or row.get("last_error") or "")[:500]
    first = synced_items[0]
    payload["search_session_item_status"] = first["status"]
    payload["search_session_stage"] = first["stage"]
    if current_analysis_summary:
        payload["search_session_cache_id"] = current_analysis_summary.get("cache_id")
        payload["search_session_analysis_status"] = current_analysis_summary.get("status")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET payload=%s::jsonb WHERE id=%s",
                (_json(payload), int(job_id)),
            )
    return len(synced_items)
