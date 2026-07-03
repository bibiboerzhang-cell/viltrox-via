"""backend/app/api/routers/vkpi_kol_pool_intel.py

行为不变抽取:vkpi_kol_pool.py 的只读「画像 / 深析 / 内容契合 / 情报卡 / 证据 /
AI Brief / Gemini preflight / bio 翻译」端点簇。本模块自带无 prefix 的 APIRouter,
主 router(prefix=/api/admin/vkpi)include 它,路径逐字不变。

红线:零触 viltrox_fit_score;此簇全只读展示信号,绝不写 fit。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.api.dependencies.perms import require_tab
from app.domains.kol import eleven_dimensions
from app.domains.kol import intelligence_card as kol_intelligence_card
from app.domains.kol import llm_deep_analysis as kol_llm_deep_analysis
import app.domains.intelligence.ai_brief as ai_brief
import app.domains.evidence.summary as evidence_summary
from app.domains.intelligence import gemini_single_kol_preflight

router = APIRouter(tags=["vkpi-kol-pool"])


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

    try:
        return business_contact_extract.add_manual_contact(
            int(kol_pool_id),
            email=str(body.get("email") or ""),
            platform=str(body.get("platform") or ""),
            handle=str(body.get("handle") or ""),
            staff=staff or {},
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"save contact error: {exc}") from exc


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
        return await run_in_threadpool(audience_stats.refresh_audience_stats, int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 估算功能失败不该炸接口,诚实回原因
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.get("/kol-pool/{kol_pool_id}/cooperation")
def get_kol_cooperation(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读 KOL 当前合作状态 + 时间线(平台为基准)。"""
    del staff
    from app.domains.kol import cooperation

    return cooperation.get_cooperation(int(kol_pool_id))


@router.post("/kol-pool/{kol_pool_id}/cooperation")
def record_kol_cooperation(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """KOL 合作动作(续约/加大投入/退出合作/评估/备注)→ 平台为基准的状态时间线。
    action ∈ renew|scale_up|exit|evaluate|note。零触 viltrox_fit_score。"""
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
    del staff
    from app.domains.kol import outreach_pack as kol_outreach_pack

    try:
        return kol_outreach_pack.get_outreach_pack(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
        return {"state": "error", "kol_pool_id": int(kol_pool_id), "reason": str(exc)[:300]}


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
    return kol_llm_deep_analysis.get_kol_llm_deep_analysis(int(kol_pool_id), limit=limit)


def _enqueue_content_fit_on_demand(kol_pool_id: int, product_sku, *, force: bool, staff) -> dict:
    """L1:内容契合按需入队逻辑已迁 domains/kol/content_fit_enqueue;此处薄委托(端点调用不变,行为一致)。"""
    from app.domains.kol import content_fit_enqueue

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
        return await run_in_threadpool(kol_content_fit.get_content_fit, int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
def translate_bio(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """#25 发现卡英文 bio → 简体中文(gpt-4o-mini,预算闸由 llm_gateway 内置)。

    进程内按原文缓存:同一 bio 第二次命中不再烧 LLM。失败/空/预算挡 → 诚实返回空译文(前端回退原文)。
    """
    text = str((body or {}).get("text") or "").strip()
    if not text:
        return {"translated": "", "lang": "zh", "cached": False, "skipped": "empty"}
    if len(text) > 1200:
        text = text[:1200]
    if text in _BIO_ZH_CACHE:
        return {"translated": _BIO_ZH_CACHE[text], "lang": "zh", "cached": True}
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
                return {"translated": out, "lang": "zh", "cached": False}
        return {"translated": "", "lang": "zh", "cached": False, "skipped": str(resp.get("status") or "unavailable")}
    except Exception as exc:  # noqa: BLE001 — 翻译失败不阻断,前端回退原文
        return {"translated": "", "lang": "zh", "cached": False, "error": str(exc)}
