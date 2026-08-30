"""中国平台(bilibili / 抖音 / 小红书)「仅视频分析」通道。

设计定案(2026-07-20):
  - 只分析不入池:按地区规避红线,CN/HK/TW 创作者绝不进 KOL 池。本通道
    终态 ``cn_platform_video``(复用 official_channel_video 的诚实终态先例):
    创作者信息只存展示,不建档、不写 vkpi_kol_pool / evidence,零 fit 写入。
  - 取数走 Apify apple_yang downloader 家族(services/scraping/apify_cn.py),
    actor 直接给出带音轨的 muxed mp4 CDN 直链;yt-dlp 硬啃 bilibili 海外
    地理锁实测过不去,故绝不走 yt-dlp。
  - 下载 → R2 缓存(前端可播)→ Gemini final_v1 就地深析(official_visual
    同款 inline 先例),结果落 vkpi_analysis_cache
    (target_type='cn_platform_video', target_id='<platform>:<native_id>'),
    重复粘贴同一视频直接吃缓存不再花钱。
  - 诚实降级:直链下载不动 → 保留元数据态(media_degraded),绝不假排队;
    预算闸不放行 → ai_analysis 诚实标 ai_disabled。
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.url_deep_crawl_helpers import (
    CN_SHORT_LINK_HOSTS,
    CN_VIDEO_ANALYSIS_PLATFORMS,
)

logger = get_logger(__name__)

CN_PLATFORM_VIDEO_STATUS = "cn_platform_video"
CN_VIDEO_CACHE_TARGET_TYPE = "cn_platform_video"
FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
CN_PLATFORM_NOTICE = "中国平台视频：仅做内容分析，不建人选档案。"
_SHORT_LINK_TIMEOUT_SECONDS = 12
_PLATFORM_LABELS = {"bilibili": "Bilibili", "douyin": "抖音", "xiaohongshu": "小红书"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def cn_platform_video_flow_plan(classified: Any) -> dict[str, Any]:
    """HTTP dry-run 计划(零 provider 调用):真实执行全在 durable worker。"""

    platform = _text(getattr(classified, "platform", ""))
    return {
        "status": "cn_platform_video_planned",
        "operation": "cn_platform_video_analysis",
        "provider_calls_performed": False,
        "provider_source": None,
        "creator_resolution_status": "pending_worker",
        "creator_identity": None,
        "video_metadata": None,
        "cn_platform_video": True,
        "cn_platform_notice": CN_PLATFORM_NOTICE,
        "platform_label": _PLATFORM_LABELS.get(platform, platform),
        "message": (
            f"识别为{_PLATFORM_LABELS.get(platform, platform)}视频链接。"
            "确认后在后台完成元数据抓取、视频缓存与 AI 内容深析；不建人选档案。"
        ),
        "would_write": False,
        "would_enqueue_worker": True,
        "business_tables_written": False,
        "llm_calls_performed": False,
        "viltrox_fit_touched": False,
    }


def _expand_cn_short_link(url: str) -> str:
    """展开 b23.tv / v.douyin.com / xhslink.com 短链(拿全量 URL 与 xsec_token)。

    urllib 自动跟随跳转,取 response.geturl() 即终点 URL。直连不走任何代理
    (Decodo 无 CN 出口);任何失败都回退原 URL,不毁主链。
    """
    from urllib.parse import urlparse

    current = _text(url)
    host = (urlparse(current).netloc or "").lower().removeprefix("www.")
    if host not in CN_SHORT_LINK_HOSTS:
        return current
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(current, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(request, timeout=_SHORT_LINK_TIMEOUT_SECONDS) as response:
            final_url = _text(response.geturl())
        return final_url or current
    except Exception:
        logger.warning("cn short link expand failed url=%s", current[:120], exc_info=True)
        return current


def _cache_target_id(platform: str, video_id: str) -> str:
    return f"{platform}:{video_id}"


def _load_ready_cn_analysis(platform: str, video_id: str) -> dict[str, Any] | None:
    try:
        row = get_conn().execute(
            """
            SELECT id, result, model, updated_at FROM vkpi_analysis_cache
            WHERE target_type = ? AND target_id = ? AND derive_method = ? AND status = 'ready'
            """,
            (CN_VIDEO_CACHE_TARGET_TYPE, _cache_target_id(platform, video_id), FINAL_V1_DERIVE_METHOD),
        ).fetchone()
    except Exception:
        logger.warning("cn video analysis cache lookup failed", exc_info=True)
        return None
    if not row:
        return None
    data = dict(row)
    result = data.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}
    return {
        "cache_id": data.get("id"),
        "model": data.get("model"),
        "updated_at": str(data.get("updated_at") or ""),
        "result": result if isinstance(result, dict) else {},
    }


def _store_cn_analysis(
    *,
    platform: str,
    video_id: str,
    shaped: dict[str, Any],
    model: str,
    triggered_by: Any,
) -> int | None:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            INSERT INTO vkpi_analysis_cache (
              target_type, target_id, model, derive_method, result, cost,
              status, triggered_by_user_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, NOW(), NOW())
            ON CONFLICT (target_type, target_id, derive_method)
            DO UPDATE SET
              model = EXCLUDED.model,
              result = EXCLUDED.result,
              status = 'ready',
              updated_at = NOW()
            RETURNING id
            """,
            (
                CN_VIDEO_CACHE_TARGET_TYPE,
                _cache_target_id(platform, video_id),
                _text(model) or "gemini_video",
                FINAL_V1_DERIVE_METHOD,
                json.dumps(shaped, ensure_ascii=False, default=str),
                0.0,
                _int_or_none(triggered_by),
            ),
        ).fetchone()
        conn.commit()
        return int(dict(row)["id"]) if row else None
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("回滚失败(best-effort)", exc_info=True)
        logger.warning("cn video analysis cache write failed", exc_info=True)
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _compact_cn_analysis(shaped: dict[str, Any]) -> dict[str, Any]:
    """给会话条目/前端的紧凑投影(门面零内部术语:只给摘要与关键分)。"""
    from app.domains.kol.final_v1_extract import _as_dict, _score_from_value

    layer1 = _as_dict(shaped.get("layer1_visual_content"))
    layer6 = _as_dict(shaped.get("layer6_flags_and_scores"))
    scores_raw = _as_dict(layer6.get("scores"))
    scores: dict[str, float] = {}
    for key, alias in (
        ("content_quality_score", "content_quality"),
        ("viewer_heart_score", "viewer_heart"),
        ("product_proof_score", "product_proof"),
    ):
        value, _conf = _score_from_value(scores_raw.get(key))
        if value is not None:
            scores[alias] = float(value)
    summary = _text(layer1.get("content_summary") or shaped.get("content_summary"))
    return {
        "schema_version": "cn_platform_video_v1",
        "content_summary": summary[:600],
        "content_genre": _text(layer1.get("content_genre"))[:120],
        "scores": scores,
        "model": _text(shaped.get("model")),
        "generated_at": _text(shaped.get("generated_at")),
    }


