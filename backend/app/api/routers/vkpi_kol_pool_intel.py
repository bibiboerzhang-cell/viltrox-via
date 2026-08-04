"""backend/app/api/routers/vkpi_kol_pool_intel.py

行为不变抽取:vkpi_kol_pool.py 的只读「画像 / 深析 / 内容契合 / 情报卡 / 证据 /
AI Brief / Gemini preflight / bio 翻译」端点簇。本模块自带无 prefix 的 APIRouter,
主 router(prefix=/api/admin/vkpi)include 它,路径逐字不变。

红线:零触 viltrox_fit_score;此簇全只读展示信号,绝不写 fit。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.kol import eleven_dimensions
from app.domains.kol import intelligence_card as kol_intelligence_card
from app.domains.kol import llm_deep_analysis as kol_llm_deep_analysis
import app.domains.intelligence.ai_brief as ai_brief
import app.domains.evidence.summary as evidence_summary
from app.domains.intelligence import gemini_single_kol_preflight

router = APIRouter(tags=["vkpi-kol-pool"])

logger = get_logger(__name__)


def _write_service_error(
    *,
    status_code: int,
    status: str,
    reason: str,
    operation: str,
    kol_pool_id: int | None = None,
) -> HTTPException:
    detail = {
        "status": status,
        "reason": reason,
        "operation": operation,
        "retryable": status_code in {502, 503},
    }
    if kol_pool_id is not None:
        detail["kol_pool_id"] = int(kol_pool_id)
    return HTTPException(status_code=status_code, detail=detail)


def _audience_refresh_contract(result: object, kol_pool_id: int) -> dict:
    if not isinstance(result, dict):
        raise _write_service_error(
            status_code=503,
            status="unavailable",
            reason="invalid_refresh_result",
            operation="audience_refresh",
            kol_pool_id=kol_pool_id,
        )
    upstream_status = str(result.get("status") or "")
    if upstream_status == "network_error":
        raise _write_service_error(
            status_code=502,
            status="upstream_unavailable",
            reason="audience_provider_unavailable",
            operation="audience_refresh",
            kol_pool_id=kol_pool_id,
        )
    if upstream_status == "not_configured":
        raise _write_service_error(
            status_code=503,
            status="unavailable",
            reason="audience_provider_not_configured",
            operation="audience_refresh",
            kol_pool_id=kol_pool_id,
        )
    return result


@router.post("/kol-pool/{kol_pool_id}/contacts")
def add_kol_manual_contact(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """人工保存 KOL 联系方式(ContactModal「保存联系方式」)。合规留痕:source='manual'、
    consent='manual_entry'、is_public_declared=FALSE、记操作人;写 vkpi_kol_pool_contacts 审计表 +
    other_contacts_json 展示快照(并集去重)。纯人工录入零外调。零触 viltrox_fit_score。"""
    from app.domains.kol import business_contact_extract
    from app.domains.kol.contact_access import authorize_plaintext_contacts, project_pool_contact_write

    try:
        result = business_contact_extract.add_manual_contact(
            int(kol_pool_id),
            email=str(body.get("email") or ""),
            platform=str(body.get("platform") or ""),
            handle=str(body.get("handle") or ""),
            staff=staff or {},
        )
        reveal = authorize_plaintext_contacts(
            staff,
            resource_type="kol_pool",
            resource_id=int(kol_pool_id),
            page_path=f"/kol-pool/{int(kol_pool_id)}/contacts",
            metadata={"operation": "write_response"},
        )
        return project_pool_contact_write(result, reveal=reveal)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        correlation_id = uuid.uuid4().hex
        logger.exception(
            "vkpi.kol_contact_save_failed | kol_pool_id=%s correlation_id=%s",
            kol_pool_id,
            correlation_id,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "kol_contact_save_failed",
                "message": "联系方式未保存，请联系管理员并提供错误编号。",
                "retryable": False,
                "correlation_id": correlation_id,
            },
        ) from exc


@router.post("/kol-pool/{kol_pool_id}/audience-stats/refresh")
async def refresh_kol_audience_stats(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """受众画像 ensemble_v1(P0):评论者抽样 -> 三层推断 -> 聚合收缩 -> 写 audience_estimated_json。

    YouTube 走免费 Data API;IG/TT 复用已抓评论(不足则入队抓评论,返回 pending_comments)。
    网络/配置异常诚实返回 {status, reason},不 500。红线:零触 viltrox_fit_score、不碰 rule_v0。
    """
    del staff
    from app.domains.kol import audience_stats

    try:
        # 网络抽样最长可到几十秒,走 threadpool 不阻塞事件循环。
        result = await run_in_threadpool(audience_stats.refresh_audience_stats, int(kol_pool_id))
        return _audience_refresh_contract(result, int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise _write_service_error(
            status_code=503,
            status="unavailable",
            reason="audience_refresh_timeout",
            operation="audience_refresh",
            kol_pool_id=kol_pool_id,
        ) from exc
    except Exception as exc:  # noqa: BLE001 — 估算功能失败不该炸接口,诚实回原因
        logger.warning("vkpi.audience_refresh_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        raise _write_service_error(
            status_code=503,
            status="unavailable",
            reason="audience_refresh_failed",
            operation="audience_refresh",
            kol_pool_id=kol_pool_id,
        ) from exc


@router.get("/kol-pool/{kol_pool_id}/cooperation")
def get_kol_cooperation(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读 KOL 当前合作状态 + 时间线(平台为基准)。"""
    del staff
    from app.domains.kol import cooperation

    return cooperation.get_cooperation(int(kol_pool_id))


