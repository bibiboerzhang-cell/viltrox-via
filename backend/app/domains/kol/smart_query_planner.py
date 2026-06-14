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


SUPPORTED_PLATFORMS = ("youtube", "instagram", "tiktok")

# 第一轮产品深度分析的首选 provider。可被 body.llm_provider 覆盖。
# 注:本环境实测 api.anthropic.com 直连被对端关闭、api.openai.com 直连 SSL 握手失败,
# 网关 _request_json 用裸 urllib 不带代理,故仅 google(generativelanguage)直连可达。
# 一旦注入真实产品 specs,gemini-flash 也能稳定产出可解析结构化 JSON(实测精准)。
# 若日后网关接入代理使 openai/anthropic 可达,改这里或传 body.llm_provider 即可。
DEFAULT_PLANNER_PROVIDER = "google"


def _text(value: Any) -> str:
    return str(value or "").strip()


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
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
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
    if any(term in lowered for term in ("flash", "strobe", "lighting", "light", "闪光", "灯", "补光")):
        keywords.extend(["lighting", "flash", "strobe", "studio lighting"])
    if any(term in lowered for term in ("evo", "300", "300w")):
        keywords.extend(["300W", "EVO", "portable lighting"])
    if any(term in lowered for term in ("人像", "portrait")):
        keywords.extend(["portrait", "portrait photographer"])
    if any(term in lowered for term in ("测评", "review", "gear")):
        keywords.extend(["gear reviewer", "camera gear review"])
    if any(term in lowered for term in ("monitor", "监视器", "550pro", "550 pro", "550por", "外接屏", "screen", "屏")):
        # 泛人群:监视器买家=各行业视频拍摄者,不止「监视器评测」。撒宽到创作者类型+代表垂类。
        keywords.extend([
            "camera monitor", "field monitor", "videographer", "filmmaker", "cinematographer",
            "content creator", "automotive videographer", "food videographer", "wedding filmmaker", "commercial video",
        ])
    if any(term in lowered for term in ("镜头", "lens", "lab", "mm")):
        keywords.extend(["videographer", "photographer", "camera gear"])

    # 规避问题A:中文 query 直接塞进 search_query 会让平台搜出中文号。
    # 有英文关键词→只用英文关键词;纯中文无匹配→给英文影视器材兜底;ASCII 原串才保留。
    has_cjk = any("一" <= ch <= "鿿" for ch in query_text)
    if keywords:
        search_query = " ".join(dict.fromkeys(keywords)).strip()
    elif has_cjk:
        search_query = "camera gear reviewer filmmaker videographer"
    else:
        search_query = query_text
    return {
        "status": "fallback",
        "original_query": query_text,
        "search_query": search_query or query_text,
        "product_focus": keywords[:6],
        "target_persona": query_text,
        "platforms": platforms,
        "market": "US",
        "creator_quota": 15,
        "reviewer_quota": 15,
        "include_new_discovery": True,
        "new_discovery_limit": 15,
        "reason": reason,
        "provider": "rule_v0",
        "model": "rule_v0",
        "fallback_used": True,
        "provider_calls_performed": False,
    }


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
    return {
        "status": "ready" if raw_plan else fallback["status"],
        "original_query": _text(query),
        "search_query": search_query,
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
    search_query = " ".join(focus).strip()
    target_persona = ideal_persona or _text(query)
    product_positioning = what_is or _text(product.get("specs_line"))

    fallback = _fallback_plan(query)
    return {
        "status": "ready",
        "original_query": _text(query),
        "search_query": search_query or fallback["search_query"],
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


def plan_text_query(
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
    try:
        resolved_product = product_resolver.resolve_product(query_text)
    except Exception:
        resolved_product = None

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
- If the request mentions flash, lighting, strobe, LED, Godox, or price/value, include lighting/flash creator terms.
- Prefer a balanced 15 creator / 15 reviewer search unless the user says otherwise.
- Platforms must be from: youtube, instagram, tiktok.
- Return JSON keys:
  search_query: string (precise English creator search terms for THIS product's buyers; for a cine lens use cinema/anamorphic/DP/commercial-film terms, never generic "camera gear review")
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
        # 1200 tokens:留足结构化 JSON 空间,避免 provider 思考/前言吃掉/截断输出(实测 700 截断)。
        max_output_tokens=1200,
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
