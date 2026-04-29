from __future__ import annotations

from collections import defaultdict


def detect_risk_flags(platform_breakdown: list[dict], account_breakdown: list[dict], comment_analysis: dict, product_breakdown: list[dict]) -> list[dict]:
    flags: list[dict] = []
    for p in platform_breakdown:
        if p.get("wow_views_change", 0) <= -0.25:
            flags.append({
                "type": "platform_drop",
                "target": p["platform"],
                "evidence": f"7天播放下降 {p['wow_views_change']:.0%}",
                "severity": "warning",
            })
        if p.get("engagement_rate", 0) <= 0.01 and p.get("views", 0) >= 10000:
            flags.append({
                "type": "low_quality_traffic",
                "target": p["platform"],
                "evidence": f"高播放但互动率仅 {p['engagement_rate']:.2%}",
                "severity": "critical",
            })
    for a in account_breakdown:
        if a.get("negative_comment_ratio", 0) >= 0.25:
            flags.append({
                "type": "comment_risk",
                "target": f"{a['platform']} / {a['handle']}",
                "evidence": f"负面评论占比 {a['negative_comment_ratio']:.0%}",
                "severity": "warning",
            })
        if a.get("wow_change", 0) <= -0.35:
            flags.append({
                "type": "account_drop",
                "target": f"{a['platform']} / {a['handle']}",
                "evidence": f"平均播放下降 {a['wow_change']:.0%}",
                "severity": "critical",
            })
    if comment_analysis.get("crisis_ratio", 0) >= 0.08:
        flags.append({
            "type": "crisis_language",
            "target": "comments",
            "evidence": "评论中出现较多退款/质量/骗局类关键词",
            "severity": "critical",
        })
    for p in product_breakdown:
        if p.get("wow_mentions_change", 0) >= 0.25 and p.get("negative_ratio", 0) >= 0.3:
            flags.append({
                "type": "hot_but_risky_product",
                "target": p["product"],
                "evidence": f"提及增长 {p['wow_mentions_change']:.0%}，但负面比例 {p['negative_ratio']:.0%}",
                "severity": "warning",
            })
    return flags


def detect_opportunities(product_breakdown: list[dict], account_breakdown: list[dict], comment_analysis: dict) -> list[dict]:
    opps: list[dict] = []
    for p in product_breakdown:
        if p.get("wow_mentions_change", 0) >= 0.2 and p.get("positive_ratio", 0) >= 0.5:
            opps.append({
                "type": "product_momentum",
                "target": p["product"],
                "evidence": f"提及增长 {p['wow_mentions_change']:.0%}，正面比例 {p['positive_ratio']:.0%}",
                "priority": "this_week",
            })
    for a in account_breakdown[:5]:
        if a.get("wow_change", 0) >= 0.2:
            opps.append({
                "type": "account_momentum",
                "target": f"{a['platform']} / {a['handle']}",
                "evidence": f"平均播放增长 {a['wow_change']:.0%}",
                "priority": "ongoing",
            })
    if comment_analysis.get("purchase_intent_ratio", 0) >= 0.15:
        opps.append({
            "type": "buy_signal",
            "target": "comments",
            "evidence": f"购买意图评论占比 {comment_analysis['purchase_intent_ratio']:.0%}",
            "priority": "urgent",
        })
    return opps


def compute_platform_stats(platform_breakdown: list[dict]) -> dict:
    if not platform_breakdown:
        return {}
    strongest = max(platform_breakdown, key=lambda x: (x.get("views", 0), x.get("engagement_rate", 0)))
    weakest = min(platform_breakdown, key=lambda x: x.get("wow_views_change", 0))
    return {
        "strongest_platform": strongest.get("platform"),
        "weakest_platform": weakest.get("platform"),
        "total_platforms": len(platform_breakdown),
    }
