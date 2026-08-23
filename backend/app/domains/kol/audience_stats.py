"""受众画像 ensemble_v1 —— 三平台评论者抽样 -> 推断 -> 聚合 -> audience_estimated_json。

像 Modash / NanoInfluencer 的 Audience Stats:性别环 + Top countries + 语言 + 年龄桶 + 评论情报,
带样本量/覆盖率/置信度,前端标 BETA。数据链路:
  - YouTube:Data API(免费额度)抽 commentThreads 评论者 -> channels.list 批量拿
    自报 country + 白捡字段(bio/订阅数/视频数/频道创建时间,同批 API 零额外配额)。
  - Instagram / TikTok:复用库里已抓的 vkpi_comments(不新写抓取);评论不足则入队抓评论。
国家两层硬信号(逐层降置信):自报 .9 > 人名词表国籍猜 .4(评论语言不再推国家);
性别人名表 conf .8(未知留空),发布口径另出 gender_normalized(按已判定样本外推 100)。
年龄三路融合(v2,BETA):A=Gemini 批推(llm_gateway,批 50/调用,预算记账+代理)
> B=M3(可选依赖,装了就用文本模式;安装法:.venv/bin/pip install m3inference,含 torch,默认不装)
> C=频道注册年龄弱先验(注册越早年龄下界越高,conf .3)。按 conf 加权投票融合。
聚合后做经验贝叶斯收缩:prior=同垂类已有 audience_estimated 的均值,tau=50,无先验跳过。
评论情报(comment_intel,纯词表/直方零成本)由 app.domains.kol.comment_intel 提供,并入同一 JSON。
身份推断结果落 vkpi_commenter_profiles 缓存(迁移 205/206),同评论者跨 KOL 复用。
地理口径(2026-08 去假):国家只认自报 / 人名两层硬信号,硬信号样本 >= 30 才出分层,
否则 geo.method=insufficient_sample、top_countries=[];评论语言只进 languages。

模块布局(2026-08 拆分,本文件是门面 + 编排;所有公开符号仍从这里导入 / monkeypatch):
  audience_stats_sampling.py  YouTube API / 本地评论抽样 + 关注图谱
  audience_stats_geo.py       人名词表 / 身份推断 / 缓存 / 地理与性别聚合 / 收缩
  audience_stats_age.py       年龄三路融合(Gemini / M3 / 注册年龄 / 生日年 / 头像)

红线:绝不写 viltrox_fit_score、不碰 rule_v0(LLM 走 llm_gateway,rule_v0 兜底文本不当真);
全部估算值明示 method/置信度,不冒充官方数据。
网络:本地网络到 googleapis / LLM 需代理 —— 读 env HTTPS_PROXY(runtime_env.sh 会从
YTDLP_PROXY 导出),没配则在错误里提示。
"""
from __future__ import annotations

