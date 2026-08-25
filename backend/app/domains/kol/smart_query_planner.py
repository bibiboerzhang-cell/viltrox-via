"""LLM-backed planner for KOL smart text search.

The planner reshapes an operator's natural-language need into a bounded search
plan. It does not write scoring fields and degrades to a deterministic rule plan
if providers are unavailable.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.platform import llm_gateway
from app.domains.kol import product_resolver
from app.domains.kol import smart_query_intent

from app.core.logging import get_logger

logger = get_logger(__name__)


SUPPORTED_PLATFORMS = ("youtube", "instagram", "tiktok")
PLAN_DERIVE_METHOD = "smart_query_plan_v2_catalog_guard"

# 第一轮产品深度分析的首选 provider。可被 body.llm_provider 覆盖。
# 注:本环境实测 api.anthropic.com 直连被对端关闭、api.openai.com 直连 SSL 握手失败,
# 网关 _request_json 用裸 urllib 不带代理,故仅 google(generativelanguage)直连可达。
# 一旦注入真实产品 specs,gemini-flash 也能稳定产出可解析结构化 JSON(实测精准)。
# 若日后网关接入代理使 openai/anthropic 可达,改这里或传 body.llm_provider 即可。
DEFAULT_PLANNER_PROVIDER = "google"


from app.core.coerce import _text


def _as_int(value: Any, default: int, *, min_value: int = 0, max_value: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item).lower() for item in value if _text(item)]
    if isinstance(value, str):
        return [_text(part).lower() for part in re.split(r"[,/，、\s]+", value) if _text(part)]
    return []


def _extract_json(text: str) -> dict[str, Any]:
    raw = _text(text)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return {}


def _fallback_plan(query: str, *, reason: str = "rule_fallback") -> dict[str, Any]:
    query_text = _text(query)
    lowered = query_text.lower()
    platforms: list[str] = []
    for platform in SUPPORTED_PLATFORMS:
        if platform in lowered or (platform == "youtube" and "yt" in lowered):
            platforms.append(platform)
    if not platforms:
        platforms = ["youtube", "instagram", "tiktok"]

    keywords: list[str] = []
    is_lighting = any(term in lowered for term in ("flash", "strobe", "lighting", "light", "闪光", "灯", "补光"))
    if is_lighting:
        keywords.extend(["lighting", "flash", "strobe", "studio lighting"])
    # 2026-08-25 车道A/A4:此前裸 "evo" 也命中 300W 便携灯分支——「55evo」是 55mm F1.8 EVO
    # 镜头,却被翻成 "300W EVO portable lighting"(实测坐实),把用户的话理解成了另一个品类。
    # 瓦数词必须由灯光语境或真实瓦数触发;EVO 只是系列族名,交给产品锚去承载。
    if any(term in lowered for term in ("300w", "300 w")) or (is_lighting and "300" in lowered):
        keywords.extend(["300W", "portable lighting"])
    if any(term in lowered for term in ("人像", "portrait")):
        keywords.extend(["portrait", "portrait photographer"])
    if any(term in lowered for term in ("测评", "评测", "review", "gear")):
        keywords.extend(["gear reviewer", "camera gear review"])
    if any(term in lowered for term in ("monitor", "监视器", "550pro", "550 pro", "550por", "外接屏", "screen", "屏")):
        # 泛人群:监视器买家=各行业视频拍摄者,不止「监视器评测」。撒宽到创作者类型+代表垂类。
        keywords.extend([
            "camera monitor", "field monitor", "videographer", "filmmaker", "cinematographer",
            "content creator", "automotive videographer", "food videographer", "wedding filmmaker", "commercial video",
        ])
    if any(term in lowered for term in ("镜头", "lens", "lab", "mm")):
        keywords.extend(["lens review", "videographer", "photographer", "camera gear"])
    if any(term in lowered for term in ("电影感", "cinematic", "cinematography")):
        keywords.extend(["cinematic", "cinematography"])
    if any(term in lowered for term in ("旅行", "travel")):
        keywords.append("travel")
    if any(term in lowered for term in ("风光", "landscape")):
        keywords.append("landscape")
    if any(term in lowered for term in ("微距", "macro")):
        keywords.append("macro")
    if any(term in lowered for term in ("产品摄影", "product photography")):
        keywords.append("product photography")
    # 2026-08-24 R4:操作者自带的职业/场景词(赛车、餐饮…)必须独立命中英文检索词,
    # 不能只藏在「监视器」组合关键词后面被整体丢弃。输出保持纯英文(问题A:中文进
    # search_query 会捞中文号)。
    if any(term in lowered for term in ("赛车", "机车", "摩托")):
        keywords.extend(["automotive videographer", "motorsport", "racing"])
    if any(term in lowered for term in ("厨师", "餐饮", "美食", "烹饪")):
        keywords.extend(["food creator", "culinary", "chef", "food videographer"])
    if "婚礼" in lowered:
        keywords.append("wedding filmmaker")
    if "健身" in lowered:
        keywords.append("fitness creator")
    if "宠物" in lowered:
        keywords.append("pet creator")
    if "旅拍" in lowered:
        keywords.append("travel videographer")

    # 规避问题A:中文 query 直接塞进 search_query 会让平台搜出中文号。
    # 有英文关键词→只用英文关键词;纯中文无匹配→给英文影视器材兜底;ASCII 原串才保留。
    has_cjk = any("一" <= ch <= "鿿" for ch in query_text)
    if keywords:
        search_query = " ".join(dict.fromkeys(keywords)).strip()
    elif has_cjk:
        search_query = "camera gear reviewer filmmaker videographer"
    else:
        search_query = query_text
    # 2026-08-24 R1/F8:provider_free_* 是设计好的首屏免调用路径,不是降级事故。
    # 再自标 rule_v0/fallback_used=true 会在运维面/会话史里读成「LLM 全灭」假警报。
    provider_free_designed = str(reason).startswith("provider_free")
    plan_label = "provider_free" if provider_free_designed else "rule_v0"
    # 车道A/A3+A4:规则路径与 LLM 路径产出同一套结构(多条短 query + 受众规模档位)。
    # 未解析到产品 → 空锚,短 query 退化为纯题材短句,绝不编造品牌/型号词。
    anchor = smart_query_intent.product_anchor(None)
    search_queries = smart_query_intent.build_search_queries(anchor, keywords or [search_query])
    audience = smart_query_intent.normalise_audience_scale(
        None, None, detected=smart_query_intent.detect_audience_scale(query_text)
    )
    return {
        "status": "fallback",
        "original_query": query_text,
        "search_query": search_query or query_text,
        "search_queries": search_queries or [search_query or query_text],
        **audience,
        "product_focus": keywords[:6],
        "target_persona": query_text,
        "platforms": platforms,
        "market": "US",
        "creator_quota": 15,
        "reviewer_quota": 15,
        "include_new_discovery": True,
        "new_discovery_limit": 15,
        "reason": reason,
        "provider": plan_label,
        "model": plan_label,
        "fallback_used": not provider_free_designed,
        "provider_calls_performed": False,
    }


def _clarification_plan(query: str, clarification: dict[str, Any]) -> dict[str, Any]:
    """Stop an explicit but unresolved product request before any provider call."""
    return {
        "status": "needs_clarification",
        "original_query": _text(query),
        "search_query": "",
        "search_queries": [],
        "audience_scale": "",
        "min_followers_hint": None,
        "audience_scale_source": "unspecified",
        "product_focus": [],
        "target_persona": "",
        "avoid_types": [],
        "product_positioning": "",
        "platforms": ["youtube", "instagram", "tiktok"],
        "market": "US",
        "creator_quota": 0,
        "reviewer_quota": 0,
        "include_new_discovery": False,
        "new_discovery_limit": 0,
        "reason": _text(clarification.get("reason")) or "explicit_product_not_in_catalog",
        "provider": "product_catalog_guard",
        "model": "product_catalog_guard",
        "fallback_used": False,
        "provider_calls_performed": False,
        "clarification": clarification,
    }


def _require_evidence_anchor(plan: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when a preview plan cannot prove any lexical intent."""
    if _text(plan.get("status")) == "needs_clarification":
        return plan
    from app.domains.kol.profile_recall_match_evidence import query_evidence_terms

    if query_evidence_terms(plan.get("search_query")):
        return plan
    # 2026-08-24 R3:兜底英文检索词可能全是泛词(纯中文 query 撞英文兜底句),但原始
    # query 本身有可举证词(型号/职业/场景)→ 不能误杀成 needs_clarification。保留 plan,
    # 标注锚来自原始 query;search_query 原样保留——下游证据闸本就同时消费
    # resolved_text+persona 兜底,不需要在这里改写检索词。
    if query_evidence_terms(plan.get("original_query")):
        return {**plan, "anchor_source": "original_query"}
    return {
        **plan,
        "status": "needs_clarification",
        "search_query": "",
        "search_queries": [],
        "creator_quota": 0,
        "reviewer_quota": 0,
        "include_new_discovery": False,
        "new_discovery_limit": 0,
        "reason": "no_evidence_anchor",
        "clarification": {
            "reason": "no_evidence_anchor",
            "message": "没识别出产品型号，也没识别出内容场景/职业——补一个具体产品（如 Z1 Pro）或职业词（如 赛车摄影）再搜",
        },
    }


