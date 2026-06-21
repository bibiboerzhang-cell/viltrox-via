"""#21 Hashtag 趋势聚合(实时,不建快照表)—— 读 vkpi_industry_posts.hashtags_json。

数据稀疏(行业帖少)时如实返回少量;无数据返回空 trends(不编造)。随行业帖增长自动变密。
红线:纯聚合,零 viltrox_fit_score / rule_v0 触点。占位符 '?'(方言层翻译)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn


def _parse_tags(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        arr = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    out: list[str] = []
    for tag in (arr or []):
        s = str(tag or "").strip().lstrip("#").lower()
        if s:
            out.append(s)
    return out


def build_hashtag_trends_v0(limit: int = 12, days: int = 14, platform: str = "") -> dict[str, Any]:
    """近 days 天行业帖的 hashtag 聚合:按出现次数 + 互动量排序,出 top N。"""
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    where = ["COALESCE(published_at, created_at) >= ?"]
    params: list[Any] = [cutoff]
    plat = (platform or "").strip().lower()
    if plat:
        where.append("LOWER(COALESCE(platform,'')) = ?")
        params.append(plat)
    rows = conn.execute(
        f"""
        SELECT LOWER(COALESCE(platform,'')) AS platform, hashtags_json,
               COALESCE(views,0) AS views, COALESCE(likes,0) AS likes,
               COALESCE(comments,0) AS comments
        FROM vkpi_industry_posts
        WHERE {' AND '.join(where)}
        """,
        tuple(params),
    ).fetchall()
    agg: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        data = dict(row)
        plat_v = str(data.get("platform") or "")
        eng = int(data.get("views") or 0) + int(data.get("likes") or 0) + int(data.get("comments") or 0)
        for tag in _parse_tags(data.get("hashtags_json")):
            cur = agg.setdefault((plat_v, tag), {"post_count": 0, "engagement": 0})
            cur["post_count"] += 1
            cur["engagement"] += eng
    trends = [
        {"platform": key[0], "hashtag": key[1], "post_count": val["post_count"], "engagement": val["engagement"]}
        for key, val in agg.items()
    ]
    trends.sort(key=lambda t: (t["post_count"], t["engagement"]), reverse=True)
    trends = trends[: max(1, min(50, int(limit)))]
    for idx, t in enumerate(trends):
        t["rank"] = idx + 1
    return {
        "trends": trends,
        "summary": {
            "total_hashtags": len(agg),
            "window_days": int(days),
            "top": trends[0]["hashtag"] if trends else "",
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
