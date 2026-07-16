"""
services/intelligence/market.py — Market intelligence with legacy-table compatibility.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform.llm_production import generate_text

logger = get_logger(__name__)

FOCAL_BUCKETS = ["24mm", "35mm", "50mm", "56mm", "85mm", "135mm"]
COMPETITOR_BRANDS = ["Sony", "Sigma", "Tamron", "Nikon", "Canon"]


def _brand_from_title(title: str) -> str:
    text = str(title or "").lower()
    if "viltrox" in text:
        return "Viltrox"
    for brand in COMPETITOR_BRANDS:
        if brand.lower() in text:
            return brand
    return "Other"


def _latest_bh_rows() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT title, price, rating, review_count, url, image_url, sku, snapshot_at
           FROM bh_products
           WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM bh_products)
           ORDER BY review_count DESC, rating DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def build_category_heatmap() -> dict:
    rows = _latest_bh_rows()
    segments = []
    for bucket in FOCAL_BUCKETS:
        viltrox_count = 0
        competitor_counts = {brand.lower(): 0 for brand in COMPETITOR_BRANDS}
        for row in rows:
            title = str(row.get("title") or "")
            if bucket not in title:
                continue
            brand = _brand_from_title(title)
            if brand == "Viltrox":
                viltrox_count += 1
            elif brand in COMPETITOR_BRANDS:
                competitor_counts[brand.lower()] += 1
        segments.append(
            {
                "focal": bucket,
                "viltrox_count": viltrox_count,
                "competitor_counts": competitor_counts,
                "status": _bucket_status(viltrox_count, competitor_counts),
            }
        )
    return {"segments": segments}


def _bucket_status(viltrox_count: int, competitor_counts: dict[str, int]) -> str:
    competitor_total = sum(competitor_counts.values())
    if viltrox_count == 0 and competitor_total <= 2:
        return "opp"
    if viltrox_count >= 2 and viltrox_count >= max(competitor_counts.values() or [0]):
        return "lead"
    if viltrox_count <= 1 and competitor_total >= 4:
        return "behind"
    return "mid"


def list_observations(
    *,
    kind: str | None = None,
    impact: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
) -> dict:
    conn = get_conn()
    cols = {
        str(row["name"] if isinstance(row, dict) else row[1])
        for row in conn.execute("PRAGMA table_info(market_observations)").fetchall()
    }
    legacy = "observation_type" in cols
    where, params = [], []
    if legacy:
        if kind:
            where.append("observation_type = ?")
            params.append(kind)
        if from_date:
            where.append("observed_at >= ?")
            params.append(from_date)
        if to_date:
            where.append("observed_at <= ?")
            params.append(to_date)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"""SELECT id, observed_at, source_platform, observation_type, summary,
                       subject_type, subject_key, metrics_json, evidence_json, region_code
                FROM market_observations
                {where_sql}
                ORDER BY observed_at DESC
                LIMIT ?""",
            [*params, limit],
        ).fetchall()
        observations = []
        for row in rows:
            item = dict(row)
            observations.append(
                {
                    "id": item["id"],
                    "observed_at": item.get("observed_at") or "",
                    "event_kind": item.get("observation_type") or "",
                    "event_title": item.get("summary") or "",
                    "impact": impact or "neutral",
                    "source_url": "",
                    "notes": item.get("summary") or "",
                    "source_platform": item.get("source_platform") or "",
                    "subject_type": item.get("subject_type") or "",
                    "subject_key": item.get("subject_key") or "",
                    "metrics": _decode_json(item.get("metrics_json"), {}),
                    "evidence": _decode_json(item.get("evidence_json"), []),
                    "region_code": item.get("region_code") or "",
                }
            )
        if impact:
            observations = [row for row in observations if row["impact"] == impact]
        return {"observations": observations}

    if kind:
        where.append("event_kind = ?")
        params.append(kind)
    if impact:
        where.append("impact = ?")
        params.append(impact)
    if from_date:
        where.append("observed_at >= ?")
        params.append(from_date)
    if to_date:
        where.append("observed_at <= ?")
        params.append(to_date)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT * FROM market_observations {where_sql} ORDER BY observed_at DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    observations = [dict(row) for row in rows]
    if observations:
        return {"observations": observations}

    fallback = []
    for row in _latest_bh_rows()[:limit]:
        title = str(row.get("title") or "").strip()
        brand = _brand_from_title(title)
        impact_value = "positive" if brand == "Viltrox" else "competitive"
        if kind and kind != "review":
            continue
        if impact and impact != impact_value:
            continue
        fallback.append(
            {
                "id": f"bh-{row.get('sku') or len(fallback) + 1}",
                "observed_at": row.get("snapshot_at") or datetime.utcnow().isoformat(),
                "event_kind": "review",
                "event_title": title or "B&H catalog observation",
                "impact": impact_value,
                "source_url": row.get("url") or "",
                "notes": f"{brand} listing rated {row.get('rating') or 0} with {row.get('review_count') or 0} reviews.",
            }
        )
    return {"observations": fallback}


def _decode_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def list_benchmarks() -> dict:
    conn = get_conn()
    cols = {
        str(row["name"] if isinstance(row, dict) else row[1])
        for row in conn.execute("PRAGMA table_info(genre_benchmarks)").fetchall()
    }
    rows = conn.execute("SELECT * FROM genre_benchmarks ORDER BY genre").fetchall()
    genres = []
    if "avg_score_target" in cols:
        for row in rows:
            item = dict(row)
            genres.append(
                {
                    "name": item.get("genre") or "",
                    "avg_score": item.get("avg_score_target") or 0,
                    "pass_rate": item.get("pass_rate_target") or 0,
                    "characteristics": _decode_json(item.get("characteristics_json"), {}),
                    "sample_count": item.get("sample_count") or 0,
                }
            )
        return {"genres": genres}

    for row in rows:
        item = dict(row)
        genres.append(
            {
                "name": item.get("genre") or "",
                "avg_score": round(float(item.get("avg_overall") or 0), 2),
                "pass_rate": 0,
                "characteristics": {
                    "tech_p50": item.get("p50_tech") or 0,
                    "tech_p75": item.get("p75_tech") or 0,
                    "marketing_p50": item.get("p50_mkt") or 0,
                    "marketing_p75": item.get("p75_mkt") or 0,
                },
                "sample_count": item.get("sample_count") or 0,
            }
        )
    if genres:
        return {"genres": genres}
    return {
        "genres": [],
        "status": "empty",
        "message": "No genre benchmarks yet. They will populate after confirmed submissions are analyzed.",
    }


def list_gap_insights() -> dict:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM ai_insights
               WHERE module='market' AND category='gap'
                 AND (expires_at IS NULL OR expires_at > datetime('now'))
                 AND dismissed_at IS NULL
               ORDER BY generated_at DESC
               LIMIT 20"""
        ).fetchall()
    except Exception:
        # 诊断阶段三c(1):ai_insights 表缺失(未迁移)时此前抛 500 崩端点。
        # 收尾期不建表——优雅降级返空,落到下方 heatmap 兜底/占位,不再 500。
        rows = []
    insights = [dict(row) for row in rows]
    if insights:
        return {"insights": insights}

    heatmap = build_category_heatmap().get("segments") or []
    generated_at = datetime.utcnow().isoformat()
    fallback = []
    for segment in heatmap:
        status = str(segment.get("status") or "")
        focal = str(segment.get("focal") or "Unknown")
        if status == "opp":
            fallback.append(
                {
                    "type": "opportunity",
                    "severity": "green",
                    "title": f"{focal} is under-defended",
                    "generated_at": generated_at,
                }
            )
        elif status == "behind":
            fallback.append(
                {
                    "type": "risk",
                    "severity": "amber",
                    "title": f"{focal} is crowded by competitors",
                    "generated_at": generated_at,
                }
            )
        elif status == "lead":
            fallback.append(
                {
                    "type": "leading",
                    "severity": "green",
                    "title": f"{focal} is currently a strength for Viltrox",
                    "generated_at": generated_at,
                }
            )
        if len(fallback) >= 4:
            break
    if not fallback:
        fallback = [
            {
                "type": "opportunity",
                "severity": "info",
                "title": "Run the next B&H refresh to generate market gaps",
                "generated_at": generated_at,
            }
        ]
    return {"insights": fallback}


