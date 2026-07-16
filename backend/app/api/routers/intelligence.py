"""
api/routers/intelligence.py — Admin Intelligence API
======================================================
B&H 商品数据 + 系统状态 + Cache/RateLimit 监控
"""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException, Query

from app.core.logging import get_logger
from app.core.security import require_admin, require_admin_async
from app.services.ingestion.pipeline import build_platform_job_type
from app.services.ingestion.research import detect_learning_source
from app.services.intelligence.dashboard import build_bh_dashboard
from app.services.intelligence import (
    build_viltrox_overview,
    get_latest_bh_products,
    get_bh_summary,
    get_bh_price_history,
    get_bh_top_rated,
    reset_viltrox_official_roster,
    scan_viltrox_official_matrix_now,
)
from app.services.cache import cache_clear, cached
from app.services.memory import (
    advance_via_live_policy_rollout_guarded,
    get_via_control_debug_snapshot,
    get_via_memory_retention_snapshot,
    get_via_retrieval_evidence_snapshot,
    get_via_rollout_alert_snapshot,
    get_via_routing_learner_snapshot,
    list_via_live_rollout_health,
    list_via_shadow_rollout_readiness,
    promote_via_policy_version_guarded,
    run_via_offline_evaluator,
)
from app.db.repositories.via_control import (
    apply_via_policy_proposal,
    list_active_via_policy_versions,
    list_via_policy_proposals,
    list_via_policy_version_history,
    review_via_policy_proposal,
    rollback_via_policy_version,
    stage_via_policy_proposal,
)
from app.services.student_identity import (
    build_student_batch_detail,
    build_student_detail,
    build_student_funnel_snapshot,
    build_student_overview,
    build_student_roster,
    create_student_qr_batch,
    ensure_student_school_defaults,
    list_student_schools_with_stats,
    create_or_update_school,
    reissue_student_qr,
    revoke_student_qr,
)
from app.services.security.rate_limiter import rate_limit
from app.api.routers.intelligence_system import router as system_router


router = APIRouter(prefix="/api/admin/intel", tags=["intelligence"])
router.include_router(system_router)
logger = get_logger(__name__)


@cached(ttl=120, key="intel:dashboard")
def _cached_bh_dashboard():
    return build_bh_dashboard()


@cached(ttl=120, key="intel:bh_summary")
def _cached_bh_summary():
    return get_bh_summary()


@cached(ttl=120)
def _cached_bh_products(limit: int):
    return {
        "products": get_latest_bh_products(limit=limit),
        "summary": get_bh_summary(),
    }


@cached(ttl=120)
def _cached_bh_top_rated(limit: int):
    return {
        "top_rated": get_bh_top_rated(limit=limit),
    }


@cached(ttl=30)
async def _cached_via_control_overview(days: int, limit: int):
    return await get_via_control_debug_snapshot(window_days=days, limit=limit)


@cached(ttl=30)
async def _cached_via_shadow_readiness(days: int, limit: int, policy_key: str, version_key: str):
    return {
        "items": await list_via_shadow_rollout_readiness(
            window_days=days,
            limit=limit,
            policy_key=policy_key,
            version_key=version_key,
        )
    }


@cached(ttl=30)
async def _cached_via_live_rollout_health(days: int, limit: int, policy_key: str, version_key: str):
    return {
        "items": await list_via_live_rollout_health(
            window_days=days,
            limit=limit,
            policy_key=policy_key,
            version_key=version_key,
        )
    }


@cached(ttl=30)
async def _cached_via_rollout_alerts(limit: int, policy_key: str, version_key: str, status: str):
    return await get_via_rollout_alert_snapshot(
        limit=limit,
        policy_key=policy_key,
        version_key=version_key,
        status=status,
    )


@cached(ttl=30)
async def _cached_via_retrieval_evidence(days: int, limit: int, policy_key: str):
    return await get_via_retrieval_evidence_snapshot(
        window_days=days,
        limit=limit,
        policy_key=policy_key,
    )


