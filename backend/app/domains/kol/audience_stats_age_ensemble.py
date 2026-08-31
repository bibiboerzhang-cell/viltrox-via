"""Per-profile signal fusion for the audience age ensemble."""
from __future__ import annotations

from typing import Any, Callable


def apply_age_signals(
    profile: dict[str, Any],
    *,
    llm_predictions: dict[str, dict[str, Any]],
    m3_predictions: dict[str, dict[str, Any]],
    avatar_predictions: dict[str, dict[str, Any]],
    counts: dict[str, int],
    channel_age: Callable[[Any], tuple[str, float]],
    handle_age: Callable[..., tuple[str, float]],
    fuse_age: Callable[[list[tuple[str, float]]], tuple[str, float]],
) -> bool:
    """Fuse one uncached profile in place and return whether it changed."""
    key = str(profile.get("author_key") or "")
    signals: list[tuple[str, float]] = []
    llm = llm_predictions.get(key)
    if llm and llm.get("age_bucket"):
        signals.append((llm["age_bucket"], float(llm.get("conf") or 0.55)))
        counts["llm"] += 1
    m3 = m3_predictions.get(key)
    if m3 and m3.get("age_bucket"):
        signals.append((m3["age_bucket"], float(m3.get("conf") or 0.5)))
        counts["m3"] += 1
    channel_bucket, channel_confidence = channel_age(profile.get("channel_created_at"))
    if channel_bucket:
        signals.append((channel_bucket, channel_confidence))
        counts["channel"] += 1
    handle_bucket, handle_confidence = handle_age(profile.get("author_key"), profile.get("display_name"))
    if handle_bucket:
        signals.append((handle_bucket, handle_confidence))
        counts["handle_year"] += 1
    avatar = avatar_predictions.get(key)
    if avatar and avatar.get("age_bucket"):
        signals.append((avatar["age_bucket"], float(avatar.get("conf") or 0.5)))
        counts["avatar"] += 1

    changed = False
    bucket, confidence = fuse_age(signals)
    if bucket:
        profile["age_bucket"], profile["age_conf"] = bucket, confidence
        counts["fused"] += 1
        changed = True
    if llm and llm.get("gender") in ("male", "female") and float(llm.get("conf") or 0) > float(profile.get("gender_conf") or 0):
        profile["gender"], profile["gender_conf"] = llm["gender"], round(float(llm.get("conf") or 0), 2)
        changed = True
    if avatar and avatar.get("gender") in ("male", "female") and float(avatar.get("conf") or 0) > float(profile.get("gender_conf") or 0):
        profile["gender"], profile["gender_conf"] = avatar["gender"], round(float(avatar.get("conf") or 0), 2)
        changed = True
    return changed
