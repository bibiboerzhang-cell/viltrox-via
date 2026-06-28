"""backend/app/api/routers/vkpi_kol_pool.py

R59: 独立 KOL Pool 路由 + 防火墙 + 审计装饰器集成示范.

这个文件是 R59 装饰器实战示范:
  - import 操作 → 防火墙 (require_budget) + 审计
  - link 操作 → 审计 (无防火墙,因为是内部数据修改)
  - list 操作 → 无装饰器 (read-only)

新增 endpoint:
  POST /api/admin/vkpi/kol-pool/import     # 一键导入 (防火墙 + 审计)
  GET  /api/admin/vkpi/kol-pool             # 列表
  GET  /api/admin/vkpi/kol-pool/{id}        # 详情
  POST /api/admin/vkpi/kol-pool/{id}/link   # 链接到 kols 主表 (审计)

注: 现有 vkpi_product_analysis.py 也有 import_items / list_pool 的暴露,
    本文件不替换那些,而是提供独立"KOL Pool 管理"入口,语义更清晰.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from app.api.dependencies.perms import require_tab
from app.domains.kol import competitor_detector as kol_competitor_detector
from app.domains.kol import account_dossier as kol_account_dossier
from app.domains.kol import account_dossier_extract as kol_account_dossier_extract
from app.domains.kol import eleven_dimensions
from app.domains.kol import intelligence_card as kol_intelligence_card
from app.domains.kol import history_match as kol_history_match
from app.domains.kol import llm_deep_analysis as kol_llm_deep_analysis
from app.domains.kol import pool as kol_pool
from app.domains.kol import profile_discovery as kol_profile_discovery
import app.domains.kol.profile_recall as kol_profile_recall
import app.domains.kol.search_sessions as kol_search_sessions
import app.domains.kol.smart_query_planner as kol_smart_query_planner
import app.domains.kol.url_deep_crawl as kol_url_deep_crawl
import app.domains.kol.video_analysis_enqueue as kol_video_analysis_enqueue
from app.domains.intelligence import gemini_single_kol_preflight
import app.domains.intelligence.ai_brief as ai_brief
import app.domains.evidence.summary as evidence_summary
from app.domains.projects import workflow as project_workflow
from app.domains.projects import cost_estimate as project_cost_estimate
from app.domains.projects import outreach as project_outreach
import app.domains.sync.refresh_tier as refresh_tier
import app.domains.tasks.enqueue as task_enqueue
from app.domains.audit.decorator import audit_action
from app.domains.access.firewall import firewall_check


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-pool"])

# task-queue 端点已抽到 vkpi_task_queue.py(无 prefix);include 后继承本 router 的 /api/admin/vkpi,路径不变。
from app.api.routers.vkpi_task_queue import router as _task_queue_router  # noqa: E402

router.include_router(_task_queue_router)


# ─── Read endpoints (无装饰器) ──────────────────────


# ── 内聚 helper 簇行为不变迁出 → vkpi_kol_pool_helpers.py;此处 re-export 兜住所有调用点 ──
from app.api.routers.vkpi_kol_pool_helpers import (  # noqa: E402
    _attach_smart_recall_session,
    _attach_smart_url_session,
    _int_or_none,
    _KNOWN_URL_DOMAINS,
    _looks_like_url,
    _maybe_enqueue_refresh,
    _on_demand_refresh_enabled,
    _smart_query_type,
)

# ── 搜索 / 会话 / 召回 / URL 深爬端点簇行为不变迁出 → vkpi_kol_pool_search.py;无 prefix 子 router include ──
from app.api.routers.vkpi_kol_pool_search import router as _kol_pool_search_router  # noqa: E402

router.include_router(_kol_pool_search_router)


@router.get("/kol-pool")
async def list_pool(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    platform: str = Query(default=""),
    query: str = Query(default=""),
    country: str = Query(default=""),
    data_status: str = Query(default=""),
    sort_by: str = Query(default="fit"),
    enrichable: bool | None = Query(default=None),
    refresh_if_stale: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """列出 KOL Pool"""
    result = kol_pool.list_pool(
        limit=limit,
        offset=offset,
        platform=platform,
        query=query,
        country=country,
        data_status=data_status,
        sort_by=sort_by,
        enrichable=enrichable,
    )
    refresh_state = None
    items = result.get("items") if isinstance(result, dict) else []
    if refresh_if_stale and query and isinstance(items, list) and items:
        first_id = int(items[0].get("id") or 0)
        if first_id:
            refresh_state = await _maybe_enqueue_refresh(
                request,
                first_id,
                staff=staff,
                enabled=True,
                reason="search_stale_while_revalidate",
            )
    if refresh_state:
        result["refresh"] = refresh_state
    return result


@router.get("/kol-pool/summary")
def get_pool_summary(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL Pool 资产池口径统计；不等于 Daily Top100 新候选。"""
    return kol_pool.summary()


