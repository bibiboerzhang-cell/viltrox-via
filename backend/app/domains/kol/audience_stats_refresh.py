"""受众画像 · 刷新编排的分步实现(2026-08-30 从 audience_stats.refresh_audience_stats 提出,行为不变)。

分层:平台抽样分拣(_collect_sample)→ 年龄融合护栏(_age_stats_for)→ 聚合装配
(_build_audience_payload:comment_intel / overlap / source_contract / affinity)→
一次写库(_store_audience_payload)→ 部分降级口径(_refresh_result)。
monkeypatch 兼容:协作符号(sample_youtube_commenters / _age_ensemble / aggregate_audience /
_yt_audience_affinity / _utcnow_iso / _pct / MIN_LOCAL_COMMENTS 等)经 _live() 回门面
app.domains.kol.audience_stats 取,tests 打在门面上的补丁原样生效。
红线:只写 audience_estimated_json + updated_at,绝不触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def _live(name: str) -> Any:
    """经门面解析协作符号:tests 在 app.domains.kol.audience_stats 上 monkeypatch 的同名符号仍生效。"""
    from app.domains.kol import audience_stats as facade

    return getattr(facade, name)


# ── 平台抽样分拣 ──


def _local_evidence_count(conn: Any, kol_pool_id: int) -> int:
    try:
        ev_row = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_kol_video_evidence WHERE kol_pool_id=?",
            (int(kol_pool_id),),
        ).fetchone()
        return int(dict(ev_row).get("n") or 0) if ev_row else 0
    except Exception:
        return 0


def _insufficient_local_comments(
    conn: Any, kol_pool_id: int, platform: str, sample: dict[str, Any], enqueue_if_missing: bool
) -> dict[str, Any]:
    """IG/TT 本地评论不足:无帖先诚实 no_posts;有帖则(可选)入队抓评论。"""
    # 无帖可采就别入队:evidence 为空时采集 job 会 1 秒空转"done",
    # 用户按"已入队稍后刷新"的提示等不到任何结果 —— 诚实返回 no_posts 让 UI 引导先跑账号分析。
    if _local_evidence_count(conn, kol_pool_id) <= 0:
        return {
            "status": "no_posts",
            "kol_pool_id": int(kol_pool_id),
            "platform": platform,
            "comments_found": int(sample.get("comments_scanned") or 0),
            "min_required": _live("MIN_LOCAL_COMMENTS"),
            "enqueued": False,
            "reason": "池内暂无该 KOL 的帖子记录,先对该 KOL 跑一次账号/视频分析再生成受众统计",
        }
    enqueued = False
    enqueue_status = ""
    if enqueue_if_missing:
        try:
            from app.domains.comments.collector import enqueue_kol_pool_comments_job

            result = enqueue_kol_pool_comments_job(int(kol_pool_id), queue_lane="batch")
            enqueue_status = str(result.get("status") or "")
            enqueued = enqueue_status in ("queued", "already_queued")
        except Exception as exc:
            enqueue_status = f"enqueue_failed: {exc}"[:200]
    return {
        "status": "pending_comments" if enqueued else "partial",
        "kol_pool_id": int(kol_pool_id),
        "platform": platform,
        "comments_found": int(sample.get("comments_scanned") or 0),
        "min_required": _live("MIN_LOCAL_COMMENTS"),
        "enqueued": enqueued,
        "enqueue_status": enqueue_status,
        "reason": (
            "本地评论不足,已入队抓评论,稍后再刷新"
            if enqueued
            else (
                "comment_collection_enqueue_failed"
                if enqueue_status.startswith("enqueue_failed:")
                else "local_comments_insufficient"
            )
        ),
    }


def _collect_sample(
    conn: Any,
    kol_pool_id: int,
    rec: dict[str, Any],
    platform: str,
    max_comments: int,
    enqueue_if_missing: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """按平台抽样;拿不到可用样本时给出诚实的提前返回 (sample, early_return)。"""
    if platform == "youtube":
        ref = _live("_youtube_channel_ref")(rec)
        if not ref:
            return {}, {"status": "skipped", "reason": "no_channel_reference", "kol_pool_id": int(kol_pool_id)}
        sample = _live("sample_youtube_commenters")(ref, max_comments=max_comments)
        if sample.get("status") != "ok":
            return {}, {**sample, "kol_pool_id": int(kol_pool_id)}
        return sample, None
    if platform in ("instagram", "tiktok"):
        sample = _live("sample_local_commenters")(int(kol_pool_id), conn=conn)
        if int(sample.get("comments_scanned") or 0) < _live("MIN_LOCAL_COMMENTS"):
            return {}, _insufficient_local_comments(conn, kol_pool_id, platform, sample, enqueue_if_missing)
        return sample, None
    return {}, {"status": "unsupported_platform", "platform": platform, "kol_pool_id": int(kol_pool_id),
                "reason": "P0 支持 youtube/instagram/tiktok"}


# ── 年龄融合护栏 + 聚合装配 ──


def _age_stats_for(
    conn: Any,
    platform: str,
    inferred: list[dict[str, Any]],
    llm_max_batches: int,
    allow_avatar_provider: bool,
    kol_pool_id: int,
) -> dict[str, Any]:
    # v2:年龄 ABC 三路融合(失败不阻断主流程,coverage 里诚实标注)。
    age_stats: dict[str, Any] = {"llm": {"status": "skipped", "calls": 0}, "m3": "unavailable", "counts": {}}
    try:
        age_stats = _live("_age_ensemble")(
            conn,
            platform,
            inferred,
            llm_max_batches=llm_max_batches,
            allow_avatar_provider=bool(allow_avatar_provider),
        )
    except Exception as exc:
        logger.warning("audience_stats age ensemble failed kol=%s: %s", kol_pool_id, exc)
        age_stats["error"] = str(exc)[:200]
    return age_stats


def _comment_intel_payload(conn: Any, kol_pool_id: int, sample: dict[str, Any]) -> dict[str, Any]:
    # v2:评论情报(纯词表/直方零成本)。YT 用本次 API 抽样带回的评论;IG/TT 读 vkpi_comments。
    try:
        from app.domains.kol import comment_intel as ci

        api_comments = list(sample.get("comments") or [])
        if api_comments:
            intel = ci.analyze_comments(api_comments)
            intel["source"] = "youtube_api_sample"
            reply_total = int(sample.get("reply_total") or 0)
            if reply_total and isinstance(intel.get("engagement"), dict):
                # API 只抓 top-level:回复占比按 thread 的 totalReplyCount 口径补算。
                top_n = int(intel.get("sample_size") or 0)
                intel["engagement"]["reply_pct"] = _live("_pct")(reply_total, top_n + reply_total)
                intel["engagement"]["reply_basis"] = "thread_total_reply_count"
        else:
            intel = ci.comment_intel_for_kol(int(kol_pool_id), conn=conn)
        return intel
    except Exception as exc:
        logger.warning("comment_intel failed kol=%s: %s", kol_pool_id, exc)
        return {"sample_size": 0, "error": str(exc)[:200]}


def _overlap_payload(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    # v2:共同粉丝(audience overlap)—— 矩阵投放去重用(重叠高的 KOL 不必都投)。
    try:
        from app.domains.kol.comment_intel import compute_audience_overlap

        return compute_audience_overlap(int(kol_pool_id), conn=conn)
    except Exception as exc:
        logger.warning("audience overlap failed kol=%s: %s", kol_pool_id, exc)
        return {"items": [], "error": str(exc)[:200]}


def _affinity_payload(sample: dict[str, Any], kol_pool_id: int) -> dict[str, Any]:
    # v3:关注图谱 v0(仅 YT;评论者=频道 id 可直接查公开订阅)。失败不阻断主流程。
    try:
        _cids = [str(c.get("author_key") or "") for c in (sample.get("commenters") or [])]
        return _live("_yt_audience_affinity")(_cids, str(sample.get("channel_id") or ""))
    except Exception as exc:
        logger.warning("audience affinity failed kol=%s: %s", kol_pool_id, exc)
        return {"items": [], "error": str(exc)[:200]}


def _build_audience_payload(
    conn: Any,
    kol_pool_id: int,
    platform: str,
    sample: dict[str, Any],
    inferred: list[dict[str, Any]],
    cache_stats: dict[str, int],
    age_stats: dict[str, Any],
) -> dict[str, Any]:
    payload = _live("aggregate_audience")(int(kol_pool_id), inferred, conn=conn, platform=platform)
    payload["generated_at"] = _live("_utcnow_iso")()
    payload["comments_scanned"] = int(sample.get("comments_scanned") or 0)
    payload["cache"] = cache_stats
    payload["age_coverage"] = age_stats
    if platform == "youtube":
        payload["channel_id"] = str(sample.get("channel_id") or "")
    payload["comment_intel"] = _comment_intel_payload(conn, kol_pool_id, sample)
    payload["overlap"] = _overlap_payload(conn, kol_pool_id)
    # v4:画像抽样、评论情报、重叠桥各自声明来源。YouTube 的画像/评论情报来自
    # 本次 Data API 抽样，不与本地 durable 评论桥混算；overlap 永远只读本地桥。
    payload["source_contract"] = _live("_audience_source_contract")(platform, sample, payload)
    if platform == "youtube":
        payload["audience_affinity"] = _affinity_payload(sample, kol_pool_id)
    return payload


# ── 一次写库 + 部分降级口径 ──


def _store_audience_payload(conn: Any, kol_pool_id: int, payload: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE vkpi_kol_pool SET audience_estimated_json=?, updated_at=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), _live("_utcnow_iso")(), int(kol_pool_id)),
    )
    conn.commit()


def _llm_degraded(age_stats: dict[str, Any], llm_stats: dict[str, Any]) -> bool:
    return bool(
        age_stats.get("error")
        or (
            int(llm_stats.get("people_in") or 0) > 0
            and str(llm_stats.get("status") or "") in {"failed", "partial"}
        )
    )


def _refresh_result(
    kol_pool_id: int,
    platform: str,
    sample: dict[str, Any],
    age_stats: dict[str, Any],
    payload: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    import time

    llm_stats = age_stats.get("llm") if isinstance(age_stats.get("llm"), dict) else {}
    llm_degraded = _llm_degraded(age_stats, llm_stats)
    sample_degraded = bool(sample.get("partial"))
    result = {
        "status": "partial" if llm_degraded or sample_degraded else "ok",
        "kol_pool_id": int(kol_pool_id),
        "platform": platform,
        "sample_size": payload.get("sample_size"),
        "confidence": payload.get("confidence"),
        "audience": payload,
        "elapsed_sec": round(time.time() - started, 2),
    }
    partial_components: list[str] = []
    reasons: list[str] = []
    if sample_degraded:
        partial_components.append("comment_sample")
        reasons.append(str(sample.get("reason") or "comment_sample_partial"))
    if llm_degraded:
        partial_components.append("age_llm")
        reasons.append(str(llm_stats.get("reason") or "age_inference_unavailable"))
    if partial_components:
        result["reason"] = ",".join(reasons)
        result["partial_components"] = partial_components
    return result