async def regenerate_gap_insights() -> dict:
    heatmap = build_category_heatmap()
    benchmarks = list_benchmarks()
    observations = list_observations(limit=10)
    prompt = _gap_prompt(heatmap, benchmarks, observations)
    try:
        response = await asyncio.to_thread(
            lambda: generate_text(
                prompt,
                provider="anthropic",
                model="claude-opus-4-7",
                purpose="intelligence_market",
                max_output_tokens=2048,
            )
        )
        parsed = json.loads(str(response.get("text") or ""))
        generated_insights = parsed.get("insights") if isinstance(parsed, dict) else None
        if not isinstance(generated_insights, list) or not all(
            isinstance(insight, dict) for insight in generated_insights
        ):
            raise ValueError("Claude response must contain a list of insight objects")
    except Exception as exc:
        logger.exception("market gap generation failed: %s", exc)
        return list_gap_insights()

    conn = get_conn()
    conn.execute(
        "UPDATE ai_insights SET expires_at = datetime('now') "
        "WHERE module='market' AND category='gap' AND expires_at IS NULL"
    )
    now = datetime.utcnow().isoformat()
    expires = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    for ins in generated_insights:
        conn.execute(
            """INSERT INTO ai_insights (
                module, category, severity, insight_type, title, body,
                source_data_ref, model_used, generated_at, expires_at
            ) VALUES ('market', 'gap', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ins.get("severity", "info"),
                ins.get("type", "opportunity"),
                ins.get("title", "Untitled"),
                ins.get("body", ""),
                json.dumps({"benchmarks": len(benchmarks.get("genres") or []), "observations": len(observations.get("observations") or [])}),
                "claude-opus-4-7",
                now,
                expires,
            ),
        )
    conn.commit()
    return list_gap_insights()


def _gap_prompt(heatmap: dict[str, Any], benchmarks: dict[str, Any], observations: dict[str, Any]) -> str:
    return f"""Analyze Viltrox market position.

HEATMAP:
{json.dumps(heatmap, ensure_ascii=False, indent=2)}

BENCHMARKS:
{json.dumps(benchmarks, ensure_ascii=False, indent=2)}

OBSERVATIONS:
{json.dumps(observations, ensure_ascii=False, indent=2)}

Return JSON only:
{{
  "insights": [
    {{
      "type": "opportunity" | "leading" | "risk" | "behind",
      "severity": "info" | "amber" | "red" | "green",
      "title": "Short headline",
      "body": "Concrete analysis and action."
    }}
  ]
}}
"""