@cached(ttl=30)
async def _cached_via_routing_learner(days: int, limit: int, bucket_key: str, target: str):
    return await get_via_routing_learner_snapshot(
        window_days=days,
        limit=limit,
        bucket_key=bucket_key,
        target=target,
    )


@cached(ttl=30)
async def _cached_via_memory_retention(days: int, limit: int, memory_tier: str, status: str):
    return await get_via_memory_retention_snapshot(
        window_days=days,
        limit=limit,
        memory_tier=memory_tier,
        status=status,
    )


@cached(ttl=45)
def _cached_student_overview(limit: int):
    ensure_student_school_defaults()
    return build_student_overview(limit=limit)


@cached(ttl=45)
def _cached_student_funnel_snapshot(limit: int):
    ensure_student_school_defaults()
    return build_student_funnel_snapshot(limit=limit)


@cached(ttl=60, key="intel:student:schools")
def _cached_student_schools():
    ensure_student_school_defaults()
    return {"items": list_student_schools_with_stats()}


@cached(ttl=60, key="intel:viltrox:overview")
def _cached_viltrox_overview():
    return build_viltrox_overview()


def _clear_student_admin_cache() -> None:
    cache_clear(prefix="app.api.routers.intelligence._cached_student_")
    cache_clear(prefix="intel:student:")


def _clear_via_admin_cache() -> None:
    cache_clear(prefix="app.api.routers.intelligence._cached_via_")


def _clear_viltrox_admin_cache() -> None:
    cache_clear(prefix="intel:")
    cache_clear(prefix="intel:viltrox:")


# ══════════════════════════════════════════════
# B&H 商品监控
# ══════════════════════════════════════════════

@router.get("/dashboard")
def intel_dashboard(request: Request):
    """完整情报大盘数据 (给 admin.html Intelligence tab 用)"""
    require_admin(request)
    return _cached_bh_dashboard()


@router.get("/bh/summary")
def bh_summary(request: Request):
    """B&H 总览数字"""
    require_admin(request)
    return _cached_bh_summary()


@router.get("/bh/products")
def bh_products(request: Request, limit: int = Query(default=50, le=200)):
    """最新一次 snapshot 的所有产品"""
    require_admin(request)
    return _cached_bh_products(limit)


@router.get("/bh/top-rated")
def bh_top_rated(request: Request, limit: int = Query(default=10, le=50)):
    """评分最高的产品"""
    require_admin(request)
    return _cached_bh_top_rated(limit)


@router.get("/bh/price-history")
def bh_price_history(
    request: Request,
    sku: str = Query(default=""),
    title: str = Query(default=""),
    days: int = Query(default=30, le=365),
):
    """价格历史 (跨 snapshot)"""
    require_admin(request)
    if not sku and not title:
        raise HTTPException(400, "Provide sku or title")
    
    return {
        "history": get_bh_price_history(sku=sku, title_like=title, days=days),
    }


