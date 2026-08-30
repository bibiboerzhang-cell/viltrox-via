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
    from app.domains.kol import audience_stats_refresh as steps

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

    sample, early_return = steps._collect_sample(
        conn, int(kol_pool_id), rec, platform, max_comments, enqueue_if_missing
    )
    if early_return is not None:
        return early_return

    commenters = list(sample.get("commenters") or [])
    if not commenters:
        return {"status": "no_commenters", "kol_pool_id": int(kol_pool_id), "platform": platform,
                "comments_scanned": int(sample.get("comments_scanned") or 0),
                "reason": "no_commenter_identities_in_sample"}
    inferred, cache_stats = _infer_with_cache(conn, platform, commenters)
    age_stats = steps._age_stats_for(
        conn, platform, inferred, llm_max_batches, bool(allow_avatar_provider), int(kol_pool_id)
    )
    payload = steps._build_audience_payload(
        conn, int(kol_pool_id), platform, sample, inferred, cache_stats, age_stats
    )
    steps._store_audience_payload(conn, int(kol_pool_id), payload)
    return steps._refresh_result(int(kol_pool_id), platform, sample, age_stats, payload, started)
