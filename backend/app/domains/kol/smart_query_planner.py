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


SUPPORTED_PLATFORMS = ("youtube", "instagram", "tiktok")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any, default: int, *, min_value: int = 0, max_value: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


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
        keywords.extend(["camera monitor", "field monitor", "on-camera monitor", "filmmaker gear", "cinematographer"])
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


def _normalise_plan(query: str, raw_plan: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_plan(query)
    search_query = _text(raw_plan.get("search_query") or raw_plan.get("query") or fallback["search_query"])
    platforms = [item for item in _as_list(raw_plan.get("platforms")) if item in SUPPORTED_PLATFORMS]
    if not platforms:
        platforms = fallback["platforms"]
    product_focus = _as_list(raw_plan.get("product_focus") or raw_plan.get("products") or raw_plan.get("keywords"))
    if not product_focus:
        product_focus = fallback["product_focus"]
    return {
        "status": "ready" if raw_plan else fallback["status"],
        "original_query": _text(query),
        "search_query": search_query,
        "product_focus": product_focus[:10],
        "target_persona": _text(raw_plan.get("target_persona") or raw_plan.get("audience") or fallback["target_persona"]),
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

    prompt = f"""
You are a V-KPI KOL search planner for Viltrox marketing.
Convert the operator request into one JSON object only. Do not include markdown.

Operator request:
{query_text}

Rules:
- OUTPUT MUST BE IN ENGLISH. Translate any Chinese / non-English request into English creator search terms. search_query and product_focus MUST be English keywords — never the raw Chinese text.
- Recognize Viltrox products and map to English creator terms: monitor / 监视器 / 550pro / 550 pro / 外接屏 / screen → "camera monitor", "field monitor", "on-camera monitor", "filmmaker gear", "cinematographer", "camera rig"; flash / 闪光灯 / 灯 → "lighting", "flash", "strobe"; lens / 镜头 → "photographer", "videographer", "camera gear".
- Target the ENGLISH-speaking market. Set market to "US" unless the user explicitly names another English region (UK/CA/AU/EU). Exclude Chinese-language creators.
- Preserve the original intent but expand it into searchable English creator terms.
- If the request mentions flash, lighting, strobe, LED, Godox, or price/value, include lighting/flash creator terms.
- Prefer a balanced 15 creator / 15 reviewer search unless the user says otherwise.
- Platforms must be from: youtube, instagram, tiktok.
- Return JSON keys:
  search_query: string
  product_focus: string[]
  target_persona: string
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
        max_output_tokens=700,
        preferred_provider=str(body.get("llm_provider") or "google"),
        cost_tag="kol_smart_search_query_plan",
        metadata={"query_text": query_text, "source": body.get("source") or "kol_smart_search"},
        staff=staff,
    )
    parsed = _extract_json(_text(response.get("text")))
    return _normalise_plan(query_text, parsed, response)