@router.post("/monitor")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def intel_monitor(request: Request, body: dict):
    """
    单镜头市场监控.
    
    Input:
        { "query": "viltrox 16mm f1.8", "max_videos": 30 }
    
    Returns: 6 分类 + 时间分布 + Claude 洞察
    """
    await require_admin_async(request)
    
    query = (body.get("query") or "").strip()
    max_videos = int(body.get("max_videos", 30))
    
    if not query:
        raise HTTPException(400, "query is required")
    
    if max_videos > 50:
        max_videos = 50
    
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(503, "durable job queue unavailable")
    payload = {
        "query": query,
        "max_videos": max_videos,
        "platform": (body.get("platform") or "youtube").strip().lower(),
        "market": (body.get("market") or body.get("region_code") or "").strip().upper(),
        "date_from": (body.get("date_from") or "").strip(),
        "date_to": (body.get("date_to") or "").strip(),
    }
    task_id = await queue.enqueue(
        "intel_lens_monitor",
        payload,
        lock_key=f"intel_lens_monitor:{payload['platform']}:{query.lower()}",
        timeout_seconds=1200,
    )
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.post("/compare")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def intel_compare(request: Request, body: dict):
    """
    对比两个镜头的 YouTube 市场表现.
    
    Input:
        { "lens_a": "viltrox 16mm", "lens_b": "sigma 16mm f1.4", "max_videos": 15 }
    
    Returns: 完整对比 + Claude 洞察
    """
    await require_admin_async(request)
    
    lens_a = (body.get("lens_a") or "").strip()
    lens_b = (body.get("lens_b") or "").strip()
    max_videos = int(body.get("max_videos", 15))
    
    if not lens_a or not lens_b:
        raise HTTPException(400, "lens_a and lens_b required")
    
    if max_videos > 30:
        max_videos = 30
    
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(503, "durable job queue unavailable")
    payload = {
        "lens_a": lens_a,
        "lens_b": lens_b,
        "max_videos": max_videos,
        "platform": (body.get("platform") or "youtube").strip().lower(),
        "market": (body.get("market") or body.get("region_code") or "").strip().upper(),
        "date_from": (body.get("date_from") or "").strip(),
        "date_to": (body.get("date_to") or "").strip(),
    }
    task_id = await queue.enqueue(
        "intel_lens_compare",
        payload,
        lock_key=f"intel_lens_compare:{payload['platform']}:{lens_a.lower()}:{lens_b.lower()}",
        timeout_seconds=1200,
    )
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.post("/bh/refresh-now")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def bh_refresh_now(request: Request, max_items: int = 500):
    """
    Admin 立刻触发一次 B&H 抓取.
    费 ~30 秒, 1 次 Apify call.
    """
    await require_admin_async(request)
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(503, "durable job queue unavailable")
    logger.info("intel.bh_refresh_queued", extra={"max_items": max_items})
    task_id = await queue.enqueue(
        "intel_bh_refresh",
        {"max_items": max(1, min(1000, int(max_items or 100)))},
        lock_key="intel_bh_refresh:catalog",
        timeout_seconds=1800,
    )
    return {"ok": True, "status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.post("/learn/url")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def learn_from_url(request: Request, body: dict):
    """
    把外部 URL 作为市场学习样本入队。
    走本地联网抓取，不强依赖 Apify。
    """
    await require_admin_async(request)
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")

    source_platform = detect_learning_source(url, body.get("source_platform") or "")
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(503, "job queue unavailable")

    payload = {
        "source_url": url,
        "external_id": url,
        "event_type": (body.get("event_type") or "").strip().lower() or ("community_signal" if source_platform == "reddit" else "market_scan"),
        "entity_type": (body.get("entity_type") or "").strip().lower() or ("discussion" if source_platform == "reddit" else "content"),
        "region_code": (body.get("region_code") or "").strip().upper(),
        "submission_id": int(body.get("submission_id") or 0),
        "user_id": int(body.get("user_id") or 0),
        "creator_handle": (body.get("creator_handle") or "").strip(),
        "autonomous_fetch": True,
        "note": (body.get("note") or "").strip(),
    }
    task_id = await queue.enqueue(
        build_platform_job_type(source_platform),
        payload,
        submission_id=payload["submission_id"] or None,
    )
    return {
        "status": "queued",
        "job_id": task_id,
        "source_platform": source_platform,
        "url": url,
    }


@router.post("/via/learn-now")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def via_learn_now(request: Request):
    """手动触发 Via 官方渠道 + B&H 日常学习"""
    await require_admin_async(request)
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(503, "durable job queue unavailable")
    task_id = await queue.enqueue(
        "intel_via_learning",
        {},
        lock_key="intel_via_learning:daily",
        timeout_seconds=3600,
    )
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.get("/via/control-overview")
async def via_control_overview(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=24, ge=8, le=80),
):
    """Decision/Outcome ledger debug snapshot for the admin panel."""
    await require_admin_async(request)
    return await _cached_via_control_overview(days, limit)


