"""Dynamic seed document builders for Via knowledge memory."""
from __future__ import annotations

import json
from typing import Any

from app.core.constants import PRODUCT_RULES
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.via.product_brain import STORE_URL
from app.services.via.stock_watch import build_via_stock_watch

logger = get_logger(__name__)


def full_product_rule_docs(limit: int = 40) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for item in PRODUCT_RULES[: max(1, int(limit))]:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        keywords = [str(value).strip() for value in (item.get("keywords") or []) if str(value).strip()]
        docs.append(
            {
                "memory_kind": "product_rule",
                "memory_key": label,
                "source_ref": f"{STORE_URL}/search?q={label.replace(' ', '+')}",
                "summary": f"{label} | product rule",
                "text": (
                    f"Viltrox product rule entry. Label: {label}. "
                    f"Series: {item.get('series') or ''}. "
                    f"Keywords: {', '.join(keywords[:16])}. "
                    f"Official store root: {STORE_URL}."
                )[:1500],
                "payload": {
                    "label": label,
                    "series": str(item.get("series") or ""),
                    "keywords": keywords[:24],
                    "source": "product_rules",
                },
            }
        )
    return docs


def _safe_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("via.knowledge_seed.safe_json_parse_failed", exc_info=True)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def submission_analysis_docs(user_id: int, limit: int = 8) -> list[dict[str, Any]]:
    if not int(user_id or 0):
        return []
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, title, platform, product_label, product_series, detection_status,
               final_score, creator_score, overall_score, created_at, video_analysis
        FROM submissions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(user_id), int(limit)),
    ).fetchall()
    docs: list[dict[str, Any]] = []
    for row in rows:
        analysis = _safe_json(row["video_analysis"] if "video_analysis" in row.keys() else row[10])
        notes = str(analysis.get("notes") or analysis.get("summary") or "").strip()
        content_genre = str(analysis.get("content_genre") or analysis.get("genre") or "").strip()
        products = analysis.get("products") or analysis.get("viltrox_products") or analysis.get("products_detected") or []
        if not isinstance(products, list):
            products = []
        improvements = analysis.get("improvement_suggestions") or analysis.get("suggestions") or []
        if not isinstance(improvements, list):
            improvements = []
        title = str(row["title"] or "").strip() or f"submission-{row['id']}"
        product_label = str(row["product_label"] or row["product_series"] or "").strip()
        summary = f"{title} | {row['platform'] or 'unknown'} | score {row['final_score'] or 0}"
        text = (
            f"User submission analysis. Title: {title}. Platform: {row['platform'] or 'unknown'}. "
            f"Detected product lane: {product_label or 'unknown'}. "
            f"Detection status: {row['detection_status'] or 'pending'}. "
            f"Final score: {row['final_score'] or 0}. Creator score: {row['creator_score'] or 0}. Overall score: {row['overall_score'] or 0}. "
            f"Content genre: {content_genre or 'unknown'}. "
            f"Detected products: {', '.join(str(item) for item in products[:6]) or 'none'}. "
            f"Analysis notes: {notes[:500]}. "
            f"Improvement suggestions: {'; '.join(str(item) for item in improvements[:4]) or 'none'}. "
            f"Created at: {row['created_at'] or ''}."
        )
        docs.append(
            {
                "memory_kind": "submission_analysis",
                "memory_key": f"submission:{row['id']}",
                "source_ref": f"submission:{row['id']}",
                "summary": summary[:300],
                "text": text[:2500],
                "payload": {
                    "submission_id": int(row["id"] or 0),
                    "product_label": product_label,
                    "platform": str(row["platform"] or ""),
                    "content_genre": content_genre,
                    "source": "user_submission",
                },
            }
        )
    return docs


def market_observation_docs(limit: int = 8) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT observation_key, source_platform, subject_type, subject_key, region_code,
               summary, metrics_json, created_at
        FROM market_observations
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    docs: list[dict[str, Any]] = []
    for row in rows:
        summary = str(row["summary"] or "").strip()
        metrics = str(row["metrics_json"] or "").strip()
        text = (
            f"Market observation from {row['source_platform'] or 'unknown'}. "
            f"Subject type: {row['subject_type'] or 'unknown'}. Subject key: {row['subject_key'] or ''}. "
            f"Region: {row['region_code'] or 'global'}. Summary: {summary or 'n/a'}. "
            f"Metrics: {metrics[:500]}. Created at: {row['created_at'] or ''}."
        )
        docs.append(
            {
                "memory_kind": "market_observation",
                "memory_key": str(row["subject_key"] or ""),
                "source_ref": str(row["observation_key"] or ""),
                "summary": (summary or text)[:300],
                "text": text[:1800],
                "payload": {
                    "source_platform": str(row["source_platform"] or ""),
                    "subject_type": str(row["subject_type"] or ""),
                    "region_code": str(row["region_code"] or ""),
                    "source": "market_observations",
                },
            }
        )
    return docs


def bh_docs(limit: int = 6) -> list[dict[str, Any]]:
    snapshot = build_via_stock_watch(limit=max(3, int(limit)))
    docs: list[dict[str, Any]] = []
    for item in snapshot.get("items") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        summary = f"{title} | ${item.get('price') or 0} | stock {'yes' if item.get('in_stock') else 'no'}"
        text = (
            f"B&H stock watch for Viltrox. Product: {title}. Price: ${item.get('price') or 0}. "
            f"Rating: {item.get('rating') or 0}. Reviews: {item.get('review_count') or 0}. "
            f"In stock: {'yes' if item.get('in_stock') else 'no'}. "
            f"URL: {item.get('url') or ''}. Snapshot: {item.get('snapshot_at') or ''}."
        )
        docs.append(
            {
                "memory_kind": "bh_market_signal",
                "memory_key": str(item.get("sku") or title),
                "source_ref": str(item.get("url") or item.get("sku") or title),
                "summary": summary[:300],
                "text": text[:1600],
                "payload": {
                    "sku": str(item.get("sku") or ""),
                    "in_stock": bool(item.get("in_stock")),
                    "price": float(item.get("price") or 0.0),
                    "source": "bh_products",
                },
            }
        )
    return docs
