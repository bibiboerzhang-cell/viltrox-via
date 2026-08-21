"""backend/app/api/routers/vkpi_kol_pool_jobs.py

行为不变迁出:竞品只读 + 批量富集/深爬/评论采集/联系草稿/外联优化 端点簇。
原 vkpi_kol_pool.py 通过 router.include_router(_kol_pool_jobs_router) 兜住;
本子 router 无 prefix,include 后继承父 router 的 /api/admin/vkpi,路径逐字不变。
"""
from __future__ import annotations

from app.core.logging import get_logger
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.dependencies.perms import require_tab
from app.domains.kol import competitor_detector as kol_competitor_detector
from app.domains.audit.decorator import audit_action


logger = get_logger(__name__)

router = APIRouter(tags=["vkpi-kol-pool"])


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
    detail_extractor=lambda result, kwargs: f"batch enrich queued {result.get('queued', 0)} attempted {result.get('attempted', 0)}",
    metadata_extractor=lambda result, kwargs: {
        "attempted": result.get("attempted", 0) if isinstance(result, dict) else 0,
        "enriched": result.get("enriched", 0) if isinstance(result, dict) else 0,
        "complete": result.get("complete", 0) if isinstance(result, dict) else 0,
        "partial": len(result.get("partial", [])) if isinstance(result, dict) else 0,
        "errors": len(result.get("errors", [])) if isinstance(result, dict) else 0,
        "capped": result.get("capped", False) if isinstance(result, dict) else False,
    },
)
async def batch_enrich_pool_items(
    request: Request,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """小批量持久排队补齐候选池数据；请求线程不运行 crawler。"""
    from app.domains.kol import pool as kol_pool
    import app.domains.tasks.enqueue as task_enqueue

    ids = body.get("ids") or []
    if ids and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    safe_limit = max(1, min(int(body.get("limit") or 3), 5))
    selected_ids = [int(value) for value in ids[:safe_limit] if str(value).strip().isdigit()]
    if not selected_ids:
        selected = kol_pool.list_pool(
            limit=safe_limit,
            platform=str(body.get("platform") or ""),
            query=str(body.get("query") or ""),
            data_status=str(body.get("data_status") or "missing"),
            sort_by="missing",
            enrichable=True,
        )
        selected_ids = [int(row["id"]) for row in selected.get("items") or [] if row.get("id")]
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    jobs: list[dict] = []
    try:
        for kol_pool_id in selected_ids:
            queued = await task_enqueue.enqueue_kol_pool_on_demand_refresh(
                queue,
                kol_pool_id,
                reason="manual_batch_enrich",
                max_posts=max(1, min(int(body.get("max_posts") or 3), 3)),
                staff=staff,
            )
            jobs.append({"kol_pool_id": kol_pool_id, **queued})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "queued",
        "attempted": len(selected_ids),
        "queued": len(jobs),
        "job_ids": [item.get("task_id") for item in jobs],
        "jobs": jobs,
        "progressive": True,
        "capped": bool(ids and len(ids) > safe_limit),
    }


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
    from app.domains.kol.video_tracking import VideoTrackingError

    try:
        return kol_url_deep_crawl.enqueue_profile_deep_crawl_job(
            str(body.get("url") or ""),
            kol_pool_id=body.get("kol_pool_id"),
            max_posts=int(body.get("max_posts") or 3),
            staff=staff,
            enforce_target_write=True,
        )
    except VideoTrackingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except (TypeError, ValueError) as exc:
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
            force_refresh=bool(body.get("force_refresh")),
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
    try:
        resp = llm_gateway.invoke(
            prompt,
            purpose="vkpi_kol_outreach_optimize",
            max_output_tokens=1200,
            cost_tag="vkpi_kol_outreach_optimize",
            staff=staff or {},
            metadata={"kol_pool_id": int(body.get("kol_pool_id") or 0)},
        )
    except Exception:  # noqa: BLE001 - return original copy with a stable retryable state
        logger.warning("kol outreach optimize provider failed", exc_info=True)
        return {
            "ok": False,
            "reason": "outreach_provider_unavailable",
            "retryable": True,
            "subject": subject,
            "body": draft,
            "model": "",
        }
    if not isinstance(resp, dict) or str(resp.get("status") or "") != "success" or str(resp.get("provider") or "") in {"", "rule_v0"}:
        return {
            "ok": False,
            "reason": "outreach_provider_unavailable",
            "retryable": True,
            "subject": subject,
            "body": draft,
            "model": "",
        }
    text = str(resp.get("text") or "").strip()
    out_subject, out_body = subject, draft  # 兜底原文
    optimized = False
    try:
        cleaned = _re.sub(r"^```json\s*|```$", "", text, flags=_re.MULTILINE).strip()
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s != -1 and e != -1 and e > s:
            parsed = _json.loads(cleaned[s : e + 1])
            if isinstance(parsed, dict):
                out_subject = str(parsed.get("subject") or subject).strip()
                out_body = str(parsed.get("body") or draft).strip()
                optimized = bool(str(parsed.get("subject") or "").strip() or str(parsed.get("body") or "").strip())
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return {
        "ok": optimized,
        "reason": "" if optimized else "invalid_outreach_response",
        "retryable": not optimized,
        "subject": out_subject,
        "body": out_body,
        "model": resp.get("model") if optimized else "",
    }