@router.post("/via/evaluate-now")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def via_evaluate_now(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=240, ge=40, le=1200),
):
    """Run the offline evaluator and persist fresh policy proposals."""
    await require_admin_async(request)
    return await run_via_offline_evaluator(window_days=days, limit=limit)


@router.get("/via/proposals")
def via_proposals(
    request: Request,
    status: str = Query(default=""),
    policy_key: str = Query(default=""),
    audit_actor: str = Query(default=""),
    limit: int = Query(default=60, ge=1, le=200),
):
    require_admin(request)
    return {
        "items": list_via_policy_proposals(
            limit=limit,
            status=status,
            policy_key=policy_key,
            audit_actor=audit_actor,
        )
    }


@router.post("/via/proposals/{proposal_key}/approve")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def via_proposal_approve(request: Request, proposal_key: str, body: dict | None = None):
    admin_user = await require_admin_async(request)
    payload = dict(body or {})
    actor = str(admin_user.get("email") or admin_user.get("name") or "admin")
    result = review_via_policy_proposal(proposal_key, action="approve", actor=actor, note=str(payload.get("note") or ""))
    _clear_via_admin_cache()
    return result


@router.post("/via/proposals/{proposal_key}/reject")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def via_proposal_reject(request: Request, proposal_key: str, body: dict | None = None):
    admin_user = await require_admin_async(request)
    payload = dict(body or {})
    actor = str(admin_user.get("email") or admin_user.get("name") or "admin")
    result = review_via_policy_proposal(proposal_key, action="reject", actor=actor, note=str(payload.get("note") or ""))
    _clear_via_admin_cache()
    return result


@router.post("/via/proposals/{proposal_key}/apply")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def via_proposal_apply(request: Request, proposal_key: str, body: dict | None = None):
    admin_user = await require_admin_async(request)
    payload = dict(body or {})
    actor = str(admin_user.get("email") or admin_user.get("name") or "admin")
    result = apply_via_policy_proposal(proposal_key, actor=actor, note=str(payload.get("note") or ""))
    _clear_via_admin_cache()
    return result


@router.post("/via/proposals/{proposal_key}/stage")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def via_proposal_stage(request: Request, proposal_key: str, body: dict | None = None):
    admin_user = await require_admin_async(request)
    payload = dict(body or {})
    actor = str(admin_user.get("email") or admin_user.get("name") or "admin")
    result = stage_via_policy_proposal(proposal_key, actor=actor, note=str(payload.get("note") or ""))
    _clear_via_admin_cache()
    return result


@router.post("/via/policies/{version_key}/promote")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def via_policy_promote(request: Request, version_key: str, body: dict | None = None):
    admin_user = await require_admin_async(request)
    payload = dict(body or {})
    actor = str(admin_user.get("email") or admin_user.get("name") or "admin")
    result = await promote_via_policy_version_guarded(
        version_key,
        actor=actor,
        note=str(payload.get("note") or ""),
        window_days=int(payload.get("window_days") or 14),
        limit=int(payload.get("limit") or 300),
        force=bool(payload.get("force")),
    )
    _clear_via_admin_cache()
    return result


@router.post("/via/policies/{version_key}/rollback")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def via_policy_rollback(request: Request, version_key: str, body: dict | None = None):
    admin_user = await require_admin_async(request)
    payload = dict(body or {})
    actor = str(admin_user.get("email") or admin_user.get("name") or "admin")
    result = rollback_via_policy_version(version_key, actor=actor, note=str(payload.get("note") or ""))
    _clear_via_admin_cache()
    return result


@router.post("/via/policies/{version_key}/advance-rollout")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def via_policy_advance_rollout(request: Request, version_key: str, body: dict | None = None):
    admin_user = await require_admin_async(request)
    payload = dict(body or {})
    actor = str(admin_user.get("email") or admin_user.get("name") or "admin")
    result = await advance_via_live_policy_rollout_guarded(
        version_key,
        actor=actor,
        note=str(payload.get("note") or ""),
        window_days=int(payload.get("window_days") or 14),
        limit=int(payload.get("limit") or 300),
        force=bool(payload.get("force")),
    )
    _clear_via_admin_cache()
    return result