def _shape_cn_final_v1(
    *,
    raw: dict[str, Any],
    platform: str,
    video_id: str,
    content_url: str,
    metadata: dict[str, Any],
    creator: dict[str, Any],
) -> dict[str, Any]:
    from datetime import datetime, timezone

    final = raw.get("video_analysis_final_v1") if isinstance(raw.get("video_analysis_final_v1"), dict) else {}
    model_name = _text(raw.get("model") or raw.get("method")) or "gemini_video"
    return {
        "schema_version": "video_analysis_final_v1",
        "status": "completed",
        "mock": False,
        "analysis_method": FINAL_V1_DERIVE_METHOD,
        "model": model_name,
        "target_type": CN_VIDEO_CACHE_TARGET_TYPE,
        "target_id": _cache_target_id(platform, video_id),
        "source": {
            "url": content_url,
            "title": metadata.get("title"),
            "platform": platform,
            "creator_name": creator.get("display_name"),
            "kol_pool_id": None,
            "enrollment": "skipped_by_design_cn_platform",
        },
        "video_metadata": metadata,
        "creator_identity": creator,
        "layer1_visual_content": final.get("layer1_visual_content") or {},
        "layer2_viewer_emotion": final.get("layer2_viewer_emotion") or {},
        "layer3_three_values": final.get("layer3_three_values") or {},
        "layer4_attribution": final.get("layer4_attribution") or {},
        "layer5_recommendations": final.get("layer5_recommendations") or {},
        "layer6_flags_and_scores": final.get("layer6_flags_and_scores") or {},
        "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
        "cost_authority": _text(raw.get("cost_authority")) or "llm_production_google_generate_content_v1",
        "viltrox_fit_score_untouched": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _cn_budget_gate(platform: str, video_id: str) -> dict[str, Any]:
    """与 final_v1 入队同一读法的 Google 预算/就绪闸(不放行=诚实 ai_disabled)。"""
    from app.domains.kol.video_analysis_enqueue import (
        LLM_BUDGET_SCOPE,
        LLM_MAX_OUTPUT_TOKENS,
        PRODUCTION_VIDEO_MODEL,
        _google_budget,
    )
    from app.platform import llm_gateway

    preflight = llm_gateway.budget_preflight(
        f"final_v1 cn_platform_video {platform}:{video_id}",
        purpose="vkpi_analysis_worker",
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        preferred_provider="google",
        model_override=PRODUCTION_VIDEO_MODEL,
        model_fallbacks=[],
        execution_class="production",
        cost_tag=LLM_BUDGET_SCOPE,
        require_configured=False,
    )
    return _google_budget(preflight)


def _run_cn_gemini_final_v1(
    *,
    video_path: str,
    title: str,
    creator_name: str,
    platform: str,
    video_id: str,
    triggered_by: Any,
) -> dict[str, Any]:
    from app.domains.kol.video_analysis_enqueue import LLM_BUDGET_SCOPE, PRODUCTION_VIDEO_MODEL
    from app.services.ai.analyzers import gemini_video

    llm_context = {
        "purpose": "audit_video_analysis",
        "cost_tag": LLM_BUDGET_SCOPE,
        "triggered_by": triggered_by,
        "execution_class": "production",
        "metadata": {
            "surface": "cn_platform_video",
            "task_binding": "audit_video_analysis",
            "target_type": CN_VIDEO_CACHE_TARGET_TYPE,
            "target_id": _cache_target_id(platform, video_id),
            "platform": platform,
            "phase": "video_analysis",
            "target_label": f"cn_video:{platform}:{video_id}",
        },
    }
    return asyncio.run(
        gemini_video.analyze_local_video_with_gemini(
            video_path,
            title,
            creator_name,
            schema_version="final_v1",
            final_v1_models=[PRODUCTION_VIDEO_MODEL],
            llm_context=llm_context,
        )
    )


def _ai_state(state: str, reason: str, *, gate_reason: str = "", allowed: bool = False) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "gate_reason": gate_reason,
        "model_readiness_status": "production_ready" if allowed else "not_ready",
        "provider_calls_allowed": allowed,
    }


