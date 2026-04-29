from __future__ import annotations


def build_emotional_layer(comment_analysis: dict, risk_flags: list[dict]) -> dict:
    pos = float(comment_analysis.get("positive_ratio", 0))
    neg = float(comment_analysis.get("negative_ratio", 0))
    crisis = float(comment_analysis.get("crisis_ratio", 0))
    buy = float(comment_analysis.get("purchase_intent_ratio", 0))

    mood_score = max(0, min(100, round((pos - neg) * 100 + 50)))
    if crisis >= 0.08:
        crisis_tone = "active"
    elif neg >= 0.2:
        crisis_tone = "watch"
    else:
        crisis_tone = "none"

    if buy >= 0.18:
        excitement = "high"
    elif buy >= 0.08:
        excitement = "rising"
    else:
        excitement = "flat"

    if mood_score >= 70:
        brand_mood = "optimistic"
    elif mood_score >= 55:
        brand_mood = "cautiously_optimistic"
    elif mood_score >= 40:
        brand_mood = "mixed"
    else:
        brand_mood = "fragile"

    return {
        "brand_mood": brand_mood,
        "brand_mood_score": mood_score,
        "user_emotion": excitement,
        "purchase_intent": round(buy, 4),
        "crisis_tone": crisis_tone,
        "negative_topics": comment_analysis.get("negative_keywords", []),
        "positive_topics": comment_analysis.get("positive_keywords", []),
        "risk_count": len(risk_flags),
    }
