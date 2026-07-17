"""#21 市场热词聚合(实时,不建快照表)。

读取三路近期证据:行业帖 hashtag(``vkpi_industry_posts``)、带来源引用的
``vkpi_market_observations``、市场监听帖(``vkpi_market_mentions``,Reddit/X
采集落表),后两路复用统一市场词表提取品牌/品类词。
各来源始终分别计数并回传,绝不把内部生成时间伪装成原帖发布时间。
数据稀疏时如实返回少量;所有来源都无命中才返回空 trends。
红线:纯聚合,零 viltrox_fit_score / rule_v0 触点。占位符 '?'(方言层翻译)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.market.signal_taxonomy import keyword_hits


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


def _row_dict(row: Any) -> dict[str, Any]:
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _evidence_url(raw: Any) -> str:
    if not raw:
        return ""
    try:
        refs = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return ""
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        candidate = str(
            ref.get("url")
            or ref.get("source_url")
            or (ref.get("raw") or {}).get("source_url")
            or ""
        ).strip()
        if candidate.startswith(("http://", "https://")):
            return candidate
    return ""


def _bucket(agg: dict[str, dict[str, Any]], term: str) -> dict[str, Any]:
    return agg.setdefault(
        term,
        {
            "evidence_count": 0,
            "engagement": 0,
            "sources": set(),
            "platforms": set(),
            "kinds": set(),
            "sample_refs": [],
            "newest_at": "",
        },
    )


def _add_evidence(
    agg: dict[str, dict[str, Any]],
    *,
    term: str,
    source: str,
    platform: str = "",
    kind: str = "",
    engagement: int = 0,
    observed_at: str = "",
    ref: dict[str, Any] | None = None,
) -> None:
    clean = str(term or "").strip().lstrip("#").lower()
    if not clean:
        return
    current = _bucket(agg, clean)
    current["evidence_count"] += 1
    current["engagement"] += max(0, int(engagement or 0))
    if source:
        current["sources"].add(source)
    if platform:
        current["platforms"].add(platform)
    if kind:
        current["kinds"].add(kind)
    if observed_at and observed_at > current["newest_at"]:
        current["newest_at"] = observed_at
    if ref and len(current["sample_refs"]) < 3:
        current["sample_refs"].append(ref)


def build_hashtag_trends_v0(limit: int = 12, days: int = 14, platform: str = "") -> dict[str, Any]:
    """Aggregate unique market terms across source-backed recent evidence."""
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    agg: dict[str, dict[str, Any]] = {}
    source_rows = {"industry_posts": 0, "market_observations": 0, "market_mentions": 0}

    where = ["COALESCE(published_at, created_at) >= ?"]
    params: list[Any] = [cutoff]
    plat = (platform or "").strip().lower()
    if plat:
        where.append("LOWER(COALESCE(platform,'')) = ?")
        params.append(plat)
    if table_exists("vkpi_industry_posts"):
        rows = conn.execute(
            f"""
            SELECT id, LOWER(COALESCE(platform,'')) AS platform, hashtags_json,
                   COALESCE(views,0) AS views, COALESCE(likes,0) AS likes,
                   COALESCE(comments,0) AS comments, post_url,
                   COALESCE(published_at, created_at) AS observed_at
            FROM vkpi_industry_posts
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        ).fetchall()
        source_rows["industry_posts"] = len(rows)
        for row in rows:
            data = _row_dict(row)
            plat_v = str(data.get("platform") or "")
            eng = int(data.get("views") or 0) + int(data.get("likes") or 0) + int(data.get("comments") or 0)
            for tag in set(_parse_tags(data.get("hashtags_json"))):
                _add_evidence(
                    agg,
                    term=tag,
                    source="industry_posts",
                    platform=plat_v,
                    kind="hashtag",
                    engagement=eng,
                    observed_at=str(data.get("observed_at") or ""),
                    ref={"table": "vkpi_industry_posts", "id": data.get("id"), "url": str(data.get("post_url") or "")},
                )

    # ``platform`` filters raw social posts only. Market observations are a
    # cross-platform learned evidence layer, so a requested platform must not
    # silently mix them into a platform-specific result.
    if not plat and table_exists("vkpi_market_observations"):
        observation_rows = conn.execute(
            """
            SELECT id, topic, kind, source, evidence_refs, suggested_action,
                   COALESCE(generated_at, updated_at) AS observed_at
            FROM vkpi_market_observations
            WHERE COALESCE(generated_at, updated_at) >= ?
            """,
            (cutoff,),
        ).fetchall()
        source_rows["market_observations"] = len(observation_rows)
        for row in observation_rows:
            data = _row_dict(row)
            terms = keyword_hits(f"{data.get('topic') or ''} {data.get('suggested_action') or ''}")
            for term in set(terms):
                _add_evidence(
                    agg,
                    term=term,
                    source="market_observations",
                    platform="cross_platform",
                    kind=str(data.get("kind") or "observation"),
                    observed_at=str(data.get("observed_at") or ""),
                    ref={
                        "table": "vkpi_market_observations",
                        "id": data.get("id"),
                        "url": _evidence_url(data.get("evidence_refs")),
                    },
                )

    # 市场监听帖(Reddit/X 采集,listening_executors 落表):标题+摘要过统一词表。
    # mentions 行带 platform 列,请求平台过滤时按同口径参与(与行业帖一致)。
    if table_exists("vkpi_market_mentions") and table_exists("vkpi_market_sources"):
        mention_where = ["m.created_at >= ?"]
        mention_params: list[Any] = [cutoff]
        if plat:
            mention_where.append("LOWER(COALESCE(m.platform,'')) = ?")
            mention_params.append(plat)
        mention_rows = conn.execute(
            f"""
            SELECT m.id, LOWER(COALESCE(m.platform,'')) AS platform, m.mention_text,
                   m.metadata_json, m.created_at,
                   COALESCE(s.source_url, '') AS source_url
            FROM vkpi_market_mentions m
            LEFT JOIN vkpi_market_sources s ON s.id = m.source_id
            WHERE {' AND '.join(mention_where)}
            """,
            tuple(mention_params),
        ).fetchall()
        source_rows["market_mentions"] = len(mention_rows)
        for row in mention_rows:
            data = _row_dict(row)
            meta: dict[str, Any] = {}
            try:
                raw_meta = data.get("metadata_json")
                parsed = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                meta = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                meta = {}
            engagement = 0
            for key in ("score", "num_comments", "likes", "retweets", "replies", "quotes"):
                try:
                    engagement += max(0, int(meta.get(key) or 0))
                except (TypeError, ValueError):
                    continue
            observed = str(meta.get("published_at") or data.get("created_at") or "")
            url = str(meta.get("url") or meta.get("source_url") or data.get("source_url") or "")
            for term in set(keyword_hits(str(data.get("mention_text") or ""))):
                _add_evidence(
                    agg,
                    term=term,
                    source="market_mentions",
                    platform=str(data.get("platform") or ""),
                    kind="keyword",
                    engagement=engagement,
                    observed_at=observed,
                    ref={"table": "vkpi_market_mentions", "id": data.get("id"), "url": url},
                )

    trends = []
    for term, value in agg.items():
        sources = sorted(value["sources"])
        platforms = sorted(value["platforms"])
        trends.append(
            {
                "hashtag": term,
                "display_term": term,
                "post_count": int(value["evidence_count"]),
                "evidence_count": int(value["evidence_count"]),
                "engagement": int(value["engagement"]),
                "platform": platforms[0] if len(platforms) == 1 else "multi",
                "platforms": platforms,
                "sources": sources,
                "source_count": len(sources),
                "kinds": sorted(value["kinds"]),
                "sample_refs": value["sample_refs"],
                "newest_at": value["newest_at"] or None,
            }
        )
    trends.sort(
        key=lambda trend: (
            trend["evidence_count"],
            trend["source_count"],
            trend["engagement"],
            trend["hashtag"],
        ),
        reverse=True,
    )
    trends = trends[: max(1, min(50, int(limit)))]
    for idx, t in enumerate(trends):
        t["rank"] = idx + 1
    active_sources = [source for source, count in source_rows.items() if count > 0]
    source_labels = {
        "industry_posts": "行业帖",
        "market_observations": "市场观测",
        "market_mentions": "市场监听",
    }
    source_label = " + ".join(source_labels[source] for source in active_sources)
    return {
        "trends": trends,
        "summary": {
            "total_hashtags": len(agg),
            "unique_terms": len(agg),
            "evidence_rows": sum(source_rows.values()),
            "source_rows": source_rows,
            "source_count": len(active_sources),
            "sources": active_sources,
            "source": "multi_source" if len(active_sources) > 1 else (active_sources[0] if active_sources else "none"),
            "source_label": source_label or "暂无来源",
            "window_days": int(days),
            "top": trends[0]["hashtag"] if trends else "",
            "status": "ready" if trends else "empty",
            "claim_status": "descriptive_only",
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
