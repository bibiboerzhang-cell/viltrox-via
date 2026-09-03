"""Search-session sync + final_v1 followup enqueue cluster for apify_jobs_worker.

行为不变,从 apify_jobs_worker.py 整簇 move 出来(函数体逐字不变)。
原文件用 `from app.workers.apify_jobs_worker_session import (...)` re-export 兜住所有调用点。
本模块顶层绝不 import 原文件(避免循环);依赖只指向 barrel helpers + final_v1_extract。
红线:本模块零 fit 写。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.domains.comments.job_identity import comments_freshness_hours, comments_job_identity, normalize_evidence_ids
from app.domains.kol.final_v1_extract import upsert_deep_analysis_from_final_v1_cache
from app.domains.kol.search_session_job_analysis import (
    score_entry as _score_entry,
    search_session_analysis_summary_from_ready_cache as _search_session_analysis_summary_from_ready_cache,
    search_session_analysis_summary_from_result as _search_session_analysis_summary_from_result,
)
from app.domains.kol.search_session_job_sync import (
    rebuild_search_session_summary as _domain_rebuild_search_session_summary,
    search_session_item_counts as _domain_search_session_item_counts,
    search_session_job_state as _domain_search_session_job_state,
    search_session_status_from_items as _domain_search_session_status_from_items,
    session_url_enrichment_error as _domain_session_url_enrichment_error,
    sync_search_session_job_impl as _sync_search_session_job_impl,
)
from app.workers.apify_jobs_worker_session_convergence import (
    converge_sessions_for_job as _converge_sessions_for_job,
)
from app.domains.tasks.apify_idempotency import active_job_idempotency_key
from app.domains.tasks.search_session_lineage import search_session_lineages
from app.workers.apify_jobs_worker_helpers import (
    _int_or_none,
    _json,
    _loads,
)
from app.workers.apify_jobs_worker_lineage import (
    _LINEAGE_STAGE_ROLES,
    _item_profile_state,
    _lineage_item_state,
    _lineage_jobs_for_item,
    _lineage_role_state,
)


logger = get_logger(__name__)

# Keep the historical worker-private symbols, but point them at the one
# domain-owned implementation so worker and web replay cannot drift.
_rebuild_search_session_summary = _domain_rebuild_search_session_summary
_search_session_item_counts = _domain_search_session_item_counts
_search_session_job_state = _domain_search_session_job_state
_search_session_status_from_items = _domain_search_session_status_from_items
_session_url_enrichment_error = _domain_session_url_enrichment_error


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
    except Exception as exc:
        logger.warning("search session job sync failed | job_id=%s status=%s error=%s", job_id, raw_status, exc)
        return False
    synced = int(synced_count or 0) > 0
    if synced:
        # 同步之后再看会话是否已超时停滞(子任务排队等并发槽 / 被拦):超时按「部分完成」
        # 收敛并写明原因,不让会话无限 running。收敛失败只告警,不影响同步结果。
        try:
            _converge_sessions_for_job(conn, job_id, raw_status=raw_status)
        except Exception as exc:
            logger.warning("search session convergence failed | job_id=%s status=%s error=%s", job_id, raw_status, exc)
    return synced
