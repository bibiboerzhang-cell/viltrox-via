"""
deepsight.py — VIA DeepSight 高级快速响应架构

包含：
- 神经大脑架构（orchestrator）
- 三脑会议（triad）
- 情感层（emotional layer）
- 规则引擎（rules）
- Evidence Pack
- 预计算 / 缓存
- 数据分流（official vs ugc）
- Tier 0/1/2 分层处理
- 并行扫描
- 评论 NLP
- 竞品雷达
- 视觉生活评分
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.dependencies.admin import get_admin
from app.schemas.deepsight import DeepSightRequest
from app.services.security.rate_limiter import rate_limit
from app.services.deepsight.cache import cache
from app.services.deepsight.orchestrator import analyze_request
from app.services.deepsight.parallel_scan import scan_accounts_concurrently
from app.services.deepsight.constants import OFFICIAL_MATRIX

router = APIRouter(prefix="/api/admin/deepsight", tags=["deepsight"])


@router.get("/health")
async def deepsight_health(request: Request):
    get_admin(request)
    return {
        "status": "ok",
        "modules": [
            "neural_brain",
            "triad_council",
            "emotional_layer",
            "rules_engine",
            "evidence_pack",
            "cache",
            "segregation",
            "tiering",
            "parallel_scan",
            "comment_nlp",
            "competitor_radar",
            "visual_life_scoring",
        ],
        "cache": cache.stats(),
    }


@router.post("/evidence-pack")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def build_pack(request_http: Request, body: DeepSightRequest):
    get_admin(request_http)
    result = await analyze_request(body.model_copy(update={"model_mode": "fast", "refresh": body.refresh}))
    return {
        "status": "success",
        "tier": result["tier"],
        "cache_hit": result["cache_hit"],
        "generated_at": result["generated_at"],
        "elapsed_sec": result["elapsed_sec"],
        "evidence_pack": result["evidence_pack"],
    }


@router.post("/diagnose")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def diagnose(request_http: Request, body: DeepSightRequest):
    get_admin(request_http)
    return await analyze_request(body)


@router.post("/scan-official-matrix")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def scan_official_matrix(request_http: Request, body: dict | None = None):
    get_admin(request_http)
    body = body or {}
    accounts = body.get("accounts") or OFFICIAL_MATRIX
    max_posts = int(body.get("max_posts_per_account", 60))
    concurrency = int(body.get("concurrency", 4))
    return await scan_accounts_concurrently(accounts=accounts, max_posts_per_account=max_posts, concurrency=concurrency)


@router.get("/cache/stats")
async def cache_stats(request_http: Request):
    get_admin(request_http)
    return {"status": "success", "cache": cache.stats()}


@router.post("/cache/clear")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def cache_clear(request_http: Request):
    get_admin(request_http)
    cleared = cache.clear()
    return {"status": "success", "cleared": cleared}