import json
import time
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.audience_avatar_llm import (  # noqa: F401 — 门面转发(tests monkeypatch 于此)
    avatar_model,
    classify_avatar_batch,
    download_avatar,
    load_avatar_gemini,
)
from app.domains.kol.audience_language import LANG_TO_MARKETS, detect_lang  # noqa: F401 — 门面转发
from app.domains.kol.audience_stats_age import (  # noqa: F401 — 门面转发
    AGE_AVATAR_BATCH,
    AGE_AVATAR_MAX_IMAGES,
    AGE_BUCKETS,
    AGE_LLM_BATCH_SIZE,
    AGE_LLM_DEADLINE_SECONDS,
    AGE_LLM_MAX_BATCHES,
    AGE_MIN_DETERMINED,
    _AGE_ALIAS,
    _age_avatar_batch,
    _age_ensemble,
    _age_from_channel_created,
    _age_from_handle,
    _age_llm_batches,
    _age_llm_failure_reason,
    _age_m3_batch,
    _extract_json_array,
    _first_name,
    _fuse_age,
    _m3_available,
    _normalize_age_bucket,
    _update_age_cache,
    _utcnow_iso,
    _validate_age_llm_batch,
)
from app.domains.kol.audience_stats_geo import (  # noqa: F401 — 门面转发
    CREATOR_DENSITY_MIN_SUBS,
    GEO_HARD_SOURCES,
    GEO_METHOD,
    GEO_MIN_SAMPLE,
    METHOD,
    SHRINK_TAU,
    _FEMALE_NAMES,
    _MALE_NAMES,
    _NAME_COUNTRY,
    _apply_shrinkage,
    _load_cached_profiles,
    _pct,
    _upsert_commenter_profiles,
    _vertical_key,
    _vertical_prior,
    aggregate_audience,
    geo_breakdown,
    infer_commenter,
)
from app.domains.kol.audience_stats_sampling import (  # noqa: F401 — 门面转发
    AFFINITY_MAX_LOOKUPS,
    AFFINITY_MIN_SHARED,
    _proxy_hint,
    _resolve_channel_id,
    _yt_api_key,
    _yt_audience_affinity,
    _yt_get,
    _youtube_channel_ref,
    sample_local_commenters,
    sample_youtube_commenters,
)

logger = get_logger(__name__)

MIN_LOCAL_COMMENTS = 30  # IG/TT 本地评论少于此数 -> 先入队抓评论,不出低质画像


# ── 编排:抽样 -> 推断(带缓存)-> 聚合 -> 落库 ──

