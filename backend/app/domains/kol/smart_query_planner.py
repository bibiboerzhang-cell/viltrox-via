"""LLM-backed planner for KOL smart text search.

The planner reshapes an operator's natural-language need into a bounded search
plan. It does not write scoring fields and degrades to a deterministic rule plan
if providers are unavailable.
"""
from __future__ import annotations

from typing import Any

from app.platform import llm_gateway
from app.domains.kol import product_resolver
from app.domains.kol import smart_query_facets
from app.domains.kol import smart_query_intent
from app.domains.kol import smart_query_planner_prompt
from app.domains.kol import targeted_search_contract
from app.domains.kol.product_resolver_projection import planner_product_projection
from app.domains.kol.smart_query_planner_cache import plan_text_query_cached
from app.domains.kol.smart_query_planner_diagnostics import (
    extract_json,
    planner_not_attempted_diagnostics as _planner_not_attempted_diagnostics,
    planner_response_diagnostics as _planner_response_diagnostics,
)
from app.domains.kol.smart_query_planner_rules import (
    as_list as _as_list,
    avoid_types_for_product as _avoid_types_for_product,
    fallback_keywords as _fallback_keywords,
    fallback_platforms,
    product_search_terms as _product_search_terms,
    vague_people_request as _vague_people_request,
)
from app.domains.kol.smart_query_planner_products import (
    catalog_unavailable_clarification as _catalog_unavailable_clarification,
    multiple_product_clarification as _multiple_product_clarification,
    product_constraints_conflict as _product_constraints_conflict,
    product_identity_key as _product_identity_key,
    resolve_requested_product as _resolve_requested_product,
)

from app.core.logging import get_logger

logger = get_logger(__name__)


SUPPORTED_PLATFORMS = ("youtube", "instagram", "tiktok")
PLAN_DERIVE_METHOD = "smart_query_plan_v4_people_intent_first"
PLANNER_REQUIRED_KEYS = (
    "search_query",
    "product_focus",
    "target_persona",
    "platforms",
)

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


def _extract_json(text: str) -> dict[str, Any]:
    return extract_json(text, logger=logger)


def _validate_planner_json_contract(value: Any) -> tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "planner output must be a JSON object"
    if not _text(value.get("search_query")):
        return False, "search_query must be a non-empty string"
    focus = value.get("product_focus")
    if not isinstance(focus, list) or not any(_text(item) for item in focus):
        return False, "product_focus must be a non-empty string array"
    if not _text(value.get("target_persona")):
        return False, "target_persona must be a non-empty string"
    platforms = value.get("platforms")
    if not isinstance(platforms, list) or not any(
        _text(item).lower() in SUPPORTED_PLATFORMS for item in platforms
    ):
        return False, "platforms must include a supported platform"
    return True, ""


def _fallback_platforms(lowered: str) -> list[str]:
    return fallback_platforms(lowered, SUPPORTED_PLATFORMS)