@router.get("/via/policies/live")
def via_policy_live(request: Request):
    require_admin(request)
    return {"items": list_active_via_policy_versions()}


@router.get("/via/policies/shadow-readiness")
async def via_policy_shadow_readiness(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=300, ge=40, le=1200),
    policy_key: str = Query(default=""),
    version_key: str = Query(default=""),
):
    await require_admin_async(request)
    return await _cached_via_shadow_readiness(days, limit, policy_key, version_key)


@router.get("/via/policies/live-rollout-health")
async def via_policy_live_rollout_health(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=300, ge=40, le=1200),
    policy_key: str = Query(default=""),
    version_key: str = Query(default=""),
):
    await require_admin_async(request)
    return await _cached_via_live_rollout_health(days, limit, policy_key, version_key)


@router.get("/via/policies/rollout-alerts")
async def via_policy_rollout_alerts(
    request: Request,
    limit: int = Query(default=80, ge=8, le=240),
    policy_key: str = Query(default=""),
    version_key: str = Query(default=""),
    status: str = Query(default=""),
):
    await require_admin_async(request)
    return await _cached_via_rollout_alerts(limit, policy_key, version_key, status)


@router.get("/via/retrieval-evidence")
async def via_retrieval_evidence(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=120, ge=8, le=400),
    policy_key: str = Query(default=""),
):
    await require_admin_async(request)
    return await _cached_via_retrieval_evidence(days, limit, policy_key)


@router.get("/via/routing-learner")
async def via_routing_learner(
    request: Request,
    days: int = Query(default=21, ge=1, le=90),
    limit: int = Query(default=120, ge=8, le=400),
    bucket_key: str = Query(default=""),
    target: str = Query(default=""),
):
    await require_admin_async(request)
    return await _cached_via_routing_learner(days, limit, bucket_key, target)


@router.get("/via/memory-retention")
async def via_memory_retention(
    request: Request,
    days: int = Query(default=45, ge=1, le=180),
    limit: int = Query(default=120, ge=8, le=400),
    memory_tier: str = Query(default=""),
    status: str = Query(default=""),
):
    await require_admin_async(request)
    return await _cached_via_memory_retention(days, limit, memory_tier, status)


@router.get("/via/policies/history")
def via_policy_history(
    request: Request,
    policy_key: str = Query(default=""),
    status: str = Query(default=""),
    audit_actor: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=240),
):
    require_admin(request)
    return {
        "items": list_via_policy_version_history(
            limit=limit,
            policy_key=policy_key,
            status=status,
            audit_actor=audit_actor,
        )
    }


@router.get("/student/overview")
def student_identity_overview(request: Request, limit: int = Query(default=48, ge=8, le=240)):
    require_admin(request)
    return _cached_student_overview(limit)


@router.get("/student/funnels")
def student_identity_funnels(request: Request, limit: int = Query(default=48, ge=8, le=240)):
    require_admin(request)
    return _cached_student_funnel_snapshot(limit)


@router.get("/student/schools")
def student_schools(request: Request):
    require_admin(request)
    return _cached_student_schools()


@router.post("/student/schools")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def student_save_school(request: Request, body: dict | None = None):
    await require_admin_async(request)
    payload = dict(body or {})
    school_id = str(payload.get("school_id") or "").strip()
    if not school_id:
        raise HTTPException(400, "school_id is required")
    result = create_or_update_school(
        school_id=school_id,
        school_code=str(payload.get("school_code") or school_id.split("_", 1)[0] or "SCH"),
        school_name=str(payload.get("school_name") or school_id),
        school_name_native=str(payload.get("school_name_native") or ""),
        country=str(payload.get("country") or ""),
        region=str(payload.get("region") or ""),
        school_type=str(payload.get("school_type") or "film"),
        tier=str(payload.get("tier") or "standard"),
        partnership_status=str(payload.get("partnership_status") or "pilot"),
        visual_theme=payload.get("visual_theme") or {},
        metadata=payload.get("metadata") or {},
    )
    _clear_student_admin_cache()
    return result