def _assert_not_others_claim(staff: dict, kol_pool_id: int) -> None:
    """X4-LOW(2026-07-03):他人认领中的 KOL,非管理层不得改合作状态/生成外联
    (与 my_kol._assert_can_share_kol 归属口径对齐;无人认领或本人认领照常,
    不挡日常发现/外联流)。表缺失等异常按旧行为放行,绝不误伤。"""
    role = str((staff or {}).get("role") or "").strip().lower()
    if int((staff or {}).get("is_owner") or 0) == 1 or role in {
        "admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"
    }:
        return
    try:
        from app.db.connection import get_conn

        row = get_conn().execute(
            """
            SELECT c.staff_id AS sid
            FROM vkpi_kol_pool p
            JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
            WHERE p.id = ?
            LIMIT 1
            """,
            (int(kol_pool_id),),
        ).fetchone()
    except Exception:
        logger.debug("claims 关联读取失败,跳过自动带出(best-effort)", exc_info=True)
        return
    if not row:
        return
    owner = int(dict(row).get("sid") or 0)
    me = int((staff or {}).get("staff_id") or 0)
    if owner and owner != me:
        raise HTTPException(status_code=403, detail="该 KOL 由他人认领中,仅负责人或管理层可执行此操作")


@router.post("/kol-pool/{kol_pool_id}/cooperation")
def record_kol_cooperation(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """KOL 合作动作(续约/加大投入/退出合作/评估/备注)→ 平台为基准的状态时间线。
    action ∈ renew|scale_up|exit|evaluate|note。零触 viltrox_fit_score。"""
    _assert_not_others_claim(staff if isinstance(staff, dict) else {}, kol_pool_id)
    from app.domains.kol import cooperation

    try:
        return cooperation.record_action(
            int(kol_pool_id),
            str(body.get("action") or ""),
            note=str(body.get("note") or ""),
            staff=staff or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/outreach-draft")
def get_kol_outreach_draft(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读最新联系草稿(cache,kol_outreach_draft_v1);无则 state=missing。"""
    del staff
    from app.domains.kol import outreach_draft as kol_outreach_draft

    return kol_outreach_draft.get_outreach_draft(int(kol_pool_id))


@router.get("/kol-pool/{kol_pool_id}/outreach-pack")
def get_kol_outreach_pack(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """C4:读最新外联包(brief + 双语邮件草稿,cache kol_outreach_pack_v1)+ 实时邮箱状态;
    无则 state=missing。零 LLM/零外调,零触 viltrox_fit_score。"""
    from app.domains.kol import outreach_pack as kol_outreach_pack

    try:
        return kol_outreach_pack.get_outreach_pack(int(kol_pool_id), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 - read surface returns an honest unavailable state
        logger.warning("vkpi.outreach_pack_read_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        return {
            "state": "error",
            "status": "unavailable",
            "reason": "outreach_pack_read_failed",
            "retryable": True,
            "kol_pool_id": int(kol_pool_id),
        }


@router.post("/kol-pool/{kol_pool_id}/outreach-pack")
async def generate_kol_outreach_pack(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """C4:一键生成外联包——brief 复用已有 why-fit/内容契合产物(零新分析),邮件草稿走
    llm_gateway(预算闸+兜底链内置,失败回落双语模板),邮箱缺失复用既有富化管线补抓。
    同 KOL 当日幂等(body.force=true 才重生成)。LLM/富化可达数十秒 → threadpool 不阻塞事件循环。
    红线:零写 viltrox_fit_score、不动 rule_v0、不碰归属判定。"""
    _assert_not_others_claim(staff if isinstance(staff, dict) else {}, kol_pool_id)
    from app.domains.kol import outreach_pack as kol_outreach_pack

    staff_dict = staff if isinstance(staff, dict) else None
    try:
        return await run_in_threadpool(
            kol_outreach_pack.generate_outreach_pack,
            int(kol_pool_id),
            force=bool((body or {}).get("force")),
            staff=staff_dict,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 生成失败不该 500 裸炸,诚实回原因供前端展示
        logger.warning("vkpi.outreach_pack_generate_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        return {
            "state": "error",
            "status": "unavailable",
            "reason": "outreach_pack_generation_failed",
            "retryable": True,
            "kol_pool_id": int(kol_pool_id),
        }


@router.get("/kol-pool/{kol_pool_id}/dimensions11")
def get_pool_item_dimensions11(
    kol_pool_id: int,
    require_persisted: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """返回 KOL Pool 项的规则版 11 维画像；只读、不调 provider、不写库。"""
    try:
        if require_persisted:
            payload = eleven_dimensions.load_persisted_dimensions_11(int(kol_pool_id))
            if payload:
                return payload
            return {
                "kol_pool_id": int(kol_pool_id),
                "status": "missing",
                "reason": "dimensions_11_json_missing",
                "persisted": False,
                "provider_calls": False,
                "llm_calls": False,
                "write_db": False,
            }
        return eleven_dimensions.compose_dimensions_11(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/llm-deep-analysis")
def get_pool_item_llm_deep_analysis(
    kol_pool_id: int,
    limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return independent LLM deep-analysis results; never touches V6 Fit."""
    del staff
    try:
        result = kol_llm_deep_analysis.get_kol_llm_deep_analysis(int(kol_pool_id), limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - this is a read surface; return an honest empty state
        logger.warning("vkpi.llm_deep_analysis_read_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        return {
            "status": "unavailable",
            "reason": "deep_analysis_read_failed",
            "retryable": True,
            "kol_pool_id": int(kol_pool_id),
            "summary": {"count": 0, "llm_calls": False, "write_db": False},
            "primary_result": None,
            "items": [],
            "count": 0,
        }
    if not isinstance(result, dict):
        return {
            "status": "unavailable",
            "reason": "invalid_deep_analysis_result",
            "retryable": True,
            "kol_pool_id": int(kol_pool_id),
            "summary": {"count": 0, "llm_calls": False, "write_db": False},
            "primary_result": None,
            "items": [],
            "count": 0,
        }
    return result


def _enqueue_content_fit_on_demand(kol_pool_id: int, product_sku, *, force: bool, staff) -> dict:
    """L1:内容契合按需入队逻辑已迁 domains/kol/content_fit_enqueue;此处薄委托(端点调用不变,行为一致)。"""
    from app.core.release_validation import release_validation_active
    from app.domains.kol import content_fit_enqueue

    if release_validation_active():
        raise RuntimeError("release validation fence blocks content-fit enqueue")
    return content_fit_enqueue.enqueue_content_fit_on_demand(kol_pool_id, product_sku, force=force, staff=staff)


@router.get("/kol-pool/{kol_pool_id}/content-fit")
async def get_pool_item_content_fit(
    kol_pool_id: int,
    analyze: bool = Query(default=False),
    force: bool = Query(default=False),
    product_sku: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """地基B 内容契合深析(content_fit_v1):读该 KOL 视频画面/故事 + 评论的契合判断。

    默认只读已缓存结果(不烧 LLM)。analyze=true / force=true 不再在请求内同步烧 LLM
    (那是 60s 超时的根源):若已有 ready cache 直接返回;否则把深析**入队**(worker 端
    跑 LLM)并立即返回 {status:'queued', job_id}。红线:零触 viltrox_fit_score,不新跑
    Gemini 视频分析;无视频证据由 worker 落 status='insufficient_evidence'(诚实不杜撰)。
    """
    from app.domains.kol import content_fit_analysis as kol_content_fit

    staff_dict = staff if isinstance(staff, dict) else None
    try:
        if analyze or force:
            # 重活移出请求路径:仅入队 + 立即返回(DB 路径走 threadpool,不阻塞事件循环)。
            return await run_in_threadpool(
                _enqueue_content_fit_on_demand,
                int(kol_pool_id),
                product_sku,
                force=bool(force),
                staff=staff_dict,
            )
        return await run_in_threadpool(
            kol_content_fit.get_content_fit,
            int(kol_pool_id),
            product_sku,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("vkpi.content_fit_endpoint_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        if analyze or force:
            raise _write_service_error(
                status_code=503,
                status="unavailable",
                reason="content_fit_enqueue_failed",
                operation="content_fit_generate",
                kol_pool_id=kol_pool_id,
            ) from exc
        return {
            "status": "unavailable",
            "reason": "content_fit_read_failed",
            "retryable": True,
            "kol_pool_id": int(kol_pool_id),
            "result": None,
        }


@router.get("/kol-pool/{kol_pool_id}/intelligence-card")
def get_pool_item_intelligence_card(
    kol_pool_id: int,
    include_product_fit: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return one read-only P2 KOL decision card from existing evidence."""
    del staff
    try:
        return kol_intelligence_card.build_kol_pool_intelligence_card(
            int(kol_pool_id),
            include_product_fit=bool(include_product_fit),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/videos")
def list_kol_pool_videos(
    kol_pool_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """C4-full:MY KOL 库内容层(Pool 收藏行)读该 KOL 全部 evidence 视频(只读)。"""
    from app.domains.kol.pool import _video_evidence_for_kol

    items = _video_evidence_for_kol(int(kol_pool_id), limit=limit)
    return {"items": items, "total": len(items), "kol_pool_id": int(kol_pool_id)}


@router.get("/kol-pool/{kol_pool_id}/competitor-exposure")
def get_pool_item_competitor_exposure(
    kol_pool_id: int,
    force: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """#51 百家饭指数(竞品露出率):聚合该 KOL 已深析(final_v1)evidence 的品牌提及 →
    Viltrox vs 竞品露出比 + 专情指数(0-100,带样本量置信折扣)。纯读已有深析产物,
    零新分析/零 LLM;结果当日缓存(vkpi_analysis_cache),force=true 才重算。
    红线:零触 viltrox_fit_score、不动 rule_v0、不碰 KOL 归属判定。"""
    del staff
    from app.domains.kol import competitor_exposure

    try:
        return competitor_exposure.get_competitor_exposure(int(kol_pool_id), force=bool(force))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 聚合失败不该 500 裸炸,诚实回原因供前端展示
        logger.warning("vkpi.competitor_exposure_read_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        return {"status": "error", "reason": "competitor_exposure_read_failed", "kol_pool_id": int(kol_pool_id)}


@router.get("/kol-pool/{kol_pool_id}/evidence-summary")
def get_pool_item_evidence_summary(
    kol_pool_id: int,
    include_product_fit: bool = Query(default=True),
    ref_limit: int = Query(default=8, ge=1, le=25),
    include_llm_preflight: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return traceable summaries derived only from existing IntelligenceCard evidence."""
    del staff
    try:
        return evidence_summary.build_kol_pool_evidence_summary(
            int(kol_pool_id),
            include_product_fit=bool(include_product_fit),
            ref_limit=int(ref_limit),
            include_llm_preflight=bool(include_llm_preflight),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/ai-brief")
def get_pool_item_ai_brief(
    kol_pool_id: int,
    include_product_fit: bool = Query(default=True),
    ref_limit: int = Query(default=8, ge=1, le=25),
    max_items: int = Query(default=8, ge=1, le=12),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only AI Brief v0 from existing evidence refs only."""
    del staff
    try:
        return ai_brief.build_kol_pool_ai_brief(
            int(kol_pool_id),
            include_product_fit=bool(include_product_fit),
            ref_limit=int(ref_limit),
            max_items=int(max_items),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/gemini-preflight")
def get_pool_item_gemini_preflight(
    kol_pool_id: int,
    candidate_limit: int = Query(default=24, ge=1, le=100),
    include_budget_preflight: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return P4.55 Gemini readiness from cached evidence only; no provider call."""
    del staff
    try:
        return gemini_single_kol_preflight.build_kol_pool_gemini_preflight(
            int(kol_pool_id),
            candidate_limit=int(candidate_limit),
            include_budget_preflight=bool(include_budget_preflight),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/gemini-go-no-go")
def get_pool_item_gemini_go_no_go(
    kol_pool_id: int,
    candidate_limit: int = Query(default=24, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return P4.56 Gemini go/no-go report; read-only and no provider call."""
    del staff
    try:
        return gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(
            int(kol_pool_id),
            candidate_limit=int(candidate_limit),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool-dimensions11/preview")
def get_pool_dimensions11_preview(
    limit: int = Query(default=20, ge=1, le=200),
    source_type: str = Query(default="legacy_excel_p2d"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """批量预览规则版 11 维画像；只读、不调 provider、不写库。"""
    del staff
    return eleven_dimensions.batch_preview_dimensions11(limit=limit, source_type=source_type)


_BIO_ZH_CACHE: dict[str, str] = {}


@router.post("/kol-pool/translate-bio")
def translate_bio(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """#25 发现卡英文 bio → 简体中文(gpt-4o-mini,预算闸由 llm_gateway 内置)。

    进程内按原文缓存:同一 bio 第二次命中不再烧 LLM。失败/空/预算挡 → 诚实返回空译文(前端回退原文)。
    """
    text = str((body or {}).get("text") or "").strip()
    if not text:
        return {"status": "skipped", "reason": "empty_input", "translated": "", "lang": "zh", "cached": False}
    if len(text) > 1200:
        text = text[:1200]
    if text in _BIO_ZH_CACHE:
        return {"status": "ready", "translated": _BIO_ZH_CACHE[text], "lang": "zh", "cached": True}
    try:
        from app.platform import llm_gateway

        prompt = (
            "Translate the following social-media creator bio into natural Simplified Chinese. "
            "Keep @handles, brand names and URLs as-is. Return ONLY the translation, "
            "no quotes, no explanation:\n\n" + text
        )
        resp = llm_gateway.invoke(
            prompt=prompt,
            purpose="vkpi_bio_translate",
            preferred_provider="openai",
            max_output_tokens=400,
        )
        if str(resp.get("status") or "") == "success":
            out = str(resp.get("text") or "").strip().strip('"').strip("'").strip()
            if out:
                _BIO_ZH_CACHE[text] = out
                return {"status": "ready", "translated": out, "lang": "zh", "cached": False}
        provider_reason = str(resp.get("reason") or resp.get("status") or "").strip().lower()
        if "timeout" in provider_reason or "deadline" in provider_reason:
            safe_reason = "provider_timeout"
        elif provider_reason in {"all_providers_failed", "budget_exceeded", "not_configured", "provider_unavailable"}:
            safe_reason = provider_reason
        else:
            safe_reason = "provider_unavailable"
        return {
            "status": "partial",
            "reason": safe_reason,
            "translated": "",
            "lang": "zh",
            "cached": False,
        }
    except Exception as exc:  # noqa: BLE001 — 翻译失败不阻断,前端回退原文
        logger.warning("vkpi.bio_translate_failed", exc_info=True)
        return {
            "status": "partial",
            "reason": "provider_exception",
            "translated": "",
            "lang": "zh",
            "cached": False,
        }


@router.post("/kol-pool/{kol_pool_id}/build-full-profile")
def build_full_profile_endpoint(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
):
    """一键补全档案:强制 full 档点火(深爬 3 帖 + 评论采集;深析/受众/契合链自动跟进)。
    幂等(下游入队各自去重),约 3-5 分钟数据陆续点亮,抽屉既有轮询自动接住。零触 fit。"""
    del staff
    from app.domains.discovery.buildout import build_full_profile

    try:
        result = build_full_profile(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("vkpi.build_full_profile_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        raise _write_service_error(
            status_code=503,
            status="unavailable",
            reason="profile_build_enqueue_failed",
            operation="build_full_profile",
            kol_pool_id=kol_pool_id,
        ) from exc
    if not isinstance(result, dict):
        raise _write_service_error(
            status_code=503,
            status="unavailable",
            reason="invalid_profile_build_result",
            operation="build_full_profile",
            kol_pool_id=kol_pool_id,
        )
    out = dict(result)
    tier = str(out.get("tier") or "")
    if tier in {"full", "light"}:
        out.setdefault("status", "queued")
    elif str(out.get("reason") or "") == "error":
        out["status"] = "partial"
        out["reason"] = "profile_build_enqueue_partial"
    else:
        out.setdefault("status", "skipped")
    return out
