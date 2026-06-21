"""R3 · 成本估算 + 风险合成(确定性 · 零 LLM · 零触 viltrox_fit_score / rule_v0)。

estimate_cost_for_kols(kol_pool_ids, staff=None) → 给一组候选 KOL 估「合作预算区间」+ 合成
「风险画像」,供 R2 项目草案 / R4 话术与 SOW 草案使用。

费率:纯估算(per-post,USD），按粉丝量级档 × 平台查费率卡,给 low/high 区间——明确是「估算」
而非「报价」(红线4:Agent 不自动承诺价格)。
风险:只读「展示信号」(suspect_inflation / 缺联系方式 / 低互动 / 缺粉丝数 / 数据陈旧),
绝不读写 viltrox_fit_score、绝不并入 fit(红线1:relevance/exposure/risk 是独立展示信号)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn


# 粉丝量级档(下界,含):nano < micro < mid < macro < mega。
_TIERS: tuple[tuple[str, int], ...] = (
    ("mega", 1_000_000),
    ("macro", 200_000),
    ("mid", 50_000),
    ("micro", 10_000),
    ("nano", 0),
)

# 费率卡:per-post 估算(USD 元,(low, high))。平台未命中走 default。
# 数值为行业大致区间(2025),仅作内部预算估算,非对外报价。
_RATE_CARD_USD: dict[str, dict[str, tuple[int, int]]] = {
    "youtube": {
        "nano": (75, 200), "micro": (200, 1000), "mid": (1000, 4000),
        "macro": (4000, 15000), "mega": (15000, 50000),
    },
    "tiktok": {
        "nano": (50, 150), "micro": (150, 600), "mid": (600, 2500),
        "macro": (2500, 10000), "mega": (10000, 40000),
    },
    "instagram": {
        "nano": (50, 150), "micro": (150, 700), "mid": (700, 3000),
        "macro": (3000, 12000), "mega": (12000, 45000),
    },
    "default": {
        "nano": (50, 150), "micro": (150, 600), "mid": (600, 2500),
        "macro": (2500, 9000), "mega": (9000, 35000),
    },
}

# 风险旗权重(单 KOL 命中即累加,封顶 100)。
_FLAG_WEIGHTS: dict[str, int] = {
    "suspect_inflation": 40,   # 疑似刷量(113 列)——最重
    "missing_followers": 20,   # 无粉丝数 → 费率/价值都难估
    "missing_contact": 15,     # 无邮箱/联系方式 → 触达难
    "low_engagement": 15,      # 互动率过低
    "stale_data": 10,          # 资料陈旧(>90 天未见)
}
_STALE_DAYS = 90
_LOW_ENGAGEMENT = 0.01  # 1%(engagement_rate 以比值存储,>1 视作百分数已在 pool normalize)


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tier_for_followers(followers: int | None) -> str | None:
    if followers is None or followers < 0:
        return None
    for name, lower in _TIERS:
        if followers >= lower:
            return name
    return "nano"


def _platform_key(platform: Any) -> str:
    p = str(platform or "").strip().lower()
    if "youtube" in p or p == "yt":
        return "youtube"
    if "tiktok" in p or p == "tt":
        return "tiktok"
    if "instagram" in p or p in {"ig", "insta"}:
        return "instagram"
    return "default"


def _has_contact(email: Any, other_contacts_json: Any) -> bool:
    if str(email or "").strip():
        return True
    try:
        parsed = other_contacts_json if isinstance(other_contacts_json, list) else json.loads(other_contacts_json or "[]")
    except Exception:
        parsed = []
    return bool(isinstance(parsed, list) and len(parsed) > 0)


def _age_days(last_seen_at: Any) -> int | None:
    if not isinstance(last_seen_at, datetime):
        return None
    dt = last_seen_at if last_seen_at.tzinfo else last_seen_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return max(0, int(delta.total_seconds() // 86400))


def _risk_level(score: int) -> str:
    if score < 25:
        return "low"
    if score < 55:
        return "medium"
    return "high"


def estimate_cost_for_kols(
    kol_pool_ids: list[Any],
    *,
    staff: dict[str, Any] | None = None,
    posts_per_creator: int = 1,
) -> dict[str, Any]:
    """估算一组候选 KOL 的合作预算区间 + 合成风险画像。

    posts_per_creator:每位创作者计划合作篇数(默认 1),线性乘进费率。
    返回 total_cents / per_creator / by_platform / risk;missing_kol_pool_ids 诚实回报。
    """
    del staff  # 估算只读公共候选数据,不做按人 scope(预算口径全局一致)。

    ids: list[int] = []
    seen: set[int] = set()
    for value in kol_pool_ids or []:
        kid = _int_or_none(value)
        if kid and kid not in seen:
            seen.add(kid)
            ids.append(kid)

    base = {
        "currency": "USD",
        "is_estimate": True,
        "posts_per_creator": max(1, int(posts_per_creator or 1)),
        "creator_count": 0,
        "total_cents": {"low": 0, "high": 0, "mid": 0},
        "per_creator": [],
        "by_platform": {},
        "by_tier": {},
        "risk": {"score": 0, "level": "low", "flags_count": {}, "creators_with_risk": 0},
        "missing_kol_pool_ids": [],
        "note": "费率为内部预算估算(per-post 行业区间 × 篇数),非对外报价;风险为独立展示信号,绝不并入 V6 Fit。",
    }
    if not ids:
        return base

    ppc = max(1, int(posts_per_creator or 1))
    conn = get_conn()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT id, platform, followers, engagement_rate, email, other_contacts_json,
               suspect_inflation, last_seen_at
        FROM vkpi_kol_pool
        WHERE id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    found = {int(dict(r)["id"]): dict(r) for r in rows}
    missing = [kid for kid in ids if kid not in found]

    total_low = total_high = 0
    per_creator: list[dict[str, Any]] = []
    by_platform: dict[str, dict[str, int]] = {}
    by_tier: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    risk_scores: list[int] = []
    creators_with_risk = 0

    for kid in ids:
        row = found.get(kid)
        if not row:
            continue
        platform_key = _platform_key(row.get("platform"))
        followers = _int_or_none(row.get("followers"))
        tier = _tier_for_followers(followers)
        # 缺粉丝数 → 无法定档:费率按 micro 估(标 assumed_tier)以免算成 0;同时记 missing_followers 风险。
        eff_tier = tier or "micro"
        low_usd, high_usd = _RATE_CARD_USD.get(platform_key, _RATE_CARD_USD["default"])[eff_tier]
        fee_low = low_usd * 100 * ppc
        fee_high = high_usd * 100 * ppc
        total_low += fee_low
        total_high += fee_high

        by_tier[eff_tier] = by_tier.get(eff_tier, 0) + 1
        bp = by_platform.setdefault(platform_key, {"count": 0, "low": 0, "high": 0})
        bp["count"] += 1
        bp["low"] += fee_low
        bp["high"] += fee_high

        # ── 风险旗(只读展示信号) ──
        flags: list[str] = []
        if bool(row.get("suspect_inflation")):
            flags.append("suspect_inflation")
        if followers is None:
            flags.append("missing_followers")
        if not _has_contact(row.get("email"), row.get("other_contacts_json")):
            flags.append("missing_contact")
        eng = _float_or_none(row.get("engagement_rate"))
        if eng is not None and followers and followers > 0 and eng < _LOW_ENGAGEMENT:
            flags.append("low_engagement")
        age = _age_days(row.get("last_seen_at"))
        if age is None or age > _STALE_DAYS:
            flags.append("stale_data")

        creator_risk = min(100, sum(_FLAG_WEIGHTS.get(f, 0) for f in flags))
        risk_scores.append(creator_risk)
        if flags:
            creators_with_risk += 1
        for f in flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1

        per_creator.append({
            "kol_pool_id": kid,
            "platform": platform_key,
            "followers": followers,
            "tier": tier,
            "assumed_tier": None if tier else eff_tier,
            "fee_cents": {"low": fee_low, "high": fee_high},
            "risk_score": creator_risk,
            "risk_flags": flags,
        })

    creator_count = len(per_creator)
    overall_risk = round(sum(risk_scores) / creator_count) if creator_count else 0

    return {
        **base,
        "creator_count": creator_count,
        "total_cents": {
            "low": total_low,
            "high": total_high,
            "mid": round((total_low + total_high) / 2),
        },
        "per_creator": per_creator,
        "by_platform": by_platform,
        "by_tier": by_tier,
        "risk": {
            "score": overall_risk,
            "level": _risk_level(overall_risk),
            "flags_count": flag_counts,
            "creators_with_risk": creators_with_risk,
        },
        "missing_kol_pool_ids": missing,
    }