def _infer_with_cache(conn: Any, platform: str, commenters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按 (platform, author_key) 走身份缓存:命中直接用;未命中推断后 upsert。"""
    for c in commenters:
        c["platform"] = platform
    keys = [str(c.get("author_key") or "") for c in commenters if c.get("author_key")]
    cached = _load_cached_profiles(conn, platform, keys)
    inferred: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    extras_keys = ("bio", "channel_created_at", "subscriber_count", "video_count")
    cache_hits = 0
    inferred_fresh = 0
    for c in commenters:
        key = str(c.get("author_key") or "")
        hit = cached.get(key)
        # 自报国家是最强信号:抽样带回了自报而缓存里不是 declared 口径时,重推断刷新缓存。
        if hit and not (c.get("declared_country") and hit.get("country_source") != "declared"):
            cache_hits += 1
            merged = dict(hit)
            # v2:抽样新带回的白捡字段(bio/订阅数/频道年龄)补进缓存命中行,并回写缓存。
            extras_changed = False
            # 地理去假(2026-08):旧缓存里「评论语言 -> 代表市场」冒充的 country 逐行清空并回写,
            # 让 geo_ensemble 等只读缓存的读端也不再吃到假国家;age/gender 等其它字段原样保留。
            if str(merged.get("country_source") or "") == "language":
                merged["country"], merged["country_source"], merged["country_conf"] = "", "", 0.0
                extras_changed = True
            for field in extras_keys:
                value = c.get(field)
                if value not in (None, "") and value != merged.get(field):
                    merged[field] = value
                    extras_changed = True
            if extras_changed:
                fresh.append(merged)
            # 瞬态字段(不入缓存表)也要带上:缓存命中但仍无年龄的人要走 A 路(评论文本)
            # 和 E 路(头像),丢了这俩字段等于把他们判成"无信号"。
            for tfield in ("comment_text", "avatar_url"):
                if c.get(tfield) and not merged.get(tfield):
                    merged[tfield] = c[tfield]
            inferred.append(merged)
            continue
        rec = infer_commenter(c)
        if c.get("avatar_url") and not rec.get("avatar_url"):
            rec["avatar_url"] = c["avatar_url"]
        inferred_fresh += 1
        fresh.append(rec)
        inferred.append(rec)
    written = _upsert_commenter_profiles(conn, fresh) if fresh else 0
    return inferred, {"cache_hits": cache_hits, "inferred_fresh": inferred_fresh, "cache_written": written}




def _audience_source_contract(
    platform: str,
    sample: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """把画像抽样、评论情报和 durable overlap 三条来源链明确分开。"""
    intel_payload = payload.get("comment_intel") if isinstance(payload.get("comment_intel"), dict) else {}
    overlap_payload = payload.get("overlap") if isinstance(payload.get("overlap"), dict) else {}
    live_youtube_sample = str(platform or "").lower() == "youtube"
    profile_source = "youtube_data_api_live_sample" if live_youtube_sample else "vkpi_comments_pool_evidence"
    return {
        "profile_sample": {
            "source": profile_source,
            "durable": not live_youtube_sample,
            "commenters": int(payload.get("sample_size") or 0),
            "comments_scanned": int(sample.get("comments_scanned") or 0),
        },
        "comment_intelligence": {
            "source": str(intel_payload.get("source") or profile_source),
            "durable": not live_youtube_sample,
            "comments": int(intel_payload.get("sample_size") or 0),
        },
        "overlap": {
            "source": "vkpi_comments_pool_evidence",
            "durable": True,
            "commenters": int(overlap_payload.get("self_commenters") or 0),
        },
        "contract_version": "audience_sources_v1",
    }


def refresh_audience_stats(
    kol_pool_id: int,
    *,
    max_comments: int = 400,
    enqueue_if_missing: bool = True,
    llm_max_batches: int = AGE_LLM_MAX_BATCHES,
    allow_avatar_provider: bool = True,
) -> dict[str, Any]:
    """入口:抽样 -> 推断(含 ABC 年龄融合)-> 聚合归一 -> comment_intel/overlap -> 一次写库。

    - youtube:Data API 全频道评论抽样(免费额度;本地被墙时报 network_error + 代理提示)。
    - instagram / tiktok:复用 vkpi_comments 已抓评论;不足 MIN_LOCAL_COMMENTS 条则入队抓评论
      (enqueue_kol_pool_comments_job,幂等),返回 pending_comments,下次刷新即有数据。
    llm_max_batches=0 可整体关掉 A 路(Gemini)。异常诚实返回 {status, reason}。
    红线:只写 audience_estimated_json + updated_at,绝不触 viltrox_fit_score、不碰 rule_v0。
    """
    from app.db.connection import get_conn

    started = time.time()
    conn = get_conn()
    row = conn.execute(
        "SELECT id, platform, handle, profile_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    rec = dict(row)
    platform = str(rec.get("platform") or "").strip().lower()

    if platform == "youtube":
        ref = _youtube_channel_ref(rec)
        if not ref:
            return {"status": "skipped", "reason": "no_channel_reference", "kol_pool_id": int(kol_pool_id)}
        sample = sample_youtube_commenters(ref, max_comments=max_comments)
        if sample.get("status") != "ok":
            return {**sample, "kol_pool_id": int(kol_pool_id)}
    elif platform in ("instagram", "tiktok"):
        sample = sample_local_commenters(int(kol_pool_id), conn=conn)
        if int(sample.get("comments_scanned") or 0) < MIN_LOCAL_COMMENTS:
            # 无帖可采就别入队:evidence 为空时采集 job 会 1 秒空转"done",
            # 用户按"已入队稍后刷新"的提示等不到任何结果 —— 诚实返回 no_posts 让 UI 引导先跑账号分析。
            ev_n = 0
            try:
                ev_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM vkpi_kol_video_evidence WHERE kol_pool_id=?",
                    (int(kol_pool_id),),
                ).fetchone()
                ev_n = int(dict(ev_row).get("n") or 0) if ev_row else 0
            except Exception:
                ev_n = 0
            if ev_n <= 0:
                return {
                    "status": "no_posts",
                    "kol_pool_id": int(kol_pool_id),
                    "platform": platform,
                    "comments_found": int(sample.get("comments_scanned") or 0),
                    "min_required": MIN_LOCAL_COMMENTS,
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
                "min_required": MIN_LOCAL_COMMENTS,
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
    else:
        return {"status": "unsupported_platform", "platform": platform, "kol_pool_id": int(kol_pool_id),
                "reason": "P0 支持 youtube/instagram/tiktok"}

    commenters = list(sample.get("commenters") or [])
    if not commenters:
        return {"status": "no_commenters", "kol_pool_id": int(kol_pool_id), "platform": platform,
                "comments_scanned": int(sample.get("comments_scanned") or 0),
                "reason": "no_commenter_identities_in_sample"}
    inferred, cache_stats = _infer_with_cache(conn, platform, commenters)
    # v2:年龄 ABC 三路融合(失败不阻断主流程,coverage 里诚实标注)。
    age_stats: dict[str, Any] = {"llm": {"status": "skipped", "calls": 0}, "m3": "unavailable", "counts": {}}
    try:
        age_stats = _age_ensemble(
            conn,
            platform,
            inferred,
            llm_max_batches=llm_max_batches,
            allow_avatar_provider=bool(allow_avatar_provider),
        )
    except Exception as exc:
        logger.warning("audience_stats age ensemble failed kol=%s: %s", kol_pool_id, exc)
        age_stats["error"] = str(exc)[:200]
    payload = aggregate_audience(int(kol_pool_id), inferred, conn=conn, platform=platform)
    payload["generated_at"] = _utcnow_iso()
    payload["comments_scanned"] = int(sample.get("comments_scanned") or 0)
    payload["cache"] = cache_stats
    payload["age_coverage"] = age_stats
    if platform == "youtube":
        payload["channel_id"] = str(sample.get("channel_id") or "")
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
                intel["engagement"]["reply_pct"] = _pct(reply_total, top_n + reply_total)
                intel["engagement"]["reply_basis"] = "thread_total_reply_count"
        else:
            intel = ci.comment_intel_for_kol(int(kol_pool_id), conn=conn)
        payload["comment_intel"] = intel
    except Exception as exc:
        logger.warning("comment_intel failed kol=%s: %s", kol_pool_id, exc)
        payload["comment_intel"] = {"sample_size": 0, "error": str(exc)[:200]}
    # v2:共同粉丝(audience overlap)—— 矩阵投放去重用(重叠高的 KOL 不必都投)。
    try:
        from app.domains.kol.comment_intel import compute_audience_overlap

        payload["overlap"] = compute_audience_overlap(int(kol_pool_id), conn=conn)
    except Exception as exc:
        logger.warning("audience overlap failed kol=%s: %s", kol_pool_id, exc)
        payload["overlap"] = {"items": [], "error": str(exc)[:200]}
    # v4:画像抽样、评论情报、重叠桥各自声明来源。YouTube 的画像/评论情报来自
    # 本次 Data API 抽样，不与本地 durable 评论桥混算；overlap 永远只读本地桥。
    payload["source_contract"] = _audience_source_contract(platform, sample, payload)
    # v3:关注图谱 v0(仅 YT;评论者=频道 id 可直接查公开订阅)。失败不阻断主流程。
    if platform == "youtube":
        try:
            _cids = [str(c.get("author_key") or "") for c in (sample.get("commenters") or [])]
            payload["audience_affinity"] = _yt_audience_affinity(_cids, str(sample.get("channel_id") or ""))
        except Exception as exc:
            logger.warning("audience affinity failed kol=%s: %s", kol_pool_id, exc)
            payload["audience_affinity"] = {"items": [], "error": str(exc)[:200]}
    conn.execute(
        "UPDATE vkpi_kol_pool SET audience_estimated_json=?, updated_at=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), _utcnow_iso(), int(kol_pool_id)),
    )
    conn.commit()
    llm_stats = age_stats.get("llm") if isinstance(age_stats.get("llm"), dict) else {}
    llm_degraded = bool(
        age_stats.get("error")
        or (
            int(llm_stats.get("people_in") or 0) > 0
            and str(llm_stats.get("status") or "") in {"failed", "partial"}
        )
    )
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