@router.post("/student/batches")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def student_create_batch(request: Request, body: dict | None = None):
    await require_admin_async(request)
    payload = dict(body or {})
    school_id = str(payload.get("school_id") or "").strip()
    batch_name = str(payload.get("batch_name") or "").strip()
    if not school_id or not batch_name:
        raise HTTPException(400, "school_id and batch_name are required")
    result = create_student_qr_batch(
        school_id=school_id,
        batch_name=batch_name,
        count=int(payload.get("count") or 0),
        roster_csv=str(payload.get("roster_csv") or ""),
        expires_days=int(payload.get("expires_days") or (365 * 4)),
        qr_only=bool(payload.get("qr_only", True)),
    )
    _clear_student_admin_cache()
    return result


@router.get("/student/batches/detail")
def student_batch_detail(
    request: Request,
    school_id: str = Query(default=""),
    batch_name: str = Query(default=""),
    limit: int = Query(default=240, ge=1, le=500),
):
    require_admin(request)
    if not school_id or not batch_name:
        raise HTTPException(400, "school_id and batch_name are required")
    return build_student_batch_detail(school_id=school_id, batch_name=batch_name, limit=limit)


@router.get("/student/roster")
def student_roster_view(
    request: Request,
    school_id: str = Query(default=""),
    batch_name: str = Query(default=""),
    limit: int = Query(default=160, ge=1, le=400),
):
    require_admin(request)
    items = build_student_roster(limit=limit)
    if school_id:
        items = [item for item in items if str(item.get("school_id") or "") == str(school_id or "")]
    return {"items": items}


@router.get("/student/detail/{user_id}")
def student_detail(request: Request, user_id: int):
    require_admin(request)
    try:
        return build_student_detail(user_id)
    except ValueError as exc:
        # 无此学生/用户 → 404,不让 not-found 冒 500(与 KOL/settings/group 端点同口径)。
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/student/cards/{qr_id}/revoke")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def student_revoke_card(request: Request, qr_id: str, body: dict | None = None):
    await require_admin_async(request)
    payload = dict(body or {})
    result = revoke_student_qr(qr_id, reason=str(payload.get("reason") or "revoked_by_admin"))
    _clear_student_admin_cache()
    return result


@router.post("/student/cards/{qr_id}/reissue")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def student_reissue_card(request: Request, qr_id: str):
    await require_admin_async(request)
    result = reissue_student_qr(qr_id)
    _clear_student_admin_cache()
    return result


@router.get("/viltrox/overview")
def viltrox_overview(request: Request):
    """Legacy admin Viltrox zone overview backed by persisted backend state."""
    require_admin(request)
    return _cached_viltrox_overview()


@router.post("/viltrox/scan-now")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
async def viltrox_scan_now(request: Request, body: dict | None = None):
    """Run a fresh full scan for the backend-managed official Viltrox matrix."""
    await require_admin_async(request)
    payload = dict(body or {})
    max_posts_per_account = int(payload.get("max_posts_per_account", 120) or 120)
    concurrency = int(payload.get("concurrency", 4) or 4)
    result = await scan_viltrox_official_matrix_now(
        max_posts_per_account=max_posts_per_account,
        concurrency=concurrency,
    )
    _clear_viltrox_admin_cache()
    return result


@router.post("/viltrox/reset-official-roster")
@rate_limit("admin_mutation", max_requests=10, window_sec=300)
def viltrox_reset_official_roster(request: Request):
    """Restore the official matrix roster from the server-side source of truth."""
    require_admin(request)
    result = reset_viltrox_official_roster()
    _clear_viltrox_admin_cache()
    return result