def _resolve_requested_product(
    query_text: str,
    body: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve explicit SKU and free text to one catalog identity, or explain why not."""
    try:
        inferred = product_resolver.resolve_product(query_text)
    except Exception:
        inferred = None
    explicit_sku = _text(body.get("product_sku") or body.get("productSku"))
    if explicit_sku:
        try:
            explicit = product_resolver.resolve_product_sku(explicit_sku)
        except Exception:
            explicit = None
        if not explicit:
            return None, {
                "reason": "explicit_product_sku_not_in_catalog",
                "message": "所选产品不在当前产品目录中，请重新选择后再找达人。",
                "suggestions": [],
            }
        inferred_sku = _text((inferred or {}).get("sku")).lower()
        explicit_resolved_sku = _text(explicit.get("sku")).lower()
        if inferred_sku and inferred_sku != explicit_resolved_sku:
            return None, {
                "reason": "conflicting_product_constraints",
                "message": "输入内容与所选产品不一致，请确认一个产品后再找达人。",
                "suggestions": [],
            }
        return explicit, None
    if inferred:
        return inferred, None
    try:
        return None, product_resolver.unresolved_product_request(query_text)
    except Exception:
        return None, None


def _avoid_types_for_product(product: dict[str, Any] | None) -> list[str]:
    """无 LLM avoid_types 时,据解析到的产品类别给规则兜底的错配规避类型(英文检索词)。"""
    if not product:
        return []
    blob = " ".join(
        str(product.get(key) or "")
        for key in ("category_main", "category_detail", "series", "model_name", "marketing_name")
    ).lower()
    avoid: list[str] = []
    if "cine" in blob or "anamorphic" in blob:
        # 电影镜头:规避泛器材评测/纯平面/手机 vlog。
        avoid = ["generic gear reviewer", "still-photography-only photographer", "phone vlogger", "camera store unboxing channel"]
    elif "monitor" in blob:
        avoid = ["still-photography-only photographer", "phone vlogger"]
    elif "flash" in blob or "lighting" in blob:
        avoid = ["pure landscape shooter", "automotive-only videographer"]
    return avoid


def _product_search_terms(product: dict[str, Any] | None) -> list[str]:
    """LLM 失败但已解析到产品时,据产品类别给英文检索词兜底(避免把裸 query 当检索词)。"""
    if not product:
        return []
    blob = " ".join(
        str(product.get(key) or "")
        for key in ("category_main", "category_detail", "series", "model_name", "marketing_name")
    ).lower()
    if "cine" in blob or "anamorphic" in blob:
        return ["cinematographer", "director of photography", "filmmaker", "anamorphic filmmaker", "commercial film", "music video"]
    if "monitor" in blob:
        return ["filmmaker", "videographer", "cinematographer", "field monitor", "content creator", "camera operator"]
    if "flash" in blob or "lighting" in blob:
        return ["wedding photographer", "portrait photographer", "studio lighting", "off-camera flash", "lighting educator"]
    if "lens" in blob:
        return ["photographer", "videographer", "portrait photographer", "filmmaker"]
    return []


def _normalise_plan(
    query: str,
    raw_plan: dict[str, Any],
    response: dict[str, Any],
    product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = _fallback_plan(query)
    # LLM 没给可用 search_query 时:已解析到产品 → 用产品派生英文检索词;否则回退 rule。
    product_terms = _product_search_terms(product)
    llm_search_query = _text(raw_plan.get("search_query") or raw_plan.get("query"))
    if llm_search_query:
        search_query = llm_search_query
    elif product_terms:
        search_query = " ".join(product_terms)
    else:
        search_query = fallback["search_query"]
    platforms = [item for item in _as_list(raw_plan.get("platforms")) if item in SUPPORTED_PLATFORMS]
    if not platforms:
        platforms = fallback["platforms"]
    product_focus = _as_list(raw_plan.get("product_focus") or raw_plan.get("products") or raw_plan.get("keywords"))
    if not product_focus:
        product_focus = product_terms or fallback["product_focus"]
    avoid_types = _as_list(raw_plan.get("avoid_types") or raw_plan.get("avoid") or raw_plan.get("mismatch_types"))
    if not avoid_types:
        avoid_types = _avoid_types_for_product(product)
    # 产品定位(说人话):优先 LLM product_positioning,缺则用解析 specs 兜底。
    product_positioning = _text(
        raw_plan.get("product_positioning")
        or raw_plan.get("positioning")
        or (product or {}).get("specs_line")
    )
    # target_persona 兜底:LLM 缺 → 用产品定位句(说人话),再缺才回退 rule(原始 query)。
    target_persona = _text(
        raw_plan.get("target_persona")
        or raw_plan.get("audience")
        or product_positioning
        or fallback["target_persona"]
    )
    # ── 车道A(2026-08-25)意图契约 ─────────────────────────────────────────────
    # A2:检索词必须携带产品锚。证据闸的 intent 腿(≥2 非泛词)与产品腿此前吃两套完全
    #     分开的词表,人为造成跨词表 AND。prod a05e48dd3 只读复验(vkpi_kol_pool 全量
    #     2034 人 + 代表作标题,产品腿按 55mm F1.8 EVO 要求):无锚 70 人过闸、带锚 88 人
    #     过闸(1.26×,零人掉队——锚词只进 intent 词池,过闸集合是严格超集)。
    # A3:一条长 query 同时服务向量召回 / YT 搜索 / IG 标签 → 拆成 2-4 条 ≤6 词短 query,
    #     search_query 保留为合并串供未改造的下游消费(向后兼容)。
    # A1:受众规模落成档位 + 粉丝下限**建议值**,绝不再被译成题材词。
    anchor = smart_query_intent.product_anchor(product, query_text=query)
    angles = smart_query_intent.angle_terms(
        [*_as_list(raw_plan.get("search_queries")), *product_focus, search_query], anchor
    )
    search_queries = smart_query_intent.build_search_queries(anchor, angles)
    compat_query = smart_query_intent.compat_search_query(
        search_queries, anchor, extra_terms=[search_query, *product_focus]
    )
    audience = smart_query_intent.normalise_audience_scale(
        raw_plan.get("audience_scale"),
        raw_plan.get("min_followers_hint") or raw_plan.get("followers_min_hint"),
        detected=smart_query_intent.detect_audience_scale(query),
    )
    return {
        "status": "ready" if raw_plan else fallback["status"],
        "original_query": _text(query),
        "search_query": compat_query or search_query,
        "search_queries": search_queries or [compat_query or search_query],
        **audience,
        "product_anchor": anchor["prefix"],
        "product_focus": product_focus[:10],
        "target_persona": target_persona,
        "avoid_types": avoid_types[:8],
        "product_positioning": product_positioning,
        "platforms": platforms,
        "market": _text(raw_plan.get("market") or raw_plan.get("country")),
        "creator_quota": _as_int(raw_plan.get("creator_quota"), 15, min_value=0, max_value=50),
        "reviewer_quota": _as_int(raw_plan.get("reviewer_quota"), 15, min_value=0, max_value=50),
        "include_new_discovery": bool(raw_plan.get("include_new_discovery", True)),
        "new_discovery_limit": _as_int(raw_plan.get("new_discovery_limit"), 15, min_value=1, max_value=50),
        "reason": _text(raw_plan.get("reason") or fallback["reason"]),
        "provider": _text(response.get("provider") or fallback["provider"]),
        "model": _text(response.get("model") or fallback["model"]),
        "fallback_used": bool(response.get("fallback_used")) or not bool(raw_plan),
        "provider_calls_performed": _text(response.get("status")) == "success",
        # 解析到的真实产品(纯展示/审计;无匹配为 None)。绝不参与任何评分。
        "resolved_product": (
            {
                "sku": product.get("sku"),
                "model_name": product.get("model_name"),
                "marketing_name": product.get("marketing_name"),
                "category_main": product.get("category_main"),
                "series": product.get("series"),
                "price_usd": _as_float_or_none(product.get("price_usd")),
            }
            if product
            else None
        ),
    }


def _plan_from_product_persona(
    query: str,
    product: dict[str, Any],
    body: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """收口路①-1:用产品知识库 persona(地基A)直接成 plan,跳过 on-the-fly LLM。

    命中已填充 persona → 返回与 _normalise_plan 同形的 plan(search_query / product_focus /
    target_persona / avoid_types / product_positioning 全取 persona 真值);未填充 → 返回 None
    让调用方回退 LLM。纯只读,零写 fit / 零改 rule_v0;读失败静默 None。
    """
    # 显式关停开关(可由 body 覆盖):默认开,need 时可强制走 LLM 对比。
    if str((body or {}).get("use_product_persona", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return None
    try:
        from app.domains.costs import product_persona as product_persona_kb

        persona = product_persona_kb.get_product_persona(str(product.get("sku") or ""))
    except Exception:
        return None
    if not persona:
        return None

    ideal_persona = _text(persona.get("ideal_persona"))
    creator_types = _as_list(persona.get("ideal_creator_types_json"))
    verticals = _as_list(persona.get("verticals_json"))
    avoid_types = _as_list(persona.get("avoid_types_json"))
    what_is = _text(persona.get("what_is"))
    # search_query / product_focus:理想创作者类型 + 代表垂类(英文检索词,与 LLM 口径一致)。
    focus = list(dict.fromkeys([*creator_types, *verticals]))
    if not focus:
        # persona 行存在但类型/垂类为空 → 退 LLM,避免空检索词。
        return None
    target_persona = ideal_persona or _text(query)
    product_positioning = what_is or _text(product.get("specs_line"))

    # 车道A:persona 路径与 LLM 路径共用同一套锚/短 query/受众规模契约。
    anchor = smart_query_intent.product_anchor(product, query_text=query)
    angles = smart_query_intent.angle_terms(focus, anchor)
    search_queries = smart_query_intent.build_search_queries(anchor, angles)
    search_query = smart_query_intent.compat_search_query(
        search_queries, anchor, extra_terms=focus
    ) or " ".join(focus).strip()
    audience = smart_query_intent.normalise_audience_scale(
        None, None, detected=smart_query_intent.detect_audience_scale(query)
    )

    fallback = _fallback_plan(query)
    return {
        "status": "ready",
        "original_query": _text(query),
        "search_query": search_query or fallback["search_query"],
        "search_queries": search_queries or [search_query or fallback["search_query"]],
        **audience,
        "product_anchor": anchor["prefix"],
        "product_focus": focus[:10],
        "target_persona": target_persona,
        "avoid_types": avoid_types[:8],
        "product_positioning": product_positioning,
        "platforms": fallback["platforms"],
        "market": "US",
        "creator_quota": 15,
        "reviewer_quota": 15,
        "include_new_discovery": True,
        "new_discovery_limit": 15,
        "reason": "product_persona_kb",
        # provider/model 标知识库来源(非本次 LLM 调用);审计可见走的是 persona 而非 on-the-fly。
        "provider": "product_persona_kb",
        "model": _text(persona.get("model")) or "product_persona_kb",
        "fallback_used": False,
        "provider_calls_performed": False,
        "persona_source": _text(persona.get("source")) or "llm_persona_v1",
        "resolved_product": {
            "sku": product.get("sku"),
            "model_name": product.get("model_name"),
            "marketing_name": product.get("marketing_name"),
            "category_main": product.get("category_main"),
            "series": product.get("series"),
            "price_usd": _as_float_or_none(product.get("price_usd")),
        },
    }


def plan_text_query_provider_free(
    query: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the first-screen search plan without contacting an LLM provider.

    The HTTP search route uses this bounded plan only for its immediate pool
    preview.  The durable worker still calls :func:`plan_text_query` before the
    full recall/discovery pipeline, so the richer provider-backed plan is not
    lost; it is merely moved out of the request latency path.
    """

    body = body or {}
    query_text = _text(query)
    if not query_text:
        return _require_evidence_anchor(_fallback_plan(query_text, reason="empty_query"))

    resolved_product, clarification = _resolve_requested_product(query_text, body)
    if clarification:
        return _clarification_plan(query_text, clarification)
    if not resolved_product:
        return _require_evidence_anchor(_fallback_plan(query_text, reason="provider_free_initial"))

    persona_plan = _plan_from_product_persona(query_text, resolved_product, body)
    if persona_plan is not None:
        return _require_evidence_anchor({
            **persona_plan,
            "plan_stage": "initial_provider_free",
            "provider_calls_performed": False,
        })

    # No materialized persona yet: retain the existing category-aware English
    # fallback, but never fall through to llm_gateway.invoke on the request.
    plan = _normalise_plan(
        query_text,
        {},
        {"provider": "rule_v0", "model": "rule_v0", "status": "fallback"},
        resolved_product,
    )
    # 2026-08-24 R1/F8(verify 补刀):本分支与 _fallback_plan 的 provider_free_* 同理——
    # 设计好的首屏免调用路径(产品已解析、persona 未填充),不是 LLM 降级事故;
    # 自标 rule_v0/fallback_used=True 会在台账/运维面读成假警报。durable worker
    # 随后仍跑完整 plan_text_query,真降级在那里如实记账。
    return _require_evidence_anchor({
        **plan,
        "market": _text(plan.get("market")) or "US",
        "reason": "provider_free_product_fallback",
        "provider": "provider_free",
        "model": "provider_free",
        "fallback_used": False,
        "provider_calls_performed": False,
        "plan_stage": "initial_provider_free",
    })


def _plan_text_query_impl(
    query: str,
    *,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a search plan for a natural-language KOL need."""

    body = body or {}
    query_text = _text(query)
    if not query_text:
        return _fallback_plan(query_text, reason="empty_query")
    if str(body.get("use_llm_planner", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return _fallback_plan(query_text, reason="llm_planner_disabled")

    # 第一轮:把 query 里提到的产品(epic 65macro / 550pro / Z1pro / SKU…)模糊匹配到
    # 真实 vkpi_products,取真 SKU + 营销名 + 类别 + 价格 + 描述。匹配到就注入 LLM prompt,
    # 让第一轮 LLM 据真实 specs 深度分析产品定位与人群,而非对着裸 query 产泛词。读取失败不崩。
    resolved_product, clarification = _resolve_requested_product(query_text, body)
    if clarification:
        return _clarification_plan(query_text, clarification)

    # 收口路①-1:解析到 SKU 后,优先读已填充的产品知识库 persona(地基A)。命中即直接拿
    # ideal_persona / avoid_types / ideal_creator_types / verticals,跳过 on-the-fly LLM(更稳更快、
    # 与批跑 369 SKU 口径一致)。无 persona(尚未填充)则照常走下面的 LLM planner。
    # 红线:只读 LLM 生成的 persona 文本/标签,零写 fit、零改 rule_v0。读失败静默回退 LLM。
    if resolved_product and str(resolved_product.get("sku") or "").strip():
        persona_plan = _plan_from_product_persona(query_text, resolved_product, body)
        if persona_plan is not None:
            return persona_plan

    if resolved_product:
        product_block = f"""
Resolved Viltrox product (matched from the operator text against the live product catalog — TRUST THIS over the raw text):
- SKU: {resolved_product.get('sku')}
- Name: {resolved_product.get('marketing_name') or resolved_product.get('model_name')}
- Category: {resolved_product.get('category_main')} / {resolved_product.get('series')}
- Price: {resolved_product.get('price_usd')} USD
- Specs: {resolved_product.get('specs_line')}
First DEEPLY analyse THIS specific product (what it is, its price tier, its professional level, who actually buys it), then plan the creator search. Do NOT treat it as a generic camera accessory.
"""
    else:
        product_block = "\nNo specific catalog product was matched from the text; infer the product family from the words and plan a sensible creator search.\n"

    # 车道A/A2:锚串由目录真值推导后喂进 prompt,LLM 不必自己拼品牌/型号(拼错就没锚)。
    _anchor = smart_query_intent.product_anchor(resolved_product, query_text=query_text)
    anchor_hint = _anchor["prefix"] or "the product name the operator named"

    prompt = f"""
You are a V-KPI KOL search planner for Viltrox marketing.
Convert the operator request into ONE JSON object only. Output ONLY the JSON object — no markdown fences, no prose, no thinking, no explanation before or after.

Operator request:
{query_text}
{product_block}
Rules:
- FIRST analyse the resolved product's true positioning (what it is, price tier, professional level). A $4000+ PL-mount anamorphic CINE / macro lens is a high-end professional cinema tool bought by DPs, cinematographers and filmmakers shooting commercial / product / food / MV / narrative work — NOT by generic gear-review channels, still-only photographers or phone vloggers. A camera monitor is bought by filmmakers/videographers across verticals. A retro on-camera flash is bought by wedding/portrait/studio shooters.
- Produce avoid_types: the mismatched creator types to EXCLUDE for this exact product (e.g. for a cine lens: "generic gear reviewer", "still-photography-only photographer", "phone vlogger", "camera store unboxing channel").
- Produce product_positioning: one plain-language sentence stating what this product is, its price tier and who it is for (this is shown to a human operator — speak plainly, no SKU codes).
- OUTPUT MUST BE IN ENGLISH. Translate any Chinese / non-English request into English creator search terms. search_query and product_focus MUST be English keywords — never the raw Chinese text.
- Recognize Viltrox products and map to English creator terms: monitor / 监视器 / 550pro / 550 pro / 外接屏 / screen → camera monitor / field monitor / on-camera monitor / filmmaker gear; flash / 闪光灯 / 灯 → lighting / flash / strobe; lens / 镜头 → photographer / videographer.
- FIRST analyze WHO actually uses/buys this product, then BROADEN the search — do NOT narrow to only literal product-name matches. A camera monitor is used by filmmakers, videographers, photographers and content creators ACROSS many verticals (automotive/racing, food/culinary, weddings & events, travel, commercial/ad, sports, real-estate, music video, documentary). product_focus should mix creator-type terms (videographer, filmmaker, cinematographer, content creator, DP) with a few representative verticals; write target_persona as one sentence describing this buyer group broadly.
- Target the ENGLISH-speaking market. Set market to "US" unless the user explicitly names another English region (UK/CA/AU/EU). Exclude Chinese-language creators.
- Preserve the original intent but expand it into searchable English creator terms.
{smart_query_intent.AUDIENCE_SCALE_PROMPT_RULE}
- PRODUCT ANCHOR IS MANDATORY. Every entry of search_queries, and search_query itself, MUST start with the resolved product anchor "{anchor_hint}" (brand + model, plus the mount where it fits). A search term set that never names the product cannot prove product relevance downstream.
- OUTPUT SHORT QUERIES, NOT ONE LONG SENTENCE. search_queries must hold 2-4 entries, each at most 6 words, each carrying the product anchor, and each covering a DIFFERENT angle (product + mount / product + genre / product + use case / product + category). Do not restate the same angle with synonyms.
- If the request mentions flash, lighting, strobe, LED, Godox, or price/value, include lighting/flash creator terms.
- Prefer a balanced 15 creator / 15 reviewer search unless the user says otherwise.
- Platforms must be from: youtube, instagram, tiktok.
- Return JSON keys:
  search_queries: string[] (2-4 entries, each <= 6 words, each starting with the product anchor, each a different angle)
  search_query: string (the single merged fallback line; it MUST also start with the product anchor)
  audience_scale: string (one of micro / mid / large / mega, or "" when the operator said nothing about reach)
  min_followers_hint: number (follower floor implied by audience_scale; 0 when unspecified — it is only a HINT, the operator's own filter always wins)
  product_focus: string[] (precise creator-type + vertical terms for this product's buyers)
  target_persona: string (one English sentence describing the ideal creator/buyer for this exact product)
  avoid_types: string[] (mismatched creator types to exclude for this exact product)
  product_positioning: string (one plain-language sentence: what it is, price tier, who it is for)
  platforms: string[]
  market: string
  creator_quota: number
  reviewer_quota: number
  include_new_discovery: boolean
  new_discovery_limit: number
  reason: string
"""
    response = llm_gateway.invoke(
        prompt,
        purpose="kol_smart_search_query_plan",
        # 4096 tokens:Gemini 思考模型的「思考 token」与 JSON 输出共享 max_output_tokens 预算,
        # 1200 仍被思考吃光 → JSON 在 ~668 字符处截断 → _extract_json 解析失败 → 退泛词
        # (「找谁都出同一批摄影师」的真因)。4096 留足思考+完整 JSON;实测 fallback_used 消除、检索词按产品差异化。
        max_output_tokens=4096,
        preferred_provider=str(body.get("llm_provider") or DEFAULT_PLANNER_PROVIDER),
        cost_tag="kol_smart_search_query_plan",
        metadata={
            "query_text": query_text,
            "source": body.get("source") or "kol_smart_search",
            "resolved_product_sku": (resolved_product or {}).get("sku") or "",
        },
        staff=staff,
    )
    parsed = _extract_json(_text(response.get("text")))
    return _normalise_plan(query_text, parsed, response, resolved_product)


# ── Wave2-#4 规划缓存(2026-07-02):同 query 7 天内直接回缓存,检索同步段 8s -> <0.1s。
# 键=md5(归一化 query);v2 使目录闸上线前缓存的错误产品计划立即失效。
# fallback 计划(带 reason)不缓存;读写任何异常都静默回退真算。红线:不触 viltrox_fit_score。
def plan_text_query(
    query: str,
    *,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import hashlib as _hl
    import json as _pj
    from datetime import datetime as _dt, timezone as _tz

    body = body or {}
    cache_identity = "|".join(
        (_text(query).strip().lower(), _text(body.get("product_sku") or body.get("productSku")).lower())
    )
    _qkey = _hl.md5(cache_identity.encode("utf-8")).hexdigest()
    try:
        from app.domains.analysis.cache_repo import get_analysis_cache_entry as _gc

        _e = _gc("search_plan", _qkey, derive_method=PLAN_DERIVE_METHOD)
        if _e and _e.get("status") == "ready":
            _u = str(_e.get("updated_at") or "")
            _t = _dt.fromisoformat(_u.replace("Z", "+00:00")) if _u else None
            if _t is not None and _t.tzinfo is None:
                _t = _t.replace(tzinfo=_tz.utc)
            if _t and (_dt.now(_tz.utc) - _t).total_seconds() < 7 * 86400:
                _res = _e.get("result")
                _plan = _pj.loads(_res) if isinstance(_res, str) else _res
                if isinstance(_plan, dict) and _plan.get("search_query"):
                    _plan["plan_cache"] = "hit"
                    return _plan
    except Exception:
        logger.debug("plan 缓存读取失败,走实时规划(best-effort)", exc_info=True)
    _plan = _plan_text_query_impl(query, body=body, staff=staff)
    try:
        if isinstance(_plan, dict) and _plan.get("search_query") and not _plan.get("fallback_used"):
            from app.db.connection import get_conn as _gcn

            _cc = _gcn()
            # result 列是 jsonb:compat 连接用 ?::jsonb(翻译层转 %s::jsonb);ON CONFLICT 幂等。
            _cc.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?::jsonb, ?, 'ready', NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET result = EXCLUDED.result, status = 'ready', updated_at = NOW()
                """,
                ("search_plan", _qkey, "plan_cache", PLAN_DERIVE_METHOD,
                 _pj.dumps(_plan, ensure_ascii=False), 0),
            )
            _cc.commit()
    except Exception:
        logger.debug("plan 缓存写入失败(best-effort,不影响返回)", exc_info=True)
    return _plan