@router.get("/kol-pool/suspect-inflation")
def get_suspect_inflation_review(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """P0-3 疑似刷量/假粉复核清单;独立角标列,绝不参与 viltrox_fit_score。"""
    del staff
    return kol_pool.suspect_inflation_review_list(limit=limit, offset=offset)


@router.get("/kol-pool/workspace")
def get_pool_workspace(
    limit: int = Query(default=1200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    platform: str = Query(default=""),
    query: str = Query(default=""),
    country: str = Query(default=""),
    data_status: str = Query(default=""),
    sort_by: str = Query(default="fit"),
    enrichable: bool | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """One read-only KOL Pool workspace bundle for 100-user-friendly page boot."""
    del staff
    return kol_pool.workspace(
        limit=limit,
        offset=offset,
        platform=platform,
        query=query,
        country=country,
        data_status=data_status,
        sort_by=sort_by,
        enrichable=enrichable,
    )


@router.get("/kol-pool/{kol_pool_id}/recommendation-card")
def kol_recommendation_card(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 推荐卡:数据完整度档(A-D)+ 为什么推荐 + 展示信号(只读;档位非 fit)。"""
    from app.domains.kol import recommendation_card

    return recommendation_card.get_recommendation_card(int(kol_pool_id), staff=staff)


@router.get("/kol-pool/unified-search")
def kol_unified_search(q: str, external: bool = False, limit: int = 20, staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """段2 · 统一搜索响应模型:source/status/cost_gate/provider_status/candidate_ids/history_match 一个形。"""
    from app.domains.kol import unified_search

    return unified_search.unified_search(q, include_external=bool(external), limit=int(limit), staff=staff)


@router.get("/kol-pool/discovery/providers")
def kol_pool_discovery_providers(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """联邦发现:源注册表(自有 internal_pool 就绪;商业源 modash/hypeauditor/蝉妈妈 待 key+适配器)。"""
    from app.domains.discovery import federation

    return {"providers": federation.list_providers()}


@router.get("/kol-pool/discovery/federated-search")
def kol_pool_federated_search(q: str, limit: int = 20, staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """联邦发现:跨启用源召回候选 KOL → 归一去重(商业源未配置则诚实 not_configured)。"""
    from app.domains.discovery import federation

    return federation.federated_search(q, limit=int(limit), staff=staff)


@router.post("/kol-pool/onboarding-sweep")
def kol_onboarding_sweep(q: str, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """阶段②·KOL 建档 Durable Workflow:联邦发现+落库→富集→记忆(可恢复,串起 Apify 线)。"""
    from app.domains.kol import onboarding_workflow

    return onboarding_workflow.start_kol_onboarding(q, staff)


@router.post("/kol-pool/discovery/enroll")
def kol_pool_discovery_enroll(q: str, limit: int = 20, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """链1 KOL 自增长:联邦发现 → 外部候选自动落 Pool + 去重(进 MY KOL 仍手动勾选)。"""
    from app.domains.discovery import enroll

    return enroll.federated_discover_and_enroll(q, limit=int(limit), staff=staff)


@router.get("/kol-pool/{kol_pool_id}/enrichment")
def kol_pool_enrichment(kol_pool_id: int, staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """KOL 外部富集证据(受众/刷粉/画像/历史;独立展示,绝不并入 viltrox_fit_score)。"""
    from app.domains.discovery import enrichment

    return enrichment.get_enrichment(int(kol_pool_id))


@router.post("/kol-pool/{kol_pool_id}/enrich-via-apify")
def kol_pool_enrich_via_apify(kol_pool_id: int, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """把 Apify 用透:抓该 KOL 公开数据 → 存富集证据(env 门控防意外计费)。"""
    from app.domains.discovery import apify_enrich

    return apify_enrich.enrich_kol(int(kol_pool_id))


@router.get("/kol-pool/auto-poll/status")
def kol_pool_auto_poll_status(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """D3 · 关注 KOL 自动轮询状态:应轮询候选数 + 队列可用性(只读,全容错)。"""
    from app.domains.kol import auto_poll

    return auto_poll.auto_poll_status()


@router.get("/kol-pool/{kol_pool_id}/twin")
def kol_twin(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """C3 · KOL 数字孪生:合作判断档案(身份+为什么记住+数据等级+历史表现+学习信号+合作建议)。"""
    from app.domains.kol import twin

    return twin.get_kol_twin(int(kol_pool_id), staff=staff)


@router.get("/kol-pool/available")
def list_available_for_project(
    project_id: int = Query(..., ge=1),
    query: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
    scope: str = Query(default="favorites"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL Pool candidates not yet assigned to the project.

    scope=favorites(默认)只返本人收藏子集;scope=all 显式逃生门返全池(诊断 P0-2 裁决)。
    """
    try:
        return project_workflow.list_available_project_kols(
            project_id,
            query=query,
            limit=limit,
            scope_mode=scope,
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        if exc.__class__.__name__ == "ScopeDenied":
            raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
        raise


@router.get("/kol-pool/competitors/dashboard")
def get_pool_competitor_dashboard(
    brand: str = Query(default=""),
    limit: int = Query(default=1200, ge=1, le=1200),
    source_type: str = Query(default="legacy_excel_p2d"),
    source: str = Query(default="auto", pattern="^(auto|computed)$"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """按 1012 历史池已有资料返回竞品风险概览；默认优先读已落库关系。"""
    return kol_competitor_detector.batch_evaluate_kol_pool(
        brand=brand,
        limit=limit,
        source_type=source_type,
        prefer_persisted=source == "auto",
    )


@router.get("/kol-pool/competitors/poach-targets")
def get_competitor_poach_targets(
    category: str = Query(default="", pattern="^(|lens|monitor|flash)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """可挖角名单:对副厂开放(无原厂深度绑定)的 KOL;纯读已落库竞品关系。"""
    del staff
    return kol_competitor_detector.list_poach_targets(category=category, limit=limit)


@router.get("/kol-pool/competitors/avoid-brands")
def get_competitor_avoid_brands(
    limit: int = Query(default=500, ge=1, le=2000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """近期合作友商/原厂避雷名单:risk_tier 在 caution/avoid 的 KOL×品牌关系;纯读。"""
    del staff
    return kol_competitor_detector.list_avoid_brands(limit=limit)


@router.post("/kol-pool/batch-enrich")
@audit_action(
    action_type="kol_pool_batch_enrich",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"batch enriched {result.get('enriched', 0)} attempted {result.get('attempted', 0)}",
    metadata_extractor=lambda result, kwargs: {
        "attempted": result.get("attempted", 0) if isinstance(result, dict) else 0,
        "enriched": result.get("enriched", 0) if isinstance(result, dict) else 0,
        "complete": result.get("complete", 0) if isinstance(result, dict) else 0,
        "partial": len(result.get("partial", [])) if isinstance(result, dict) else 0,
        "errors": len(result.get("errors", [])) if isinstance(result, dict) else 0,
        "capped": result.get("capped", False) if isinstance(result, dict) else False,
    },
)
def batch_enrich_pool_items(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """小批量真实补齐候选池数据；服务端强制最多 5 条。"""
    ids = body.get("ids") or []
    if ids and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    return kol_pool.batch_enrich_items(
        ids=[int(value) for value in ids if str(value).strip().isdigit()] if ids else None,
        platform=str(body.get("platform") or ""),
        query=str(body.get("query") or ""),
        data_status=str(body.get("data_status") or "missing"),
        limit=max(1, min(int(body.get("limit") or 3), 5)),
        max_posts=max(1, min(int(body.get("max_posts") or 6), 24)),
        staff=staff,
    )


@router.post("/kol-pool/profile-deep-crawl/enqueue")
@audit_action(
    action_type="kol_profile_deep_crawl_enqueue",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str((result or {}).get("job_id") or ""),
    detail_extractor=lambda result, kwargs: f"enqueue deep crawl status={(result or {}).get('status')}",
)
def enqueue_kol_profile_deep_crawl(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """队列铁律:账号深爬入 apify_jobs(泳道可见),替代同步内爬。"""
    from app.domains.kol import url_deep_crawl as kol_url_deep_crawl

    try:
        return kol_url_deep_crawl.enqueue_profile_deep_crawl_job(
            str(body.get("url") or ""),
            kol_pool_id=body.get("kol_pool_id"),
            max_posts=int(body.get("max_posts") or 3),
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/kol-pool/comments-collect/enqueue")
@audit_action(
    action_type="kol_pool_comments_collect_enqueue",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str((result or {}).get("job_id") or ""),
    detail_extractor=lambda result, kwargs: f"enqueue comments collect status={(result or {}).get('status')}",
)
def enqueue_kol_pool_comments_collect(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """评论采集入 apify_jobs(2026-06-12 裁令"评论的展示也要有";泳道「评论采集」可见)。"""
    from app.domains.comments import collector as comments_collector

    try:
        return comments_collector.enqueue_kol_pool_comments_job(
            int(body.get("kol_pool_id") or 0),
            evidence_ids=body.get("evidence_ids") or None,
            max_comments=body.get("max_comments"),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/video-comments")
def get_kol_pool_video_comments(
    kol_pool_id: int,
    evidence_id: int = Query(..., ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读该 evidence 的已采评论(vkpi_comments,post_table=evidence;字段对齐 mapCommentRows)。"""
    del staff
    from app.domains.comments import collector as comments_collector

    try:
        return comments_collector.list_pool_video_comments(
            int(kol_pool_id), evidence_id=int(evidence_id), limit=int(limit)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/outreach-draft/enqueue")
@audit_action(
    action_type="kol_outreach_draft_enqueue",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str((result or {}).get("job_id") or ""),
    detail_extractor=lambda result, kwargs: f"enqueue outreach draft status={(result or {}).get('status')}",
)
def enqueue_kol_outreach_draft(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """联系草稿入队(2026-06-12 裁令:点联系给优化后的聊天方式;泳道「联系草稿」可见)。"""
    from app.domains.kol import outreach_draft as kol_outreach_draft

    try:
        return kol_outreach_draft.enqueue_outreach_draft_job(
            int(body.get("kol_pool_id") or 0),
            project_id=body.get("project_id"),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/kol-pool/outreach-optimize")
def optimize_kol_outreach(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """AI 优化外联文案(同步小调用):给定 KOL 名 + 主推产品 + 当前主题/正文,LLM 把这封给海外创作者的
    合作邀约润色成更自然、更口语、更高回复率的英文。**只产文案、不发送**;失败诚实回退原文。
    返回 {ok, subject, body, model}。走 llm_gateway(预算闸 + 代理)。零触 viltrox_fit_score。"""
    import json as _json
    import re as _re

    from app.platform import llm_gateway

    subject = str(body.get("subject") or "").strip()
    draft = str(body.get("body") or "").strip()
    product = str(body.get("product") or "").strip()
    kol_name = str(body.get("kol_name") or "").strip()
    if not draft and not subject:
        return {"ok": False, "reason": "empty_draft", "subject": subject, "body": draft}

    prompt = (
        "你是 Viltrox(唯卓仕,海外相机镜头品牌)的 KOL 外联文案专家。把下面这封给【海外/英文圈创作者】的\n"
        "合作邀约润色成更自然、更口语、更高回复率的**英文**(别中式生硬英文)。要求:真诚、简短、具体\n"
        "(点出一个具体的合作点),不套路营销腔、不夸大、不编造数据。\n"
        f"对象 KOL:{kol_name or '(未提供)'};主推产品:{product or 'Viltrox 镜头'}。\n"
        f"当前主题:{subject or '(空)'}\n当前正文:\n{draft or '(空)'}\n\n"
        '只输出 JSON(不要多余文字):{"subject": "优化后主题", "body": "优化后正文(英文,保留换行)"}'
    )
    resp = llm_gateway.invoke(
        prompt,
        purpose="vkpi_kol_outreach_optimize",
        max_output_tokens=1200,
        cost_tag="vkpi_kol_outreach_optimize",
        staff=staff or {},
        metadata={"kol_pool_id": int(body.get("kol_pool_id") or 0)},
    )
    text = str(resp.get("text") or "").strip()
    out_subject, out_body = subject, draft  # 兜底原文
    try:
        cleaned = _re.sub(r"^```json\s*|```$", "", text, flags=_re.MULTILINE).strip()
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s != -1 and e != -1 and e > s:
            parsed = _json.loads(cleaned[s : e + 1])
            if isinstance(parsed, dict):
                out_subject = str(parsed.get("subject") or subject).strip()
                out_body = str(parsed.get("body") or draft).strip()
    except Exception:
        pass
    return {"ok": bool(text), "subject": out_subject, "body": out_body, "model": resp.get("model")}


# ── 联系方式 / 合作时间线 / 联系草稿读端点簇行为不变迁出 → vkpi_kol_pool_intel.py(同子 router,已 include)──


@router.get("/kol-pool/favorites")
def list_kol_pool_favorites(
    limit: int = Query(default=2000, ge=1, le=5000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """本人收藏清单(staff 隔离),供 Pool 星标/My KOL 收藏集渲染。"""
    from app.domains.kol import pool_favorites

    try:
        return pool_favorites.list_favorites(staff=staff, limit=limit)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/kol-pool/needs-analysis")
def list_kol_pool_needs_analysis(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """库内有视频证据但还没 ready 深析的 KOL(供「待分析」列表 + 批量入队)。注册在 /{kol_pool_id} 之前避免被吞。"""
    del staff
    try:
        return kol_video_analysis_enqueue.list_kols_needing_video_analysis(limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"needs-analysis error: {exc}") from exc


@router.get("/kol-pool/{kol_pool_id}")
async def get_item(
    request: Request,
    kol_pool_id: int,
    refresh_if_stale: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """获取单个 KOL Pool 项"""
    try:
        result = kol_pool.get_item(int(kol_pool_id))
        refresh_state = await _maybe_enqueue_refresh(
            request,
            int(kol_pool_id),
            staff=staff,
            enabled=bool(refresh_if_stale),
            reason="detail_stale_while_revalidate",
        )
        result["freshness"] = refresh_state.get("freshness")
        result["refresh"] = refresh_state
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/detail-bundle")
def get_item_detail_bundle(
    kol_pool_id: int,
    # P9:此前 default=3/max=10 把账号详情抽屉钉死在"前 4 条";底层 evidence 早已物化全量,
    # 抬到 default=24/max=200,让单账号详情默认展示该账号(基本)全部视频,前端可按需再加载。
    # 这是 READ-ONLY 物化展示口径(便宜),不触发新的 Gemini 深析(那是另一条限量+预算闸的链)。
    video_limit: int = Query(default=24, ge=1, le=200),
    llm_limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only detail drawer bundle; does not refresh providers or touch V6 Fit."""
    del staff
    try:
        return kol_pool.detail_bundle(int(kol_pool_id), video_limit=video_limit, llm_limit=llm_limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/account-dossier")
def get_pool_item_account_dossier(
    kol_pool_id: int,
    video_limit: int = Query(default=50, ge=1, le=200),
    event_limit: int = Query(default=80, ge=1, le=300),
    deep_limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only KOL account dossier; aggregates local state without providers."""
    del staff
    try:
        return kol_account_dossier.get_kol_account_dossier(
            int(kol_pool_id),
            video_limit=video_limit,
            event_limit=event_limit,
            deep_limit=deep_limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/account-dossier-extract-job")
def enqueue_pool_item_account_dossier_extract(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue local account dossier materialization into independent profile_llm results."""
    try:
        return kol_account_dossier_extract.enqueue_account_dossier_extract_job(
            int(kol_pool_id),
            body=body or {},
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/refresh")
async def refresh_pool_item(
    request: Request,
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue a stale-while-revalidate KOL Pool refresh; does not block on providers."""
    try:
        kol_pool.get_item(int(kol_pool_id))
        return await _maybe_enqueue_refresh(
            request,
            int(kol_pool_id),
            staff=staff,
            enabled=True,
            force=bool(body.get("force")),
            reason=str(body.get("reason") or "manual_on_demand_refresh"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/enqueue-video-analysis")
def enqueue_pool_item_video_analysis(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue one final_v1 video analysis job; independent from V6 Fit."""
    evidence_id = body.get("evidence_id")
    try:
        evidence_id_int = int(evidence_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="evidence_id required") from exc
    try:
        return kol_video_analysis_enqueue.enqueue_final_v1_video_analysis(
            kol_pool_id=int(kol_pool_id),
            evidence_id=evidence_id_int,
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/kol-pool/enqueue-video-analysis-batch")
def enqueue_pool_video_analysis_batch(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue multiple final_v1 video analysis jobs; independent from V6 Fit."""
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(status_code=400, detail="items required")
    try:
        return kol_video_analysis_enqueue.enqueue_final_v1_video_analysis_batch(
            items=raw_items,
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/enqueue-all-videos")
def enqueue_pool_all_videos(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """「全视频跑」:该 KOL 全部视频证据各入队一条 final_v1;独立于 V6 Fit。"""
    try:
        return kol_video_analysis_enqueue.enqueue_all_kol_videos(
            kol_pool_id=int(kol_pool_id),
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/main-candidates")
def get_main_candidates(
    kol_pool_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """查找 KOL Pool 项可能对应的 kols 主表记录。"""
    try:
        return kol_pool.main_candidates(int(kol_pool_id), limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/competitors")
def get_pool_item_competitors(
    kol_pool_id: int,
    source: str = Query(default="auto", pattern="^(auto|computed)$"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """返回单个 KOL Pool 项与 6 个竞品的关系；默认优先读已落库关系。"""
    try:
        return kol_competitor_detector.evaluate_kol_competitors(
            int(kol_pool_id),
            prefer_persisted=source == "auto",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── 只读情报/画像/契合/证据端点簇行为不变迁出 → vkpi_kol_pool_intel.py;无 prefix 子 router include。
#    私有名 _enqueue_content_fit_on_demand / _BIO_ZH_CACHE 在下方 re-export 兜住可能的外部引用。──
from app.api.routers.vkpi_kol_pool_intel import (  # noqa: E402
    router as _kol_pool_intel_router,
    _enqueue_content_fit_on_demand,
    _BIO_ZH_CACHE,
)

router.include_router(_kol_pool_intel_router)


@router.get("/kol-pool/resolve")
def resolve_kol_pool(
    handle: str = Query(default=""),
    platform: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """#17 按 handle(可选 platform)解析到 vkpi 主池记录。

    供 mover 预览弹窗(#5)/ KOLDetailModal 真指标(#22):用 handle 拿真 kol_pool_id +
    真 followers/avg_views/合作摘要。命中返回 history_match 全量 payload;未命中诚实
    返回 matched=False(前端据此走「先入库」或显空,不再编造假指标)。
    """
    h = (handle or "").strip()
    plat = (platform or "").strip()
    if not h:
        return {"matched": False, "reason": "handle required"}
    item = {"handle": h, "display_name": h, "platform": plat}
    payload = kol_history_match.find_history_match(item, platform=plat)
    if not payload:
        return {"matched": False, "handle": h, "platform": plat}
    return payload


@router.post("/kol-pool/{kol_pool_id}/promote")
@audit_action(
    action_type="kol_pool_promote_to_main",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"promote pool_id={kwargs.get('kol_pool_id')} mode={result.get('mode')} main_kol_id={result.get('main_kol_id')}",
    metadata_extractor=lambda result, kwargs: {
        "mode": result.get("mode") if isinstance(result, dict) else "",
        "main_kol_id": result.get("main_kol_id") if isinstance(result, dict) else None,
    },
)
def promote_to_main_kol(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """自动匹配或创建 kols 主表记录并链接，替代前端手动输入 ID。"""
    try:
        return kol_pool.promote_to_main(
            int(kol_pool_id),
            staff=staff,
            mode=str(body.get("mode") or "match_or_create"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/enrich")
@audit_action(
    action_type="kol_pool_enrich",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"enriched pool_id={kwargs.get('kol_pool_id')} status={result.get('sync_status')}",
)
def enrich_pool_item(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """用真实平台 crawler 补齐单条候选的头像/播放/互动/适配度。"""
    try:
        return kol_pool.enrich_item(
            int(kol_pool_id),
            max_posts=max(1, min(int(body.get("max_posts") or 12), 50)),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─── Write endpoints (装饰器集成示范) ────────────────


@router.post("/kol-pool/import")
@firewall_check(
    platform="",  # 平台从 body 动态取,防火墙在 service 层做(此处先用 feature_flag)
    feature_flag="",  # 暂时不用 feature_flag,只做 audit
    require_budget=False,  # import 本身不调外部 API,不用 budget
    bypass_param="force",
)
@audit_action(
    action_type="kol_pool_import",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"imported {result.get('imported', 0)} skipped {result.get('skipped', 0)}",
    metadata_extractor=lambda result, kwargs: {
        "source_type": kwargs.get("body", {}).get("source_type") if isinstance(kwargs.get("body"), dict) else "",
        "platform": kwargs.get("body", {}).get("platform") if isinstance(kwargs.get("body"), dict) else "",
        "imported_count": result.get("imported", 0) if isinstance(result, dict) else 0,
    },
)
def import_pool(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """
    一键导入 KOL 数据 (CSV / Apify / 手动).
    
    Body:
      items:        list[dict] - KOL 数据列表
      source_type:  str        - "manual" | "apify" | "csv" 等
      source_ref:   str        - 来源标识 (run_id / file_name 等)
      platform:     str        - 默认平台 (item 没指定时用)
      force:        bool       - owner 可用,bypass 防火墙
    
    返回:
      {imported: int, skipped: int, items: [...]}
    """
    items = body.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    if not items:
        raise HTTPException(status_code=400, detail="items cannot be empty")
    
    return kol_pool.import_items(
        items,
        source_type=str(body.get("source_type") or "manual"),
        source_ref=str(body.get("source_ref") or ""),
        platform=str(body.get("platform") or ""),
        staff=staff,
    )


@router.post("/kol-pool/{kol_pool_id}/link")
@audit_action(
    action_type="kol_pool_link_to_main",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"linked pool_id={kwargs.get('kol_pool_id')} to main_kol_id={kwargs.get('body', {}).get('main_kol_id')}",
)
def link_to_main_kol(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """
    把 KOL Pool 项链接到 kols 主表(作为活跃合作 KOL).
    
    Body:
      main_kol_id: int - kols 表的 id
    """
    main_kol_id = body.get("main_kol_id")
    if not main_kol_id:
        raise HTTPException(status_code=400, detail="main_kol_id is required")
    
    from app.db.connection import get_conn
    from datetime import datetime, UTC
    
    conn = get_conn()
    
    # 验证 kol_pool 存在
    pool_row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not pool_row:
        raise HTTPException(status_code=404, detail="kol_pool item not found")
    
    # 验证 kols 主表存在
    main_row = conn.execute(
        "SELECT id FROM kols WHERE id=?",
        (int(main_kol_id),),
    ).fetchone()
    if not main_row:
        raise HTTPException(status_code=404, detail="main kol not found")
    
    # 链接
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE vkpi_kol_pool SET linked_main_kol_id=?, updated_at=? WHERE id=?",
        (int(main_kol_id), now, int(kol_pool_id)),
    )
    conn.commit()
    
    return {
        "kol_pool_id": int(kol_pool_id),
        "main_kol_id": int(main_kol_id),
        "linked": True,
    }


# ── C2 收藏三端点(四环漏斗第一段;依赖 migration 107,apply 前勿激活)──
@router.post("/kol-pool/{kol_pool_id}/favorite")
@audit_action(
    action_type="kol_pool_favorite",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"favorite pool_id={kwargs.get('kol_pool_id')} status={result.get('status')}",
)
def favorite_kol_pool_item(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """收藏(My KOL 归宿)。幂等:重复收藏返回 already_favorited。"""
    from app.domains.kol import pool_favorites

    try:
        return pool_favorites.add_favorite(int(kol_pool_id), staff=staff, note=str(body.get("note") or ""))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/kol-pool/{kol_pool_id}/favorite")
@audit_action(
    action_type="kol_pool_unfavorite",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"unfavorite pool_id={kwargs.get('kol_pool_id')} status={result.get('status')}",
)
def unfavorite_kol_pool_item(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """取消收藏。幂等;在役软禁止(C8)落地时在 domain 层前置。"""
    from app.domains.kol import pool_favorites

    try:
        return pool_favorites.remove_favorite(int(kol_pool_id), staff=staff)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc



