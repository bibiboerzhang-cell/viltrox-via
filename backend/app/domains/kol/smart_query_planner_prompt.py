"""Pure prompt construction for the KOL smart-query planner."""
from __future__ import annotations

from typing import Any

from app.domains.kol import smart_query_facets
from app.domains.kol import smart_query_intent
from app.domains.kol import targeted_search_contract
from app.core.coerce import _text


def _as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_prompt(
    query_text: str,
    *,
    resolved_product: dict[str, Any] | None,
    body: dict[str, Any],
) -> str:
    """Build the provider prompt without performing I/O or changing planner state."""
    if resolved_product:
        # 焦段家族(操作员只说了「135」)没有唯一 SKU、也没有唯一价格。这两行必须如实写
        # "not specified",绝不能渲染成空 SKU 或 "None USD" —— 那会让模型把空值当成事实。
        _sku_line = _text(resolved_product.get("sku")) or "not a single SKU — the operator named a focal length family, not one model"
        _price = _as_float_or_none(resolved_product.get("price_usd"))
        _price_line = f"{_price} USD" if _price else "not specified (family spans several price points)"
        product_block = f"""
Resolved Viltrox product (matched from the operator text against the live product catalog — TRUST THIS over the raw text):
- SKU: {_sku_line}
- Name: {resolved_product.get('marketing_name') or resolved_product.get('model_name')}
- Category: {resolved_product.get('category_main')} / {resolved_product.get('series')}
- Price: {_price_line}
- Specs: {resolved_product.get('specs_line')}
First DEEPLY analyse THIS specific product (what it is, its price tier, its professional level, who actually buys it), then plan the creator search. Do NOT treat it as a generic camera accessory.
"""
    else:
        product_block = "\nNo specific catalog product was matched from the text; infer the product family from the words and plan a sensible creator search.\n"

    # 车道A/A2:锚串由目录真值推导后喂进 prompt,LLM 不必自己拼品牌/型号(拼错就没锚)。
    _anchor = smart_query_intent.product_anchor(resolved_product, query_text=query_text)
    anchor_hint = _anchor["prefix"] or "the product name the operator named"
    search_objective = targeted_search_contract.normalize_objective(body)
    explicit_segments = targeted_search_contract.extract_explicit_segments(query_text, body)
    segment_hint = ", ".join(item["query_term"] for item in explicit_segments) or "none explicitly named"
    if search_objective == targeted_search_contract.EXISTING_EVIDENCE:
        objective_rule = (
            f'- OBJECTIVE IS existing_evidence. Brand/model evidence is required. Every search query '
            f'must carry the resolved product anchor "{anchor_hint}".'
        )
    else:
        objective_rule = (
            "- OBJECTIVE IS prospective_growth. Find creators who would genuinely USE this kind of "
            "product and can activate its target market. Viltrox or model-name mentions are NOT required "
            "in queries, eligibility, or ranking. Search by product capability + creator job/use case."
        )

    return f"""
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
{smart_query_facets.FACET_PROMPT_RULE}
{objective_rule}
- EXPLICIT INDUSTRIES/USE CASES ARE IMMUTABLE. The operator explicitly named: {segment_hint}. Preserve every one as a separate item in segments; never replace them with a generic product persona. Do not merge several industries into one broad query.
- OUTPUT SHORT QUERIES, NOT ONE LONG SENTENCE. search_queries must hold 2-4 entries, each at most 6 words and each covering a DIFFERENT product-capability + creator-use-case angle. Do not restate the same angle with synonyms.
- If the request mentions flash, lighting, strobe, LED, Godox, or price/value, include lighting/flash creator terms.
- Prefer a balanced 15 creator / 15 reviewer search unless the user says otherwise.
- Platforms must be from: youtube, instagram, tiktok.
- Return JSON keys:
  objective: string (prospective_growth or existing_evidence; use {search_objective})
  segments: string[] (operator-explicit industries/use cases, translated to English and kept separate)
  search_queries: string[] (2-4 entries, each <= 6 words and each a different angle)
  search_query: string (a compatibility query; do not merge explicit industries)
  audience_scale: string (one of micro / mid / large / mega, or "" when the operator said nothing about reach)
  min_followers_hint: number (follower floor implied by audience_scale; 0 when unspecified — it is only a HINT, the operator's own filter always wins)
  product_focus: string[] (precise creator-type + vertical terms for this product's buyers)
  target_persona: string (one English sentence describing the ideal creator/buyer for this exact product)
  avoid_types: string[] (mismatched creator types to exclude for this exact product)
  product_positioning: string (one plain-language sentence: what it is, price tier, who it is for)
  platforms: string[]
  filter_proposal: object (countries / languages / verticals / min_followers / platforms — the operator's filter picks)
  market: string
  creator_quota: number
  reviewer_quota: number
  include_new_discovery: boolean
  new_discovery_limit: number
  reason: string
"""
