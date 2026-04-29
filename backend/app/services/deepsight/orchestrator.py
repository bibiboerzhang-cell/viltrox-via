from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.schemas.deepsight import DeepSightRequest
from app.services.deepsight.cache import cache
from app.services.deepsight.evidence_pack import build_evidence_pack
from app.services.deepsight.emotional import build_emotional_layer
from app.services.deepsight.tiering import decide_tier, ttl_for_tier
from app.services.deepsight.parallel_scan import scan_accounts_concurrently
from app.services.deepsight.triad import run_triad


def _cache_key(request: DeepSightRequest) -> str:
    return "|".join([
        request.brand,
        request.scope,
        str(request.days),
        str(request.previous_days or request.days),
        ",".join(sorted(request.platforms)),
        request.model_mode,
        str(bool(request.scan_data)),
    ])


def _fast_diagnosis(pack: dict) -> dict:
    platform_breakdown = pack.get("platform_breakdown", [])
    account_breakdown = pack.get("account_breakdown", [])
    product_breakdown = pack.get("product_breakdown", [])
    comments = pack.get("comment_analysis", {})
    risks = pack.get("risk_flags", [])
    opportunities = pack.get("opportunities", [])

    strongest = platform_breakdown[0] if platform_breakdown else None
    weakest = min(platform_breakdown, key=lambda x: x.get("wow_views_change", 0)) if platform_breakdown else None
    top_product = product_breakdown[0] if product_breakdown else None

    if risks and len([r for r in risks if r.get("severity") == "critical"]) >= 2:
        health = "critical"
    elif risks:
        health = "warning"
    else:
        health = "good"

    one_liner_parts = []
    if strongest:
        one_liner_parts.append(f"{strongest['platform']} 表现最强")
    if weakest and weakest is not strongest:
        one_liner_parts.append(f"{weakest['platform']} 需要修正")
    if top_product:
        one_liner_parts.append(f"{top_product['product']} 是当前热度核心")
    if comments.get("negative_keywords"):
        one_liner_parts.append(f"评论问题集中在 {', '.join(comments['negative_keywords'][:2])}")
    one_liner = "，".join(one_liner_parts) or "当前证据不足，建议扩大样本后再判断。"

    actions = []
    if risks:
        actions.append({
            "priority": "urgent",
            "owner": "ops",
            "text": f"先处理最高风险项：{risks[0]['target']}，原因：{risks[0]['evidence']}",
            "expected_impact": "先止损，再决定是否扩大投放或发分。",
        })
    if strongest:
        actions.append({
            "priority": "this_week",
            "owner": "content",
            "text": f"继续扩展 {strongest['platform']} 的强势内容类型，优先复用高效率账号模板。",
            "expected_impact": "放大当前最有效的平台表现。",
        })
    if top_product:
        actions.append({
            "priority": "ongoing",
            "owner": "brand",
            "text": f"围绕 {top_product['product']} 继续做 {', '.join(top_product.get('top_topics', [])[:2]) or '核心卖点'} 内容。",
            "expected_impact": "延续产品热度并验证需求方向。",
        })
    emotional = build_emotional_layer(comments, risks)
    return {
        "one_liner": one_liner,
        "overall_health": health,
        "platform_diagnosis": platform_breakdown[:5],
        "account_diagnosis": account_breakdown[:5],
        "comment_insight": comments,
        "product_insight": product_breakdown[:5],
        "actions": actions,
        "split_vote": False,
        "council_views": {},
        "emotional_layer": emotional,
    }


async def analyze_request(request: DeepSightRequest) -> dict[str, Any]:
    started = time.time()
    tier = decide_tier(request)
    key = _cache_key(request)
    if not request.refresh:
        hit = cache.get(key)
        if hit is not None:
            return {
                "status": "success",
                "tier": tier,
                "cache_hit": True,
                "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "elapsed_sec": round(time.time() - started, 3),
                **hit,
            }

    if request.scan_accounts and not request.scan_data:
        scan_data = await scan_accounts_concurrently(
            accounts=[a.model_dump() for a in request.scan_accounts],
            max_posts_per_account=request.max_posts_per_account,
            concurrency=request.concurrency,
        )
        request = request.model_copy(update={"scan_data": scan_data})

    pack = await build_evidence_pack(request)
    if request.model_mode == "triad" or tier == "tier2":
        council = await run_triad(pack)
        diagnosis = _fast_diagnosis(pack)
        diagnosis["council_views"] = council
        diagnosis["split_vote"] = bool(council.get("split_vote"))
    else:
        diagnosis = _fast_diagnosis(pack)

    payload = {"evidence_pack": pack, "diagnosis": diagnosis}
    cache.set(key, payload, ttl_for_tier(tier))
    return {
        "status": "success",
        "tier": tier,
        "cache_hit": False,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_sec": round(time.time() - started, 3),
        **payload,
    }
