"""final_v1 收尾的零成本跟进(波 D·D2):深析缓存写成功 → 立刻提列镜头出镜证据。

挂在 apify_jobs_worker_gemini._write_gemini_cache 的主成功路径(cache 已在自己的事务里 commit、
job 已标 done)之后。与 content_fit / account_dossier 入队同一地位:best-effort、失败只 warning、
绝不冒泡把 final_v1 标 failed。

* 零 LLM、零外调:纯读缓存正文 → 目录归一 → 写两张派生表(UPSERT,幂等);
* 走 compat 作用域连接(? 占位),不碰 worker 自己的 psycopg 连接(%s);
* 只对 derive_method=video_analysis_final_v1 生效(keyframe_qa / v2 / 本地评测行不提列)。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import db_connection_sync_scope, get_conn
from app.domains.kol import lens_evidence as lens_extractor
from app.domains.kol import lens_evidence_followup


logger = get_logger(__name__)


def extract_lens_evidence_after_final_v1(
    *,
    cache_id: Any,
    derive_method: str,
    job_id: Any = None,
) -> dict[str, Any] | None:
    """深析完成即提列。返回提列统计(或 None=不适用);永不抛异常。"""

    if str(derive_method or "") != lens_extractor.FINAL_DERIVE_METHOD:
        return None
    try:
        cid = int(cache_id or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid <= 0:
        return None
    try:
        with db_connection_sync_scope():
            result = lens_evidence_followup.extract_for_cache_id(get_conn(), cid)
    except Exception as exc:  # noqa: BLE001 — 派生表提列失败不阻断 final_v1 主链
        logger.warning(
            "final_v1 lens evidence extract failed (non-fatal) | job_id=%s cache_id=%s exception_type=%s",
            job_id,
            cid,
            type(exc).__name__,
        )
        return {"status": "failed", "cache_id": cid, "error": type(exc).__name__}
    logger.info(
        "final_v1 lens evidence extracted | job_id=%s cache_id=%s status=%s mention_rows=%s",
        job_id,
        cid,
        result.get("status"),
        result.get("mention_rows"),
    )
    return result


__all__ = ["extract_lens_evidence_after_final_v1"]