def _terminal_result(
    *,
    platform: str,
    video_id: str,
    metadata: dict[str, Any] | None,
    creator: dict[str, Any] | None,
    cached_video_url: str | None,
    ai_analysis: dict[str, Any],
    cn_analysis: dict[str, Any] | None,
    resolution_progress: dict[str, Any],
    provider_calls_performed: bool,
    llm_calls_performed: bool,
    media_degraded: bool = False,
    media_degraded_reason: str = "",
    analysis_cache_id: int | None = None,
) -> dict[str, Any]:
    return {
        "status": CN_PLATFORM_VIDEO_STATUS,
        "operation": "cn_platform_video_analysis",
        "platform": platform,
        "video_id": video_id,
        "kol_pool_id": None,
        "evidence_id": None,
        "creator_identity": creator or None,
        "video_metadata": metadata or None,
        "cached_video_url": cached_video_url,
        "ai_analysis": ai_analysis,
        "cn_analysis": cn_analysis or None,
        "analysis_cache_id": analysis_cache_id,
        "cn_platform_notice": CN_PLATFORM_NOTICE,
        "media_degraded": bool(media_degraded),
        "media_degraded_reason": _text(media_degraded_reason) or None,
        "video_flow": {
            "status": CN_PLATFORM_VIDEO_STATUS,
            "operation": "cn_platform_video_analysis",
            "cn_platform_video": True,
            "cn_platform_notice": CN_PLATFORM_NOTICE,
            "creator_identity": creator or None,
            "video_metadata": metadata or None,
            "cached_video_url": cached_video_url,
            "ai_analysis": ai_analysis,
            "cn_analysis": cn_analysis or None,
            "media_degraded": bool(media_degraded),
            "media_degraded_reason": _text(media_degraded_reason) or None,
            "message": CN_PLATFORM_NOTICE,
            "viltrox_fit_score_untouched": True,
        },
        "resolution_progress": resolution_progress,
        "provider_calls_performed": bool(provider_calls_performed),
        "llm_calls_performed": bool(llm_calls_performed),
        "business_tables_written": bool(analysis_cache_id),
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


def run_cn_platform_video_for_job(
    payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    authorization_checkpoint: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """CN 平台视频 durable worker 门面；副作用顺序由 runtime 显式编排。"""

    from app.domains.kol.cn_platform_video_runtime import (
        CNPlatformVideoHooks,
        execute_cn_platform_video,
    )
    from app.domains.kol.url_deep_crawl import classify_url
    from app.domains.kol.video_url_resolver import (
        _emit,
        _progress,
        find_official_channel_match,
        initial_video_url_resolution_progress,
    )
    from app.platform.apify_budget import current_apify_execution_context
    from app.services.scraping.apify_cn import scrape_cn_platform_video

    hooks = CNPlatformVideoHooks(
        classify_url=classify_url,
        emit=_emit,
        progress=_progress,
        find_official_channel_match=find_official_channel_match,
        initial_progress=initial_video_url_resolution_progress,
        current_apify_execution_context=current_apify_execution_context,
        scrape_cn_platform_video=scrape_cn_platform_video,
        expand_short_link=_expand_cn_short_link,
        load_ready_analysis=_load_ready_cn_analysis,
        store_analysis=_store_cn_analysis,
        budget_gate=_cn_budget_gate,
        run_gemini=_run_cn_gemini_final_v1,
        shape_final_v1=_shape_cn_final_v1,
        compact_analysis=_compact_cn_analysis,
        terminal_result=_terminal_result,
        ai_state=_ai_state,
        int_or_none=_int_or_none,
        text=_text,
    )
    return execute_cn_platform_video(
        payload,
        staff=staff,
        progress_callback=progress_callback,
        authorization_checkpoint=authorization_checkpoint,
        hooks=hooks,
    )