def _fallback_plan(
    query: str,
    *,
    reason: str = "rule_fallback",
    body: dict[str, Any] | None = None,
    product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query_text = _text(query)
    lowered = query_text.lower()
    platforms = _fallback_platforms(lowered)
    keywords = _fallback_keywords(lowered)

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
    # 默认 prospective_growth 只按产品能力/使用场景搜潜在用户;只有显式
    # existing_evidence 才把品牌/型号锚带进检索词。
    objective = targeted_search_contract.normalize_objective(body)
    anchor = smart_query_intent.product_anchor(
        product if objective == targeted_search_contract.EXISTING_EVIDENCE else None,
        query_text=query_text,
    )
    search_queries = smart_query_intent.build_search_queries(anchor, keywords or [search_query])
    audience = smart_query_intent.normalise_audience_scale(
        None, None, detected=smart_query_intent.detect_audience_scale(query_text)
    )
    plan = {
        "status": "fallback",
        "original_query": query_text,
        "search_query": search_query or query_text,
        "search_queries": search_queries or [search_query or query_text],
        "objective": objective,
        **audience,
        "product_focus": keywords[:6],
        "target_persona": targeted_search_contract.build_target_persona(
            query=query_text,
            body=body,
            product=product,
            product_focus=keywords,
        ),
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
        **_planner_not_attempted_diagnostics(),
    }
    # 车道「模型提议筛选」:规则路径也要给出五项筛选提议(全部标成推断项),
    # 否则操作员在降级时又回到「自己勾一堆最后 0 个人」。零成本、纯字符串规则。
    plan["filter_proposal"] = smart_query_facets.propose_facets(query_text, plan)
    return targeted_search_contract.apply_targeted_contract(
        plan,
        query=query_text,
        body=body,
        product=product,
    )


def _clarification_plan(
    query: str,
    clarification: dict[str, Any],
    *,
    body: dict[str, Any] | None = None,
    product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stop an explicit but unresolved product request before any provider call."""
    plan = {
        "status": "needs_clarification",
        "original_query": _text(query),
        "search_query": "",
        "search_queries": [],
        "query_cells": [],
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
        **_planner_not_attempted_diagnostics(),
        "clarification": clarification,
    }
    if clarification.get("catalog_status"):
        plan["catalog_status"] = clarification["catalog_status"]
    return targeted_search_contract.apply_targeted_contract(
        plan,
        query=query,
        body=body,
        product=product,
    )


def _needs_people_clarification(query: str, body: dict[str, Any] | None) -> bool:
    """Ask for a people brief only when neither text nor filters supply one."""

    body_segments = targeted_search_contract.extract_explicit_segments("", body)
    return _vague_people_request(query) and not body_segments


def _people_clarification_plan(
    query: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _clarification_plan(
        query,
        {
            "reason": "missing_people_intent",
            "message": "请补充要找的行业、拍摄场景、人物角色或内容形式；不需要输入 SKU。",
            "suggestions": [],
        },
        body=body,
    )


def _require_evidence_anchor(plan: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when a preview plan cannot prove any lexical intent."""
    if _text(plan.get("status")) == "needs_clarification":
        return plan
    if plan.get("explicit_segments"):
        # A controlled operator-owned people role/scene is already auditable
        # intent, even when its English occupation words are intentionally
        # excluded from the generic lexical evidence helper.
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
        "query_cells": [],
        "creator_quota": 0,
        "reviewer_quota": 0,
        "include_new_discovery": False,
        "new_discovery_limit": 0,
        "reason": "no_evidence_anchor",
        "clarification": {
            "reason": "no_evidence_anchor",
            "message": "没识别出要找的行业、场景、人物角色或内容形式，请补充其中一项；不需要输入 SKU。",
        },
    }


def _normalise_plan(
    query: str,
    raw_plan: dict[str, Any],
    response: dict[str, Any],
    product: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = _fallback_plan(query, body=body, product=product)
    response_diagnostics = _planner_response_diagnostics(response, raw_plan)
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
        product_focus = list(dict.fromkeys([*fallback["product_focus"], *product_terms]))
    avoid_types = _as_list(raw_plan.get("avoid_types") or raw_plan.get("avoid") or raw_plan.get("mismatch_types"))
    if not avoid_types:
        avoid_types = _avoid_types_for_product(product)
    # 产品定位(说人话):优先 LLM product_positioning,缺则用解析 specs 兜底。
    product_positioning = _text(
        raw_plan.get("product_positioning")
        or raw_plan.get("positioning")
        or (product or {}).get("specs_line")
    )
    # target_persona 描述要找的人；产品规格只能作为能力证据，不能冒充人物画像。
    explicit_segments = targeted_search_contract.extract_explicit_segments(query, body)
    people_first_persona = targeted_search_contract.build_target_persona(
        query=query,
        body=body,
        product=product,
        product_focus=product_focus,
    )
    # When the operator names a scene or role, deterministic operator intent
    # outranks provider prose.  This prevents a product-heavy LLM response from
    # replacing "who to find" with a SKU/specification summary.
    target_persona = _text(
        people_first_persona
        if explicit_segments
        else (
            raw_plan.get("target_persona")
            or raw_plan.get("audience")
            or people_first_persona
            or fallback["target_persona"]
        )
    )
    # 双目标意图契约:默认按「产品能力 + 使用场景」找潜在使用者;只有
    # existing_evidence 保留品牌/型号锚。操作员明确行业由 V2 QueryCell 再次锁定。
    anchor = smart_query_intent.product_anchor(product, query_text=query)
    objective = targeted_search_contract.normalize_objective(body, raw_plan)
    query_anchor = (
        anchor
        if objective == targeted_search_contract.EXISTING_EVIDENCE
        else smart_query_intent.product_anchor(None)
    )
    angles = smart_query_intent.angle_terms(
        [*_as_list(raw_plan.get("search_queries")), *product_focus, search_query], anchor
    )
    search_queries = smart_query_intent.build_search_queries(query_anchor, angles)
    compat_query = smart_query_intent.compat_search_query(
        search_queries, query_anchor, extra_terms=angles
    )
    audience = smart_query_intent.normalise_audience_scale(
        raw_plan.get("audience_scale"),
        raw_plan.get("min_followers_hint") or raw_plan.get("followers_min_hint"),
        detected=smart_query_intent.detect_audience_scale(query),
    )
    plan = {
        "status": "ready" if raw_plan else fallback["status"],
        "original_query": _text(query),
        "search_query": compat_query or search_query,
        "search_queries": search_queries or [compat_query or search_query],
        "objective": objective,
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
        "reason": _text(
            raw_plan.get("reason")
            or (
                "planner_parse_failed"
                if response_diagnostics["planner_parse_failed"]
                else fallback["reason"]
            )
        ),
        "provider": _text(response.get("provider") or fallback["provider"]),
        "model": _text(response.get("model") or fallback["model"]),
        "fallback_used": bool(response.get("fallback_used")) or not bool(raw_plan),
        **response_diagnostics,
        # 解析到的真实产品(纯展示/审计;无匹配为 None)。绝不参与任何评分。
        "resolved_product": planner_product_projection(product) if product else None,
    }
    # 车道「模型提议筛选」:五项筛选提议(国家/语言/垂类/粉丝下限/平台)。
    # 模型的 filter_proposal 只提供**取值**;「是不是操作员明确要求的」由原话规则判定,
    # 模型无权自封 —— 否则自动松绑车道会被它一句话冻死。
    plan["filter_proposal"] = smart_query_facets.propose_facets(query, plan, raw_plan=raw_plan)
    return targeted_search_contract.apply_targeted_contract(
        plan,
        query=query,
        body=body,
        product=product,
    )


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

    explicit_segments = targeted_search_contract.extract_explicit_segments(query, body)
    explicit_people_focus = [
        _text(segment.get("query_term"))
        for segment in explicit_segments
        if _text(segment.get("query_term"))
    ]
    ideal_persona = _text(persona.get("ideal_persona"))
    creator_types = _as_list(persona.get("ideal_creator_types_json"))
    verticals = _as_list(persona.get("verticals_json"))
    avoid_types = _as_list(persona.get("avoid_types_json"))
    what_is = _text(persona.get("what_is"))
    # search_query / product_focus:理想创作者类型 + 代表垂类(英文检索词,与 LLM 口径一致)。
    review_focus = (
        _fallback_keywords(_text(query).lower())
        if any(term in _text(query).lower() for term in ("评测", "测评", "review"))
        else []
    )
    focus = list(dict.fromkeys(
        explicit_people_focus
        if explicit_segments
        else [*review_focus, *creator_types, *verticals]
    ))
    if not focus:
        # persona 行存在但类型/垂类为空 → 退 LLM,避免空检索词。
        return None
    people_first_persona = targeted_search_contract.build_target_persona(
        query=query,
        body=body,
        product=product,
        product_focus=focus,
    )
    target_persona = people_first_persona if explicit_segments else (ideal_persona or people_first_persona)
    if explicit_segments:
        # A catalog persona is an inference about likely buyers. It cannot
        # exclude or demote a role/scene the operator explicitly requested.
        avoid_types = []
    product_positioning = what_is or _text(product.get("specs_line"))

    # persona 只提供潜在人群方向;默认目标不把品牌/型号塞回查询。
    anchor = smart_query_intent.product_anchor(product, query_text=query)
    objective = targeted_search_contract.normalize_objective(body)
    query_anchor = (
        anchor
        if objective == targeted_search_contract.EXISTING_EVIDENCE
        else smart_query_intent.product_anchor(None)
    )
    angles = smart_query_intent.angle_terms(focus, anchor)
    search_queries = smart_query_intent.build_search_queries(query_anchor, angles)
    search_query = smart_query_intent.compat_search_query(
        search_queries, query_anchor, extra_terms=angles
    ) or " ".join(focus).strip()
    audience = smart_query_intent.normalise_audience_scale(
        None, None, detected=smart_query_intent.detect_audience_scale(query)
    )

    fallback = _fallback_plan(query, body=body, product=product)
    plan = {
        "status": "ready",
        "original_query": _text(query),
        "search_query": search_query or fallback["search_query"],
        "search_queries": search_queries or [search_query or fallback["search_query"]],
        "objective": objective,
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
        **_planner_not_attempted_diagnostics(),
        "persona_source": (
            "operator_people_intent_with_product_capability"
            if explicit_segments
            else (_text(persona.get("source")) or "llm_persona_v1")
        ),
        "resolved_product": planner_product_projection(product),
    }
    # persona 路径同样给五项筛选提议:产品知识库只解决「找什么人」,
    # 「按什么筛」照旧全部标成推断项,人不够时由自动松绑车道先松这些。
    plan["filter_proposal"] = smart_query_facets.propose_facets(query, plan)
    return targeted_search_contract.apply_targeted_contract(
        plan,
        query=query,
        body=body,
        product=product,
    )


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
        return _require_evidence_anchor(
            _fallback_plan(query_text, reason="empty_query", body=body)
        )

    if _needs_people_clarification(query_text, body):
        return _people_clarification_plan(query_text, body=body)

    resolved_product, clarification = _resolve_requested_product(query_text, body)
    if clarification:
        return _clarification_plan(
            query_text,
            clarification,
            body=body,
            product=resolved_product,
        )
    if not resolved_product:
        return _require_evidence_anchor(
            _fallback_plan(query_text, reason="provider_free_initial", body=body)
        )

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
        body,
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
        return _fallback_plan(query_text, reason="empty_query", body=body)
    if _needs_people_clarification(query_text, body):
        return _people_clarification_plan(query_text, body=body)
    if str(body.get("use_llm_planner", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return _fallback_plan(query_text, reason="llm_planner_disabled", body=body)

    # 第一轮:把 query 里提到的产品(epic 65macro / 550pro / Z1pro / SKU…)模糊匹配到
    # 真实 vkpi_products,取真 SKU + 营销名 + 类别 + 价格 + 描述。匹配到就注入 LLM prompt,
    # 让第一轮 LLM 据真实 specs 深度分析产品定位与人群,而非对着裸 query 产泛词。读取失败不崩。
    resolved_product, clarification = _resolve_requested_product(query_text, body)
    if clarification:
        return _clarification_plan(
            query_text,
            clarification,
            body=body,
            product=resolved_product,
        )

    # 收口路①-1:解析到 SKU 后,优先读已填充的产品知识库 persona(地基A)。命中即直接拿
    # ideal_persona / avoid_types / ideal_creator_types / verticals,跳过 on-the-fly LLM(更稳更快、
    # 与批跑 369 SKU 口径一致)。无 persona(尚未填充)则照常走下面的 LLM planner。
    # 红线:只读 LLM 生成的 persona 文本/标签,零写 fit、零改 rule_v0。读失败静默回退 LLM。
    if resolved_product and str(resolved_product.get("sku") or "").strip():
        persona_plan = _plan_from_product_persona(query_text, resolved_product, body)
        if persona_plan is not None:
            return persona_plan

    prompt = smart_query_planner_prompt.build_prompt(
        query_text,
        resolved_product=resolved_product,
        body=body,
    )
    response = llm_gateway.invoke_json(
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
        required_keys=PLANNER_REQUIRED_KEYS,
        validator=_validate_planner_json_contract,
    )
    if not isinstance(response, dict):
        response = {
            "status": "invalid_response",
            "provider": "rule_v0",
            "model": "rule_v0",
            "fallback_used": True,
            "errors": [
                {
                    "status": "validation_failure",
                    "error": "planner gateway returned a non-object response",
                }
            ],
        }
    else:
        response = dict(response)
    structured = response.get("json")
    # Compatibility for existing gateway/provider mocks that still return the
    # historical ``text`` envelope.  Production uses invoke_json's validated
    # ``json`` value; the fallback decoder is never used to repair invalid JSON.
    parsed = structured if isinstance(structured, dict) else _extract_json(
        _text(response.get("text"))
    )
    contract_valid, contract_error = _validate_planner_json_contract(parsed)
    if not contract_valid:
        parsed = {}
        response_errors = response.get("errors")
        errors = list(response_errors) if isinstance(response_errors, list) else []
        if _text(response.get("status")).lower() == "success" and not any(
            isinstance(item, dict)
            and _text(item.get("status")).lower()
            in {"parse_failure", "validation_failure", "empty_response"}
            for item in errors
        ):
            errors.append(
                {
                    "provider": _text(response.get("provider")),
                    "model": _text(response.get("model")),
                    "status": "validation_failure",
                    "error": contract_error,
                }
            )
            response["errors"] = errors
    diagnostics = _planner_response_diagnostics(response, parsed)
    if diagnostics["planner_parse_failed"]:
        logger.warning(
            "vkpi.kol.smart_query_planner.parse_failed",
            extra={
                "provider": _text(response.get("provider")) or "unknown",
                "model": _text(response.get("model")) or "unknown",
                "provider_attempts": diagnostics["provider_attempts"],
                "provider_response_status": diagnostics["provider_response_status"],
            },
        )
    return _normalise_plan(query_text, parsed, response, resolved_product, body)


# ── Wave2-#4 规划缓存(2026-07-02):同 query 7 天内直接回缓存,检索同步段 8s -> <0.1s。
# 键=md5(归一化 query);v2 使目录闸上线前缓存的错误产品计划立即失效。
# fallback 计划(带 reason)不缓存;读写任何异常都静默回退真算。红线:不触 viltrox_fit_score。
def plan_text_query(
    query: str,
    *,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = body or {}
    if _needs_people_clarification(_text(query), body):
        # Guard before cache lookup so a stale seven-day product-centric plan
        # cannot bypass the current people-intent clarification contract.
        return _people_clarification_plan(_text(query), body=body)
    return plan_text_query_cached(
        query,
        body=body,
        staff=staff,
        derive_method=PLAN_DERIVE_METHOD,
        build_plan=_plan_text_query_impl,
        logger=logger,
    )
