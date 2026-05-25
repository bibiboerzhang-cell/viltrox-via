"""Product monitoring, run records, and raw outreach suggestions."""
from __future__ import annotations

import math
import secrets
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains.analytics.common import (
    DEFAULT_PLATFORMS,
    _actor,
    _db_bool,
    _int,
    _json,
    _provider_error_payload,
    _provider_status_from_error,
    _run_uid,
    _utcnow,
)
from app.domains.analytics.schema import ensure_vkpi_analytics_schema


def _create_run(run_type: str, *, staff: dict[str, Any] | None, target_skus: list[str], platforms: list[str], period_days: int) -> int:
    ensure_vkpi_analytics_schema()
    uid = _run_uid(run_type)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_analytics_runs
            (run_uid, run_type, triggered_by_staff_id, triggered_at, status, target_skus_json, platforms_json)
        VALUES (?,?,?,?,?,?,?)
        """,
        (uid, run_type, _actor(staff) or None, _utcnow(), "running", _json(target_skus), _json(platforms)),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM vkpi_analytics_runs WHERE run_uid=?", (uid,)).fetchone()
    return int(row["id"]) if row else 0


def _update_run(run_id: int, status: str, *, summary: dict[str, Any] | None = None, raw: dict[str, Any] | None = None, error: str = "") -> None:
    get_conn().execute(
        """
        UPDATE vkpi_analytics_runs
        SET status=?, completed_at=?, summary_json=?, raw_result_json=?, error_message=?
        WHERE id=?
        """,
        (status, _utcnow(), _json(summary or {}), _json(raw or {}), error[:500], int(run_id)),
    )
    get_conn().commit()


async def _invoke_lens_compare(product_a: str, product_b: str, *, platform: str, market: str, date_from: str, date_to: str, max_videos: int) -> dict[str, Any]:
    try:
        from app.services.intelligence.lens_compare import compare_two_lenses

        result = await compare_two_lenses(product_a, product_b, max_videos=max_videos, platform=platform, market=market, date_from=date_from, date_to=date_to)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        return {
            **_provider_error_payload(exc, platform=platform, query=f"{product_a} vs {product_b}"),
            "metadata": {
                "provider_status_a": _provider_status_from_error(exc),
                "provider_status_b": _provider_status_from_error(exc),
                "provider_error": str(exc)[:500],
            },
        }


async def _invoke_lens_monitor(product_sku: str, *, platform: str, market: str, date_from: str, date_to: str, max_videos: int) -> dict[str, Any]:
    try:
        from app.services.intelligence.lens_monitor import monitor_lens_market

        result = await monitor_lens_market(product_sku, max_videos=max_videos, platform=platform, market=market, date_from=date_from, date_to=date_to)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        return _provider_error_payload(exc, platform=platform, query=product_sku)


async def compare_products(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    product_a = str(body.get("product_a") or body.get("sku_a") or body.get("lens_a") or "").strip()
    product_b = str(body.get("product_b") or body.get("sku_b") or body.get("lens_b") or "").strip()
    if not product_a or not product_b:
        raise ValueError("product_a and product_b required")
    platform = str(body.get("platform") or "youtube").strip().lower()
    run_id = _create_run("compare", staff=staff, target_skus=[product_a, product_b], platforms=[platform], period_days=_int(body.get("period_days"), 30))
    try:
        raw = await _invoke_lens_compare(product_a, product_b, platform=platform, market=str(body.get("market") or ""), date_from=str(body.get("date_from") or ""), date_to=str(body.get("date_to") or ""), max_videos=max(1, min(100, _int(body.get("max_videos"), 15))))
        summary = {"product_a": product_a, "product_b": product_b, "provider_status_a": raw.get("metadata", {}).get("provider_status_a"), "provider_status_b": raw.get("metadata", {}).get("provider_status_b"), "comparison": raw.get("comparison") or {}}
        _update_run(run_id, "success", summary=summary, raw=raw)
        return {"run_id": run_id, "status": "success", "summary": summary, "result": raw}
    except Exception as exc:
        _update_run(run_id, "failed", error=str(exc))
        raise


def _suggestion_score(video: dict[str, Any], channel: str) -> float:
    views = _int(video.get("views"))
    likes = _int(video.get("likes"))
    comments = _int(video.get("comments"))
    engagement = (likes + comments) / max(1, views)
    return round(math.log10(max(10, views)) * 8 + engagement * 100, 2)


def _upsert_suggestions(run_id: int, product_sku: str, platform: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    seen: set[tuple[str, str]] = set()
    for cat_data in categories.values():
        if not isinstance(cat_data, dict):
            continue
        for video in cat_data.get("top_videos") or []:
            if not isinstance(video, dict):
                continue
            handle = str(video.get("channel") or "").strip()
            url = str(video.get("url") or "").strip()
            if not handle or (platform, handle.lower()) in seen:
                continue
            seen.add((platform, handle.lower()))
            score = _suggestion_score(video, handle)
            uid = f"sug-{secrets.token_hex(8)}"
            conn = get_conn()
            values = (
                uid, run_id, product_sku, _utcnow(), platform, handle, handle, None, None, url,
                str(video.get("title") or ""), _int(video.get("views")), _int(video.get("likes")),
                video.get("published") or None, 5 if score >= 70 else 0, score, _db_bool(_int(video.get("views")) >= 100000),
                "new", _json({"source": "lens_monitor", "video": video}),
            )
            if is_postgres_runtime():
                conn.execute(
                    """
                    INSERT INTO vkpi_outreach_suggestions
                        (suggestion_uid, source_run_id, source_product_sku, detected_at, platform, handle,
                         channel_name, follower_count, engagement_rate, source_video_url, source_video_title,
                         source_view_count, source_like_count, source_published_at, priority, score, is_viral,
                         status, metadata_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(platform, handle, source_product_sku) DO NOTHING
                    """,
                    values,
                )
            else:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO vkpi_outreach_suggestions
                        (suggestion_uid, source_run_id, source_product_sku, detected_at, platform, handle,
                         channel_name, follower_count, engagement_rate, source_video_url, source_video_title,
                         source_view_count, source_like_count, source_published_at, priority, score, is_viral,
                         status, metadata_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    values,
                )
            conn.commit()
            row = conn.execute("SELECT * FROM vkpi_outreach_suggestions WHERE platform=? AND handle=? AND source_product_sku=?", (platform, handle, product_sku)).fetchone()
            if row:
                suggestions.append(dict(row))
    return suggestions


async def monitor_product(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    product_sku = str(body.get("product_sku") or body.get("query") or body.get("product") or "").strip()
    if not product_sku:
        raise ValueError("product_sku required")
    platform = str(body.get("platform") or "youtube").strip().lower()
    run_id = _create_run("monitor", staff=staff, target_skus=[product_sku], platforms=[platform], period_days=_int(body.get("period_days"), 30))
    try:
        raw = await _invoke_lens_monitor(product_sku, platform=platform, market=str(body.get("market") or ""), date_from=str(body.get("date_from") or ""), date_to=str(body.get("date_to") or ""), max_videos=max(1, min(200, _int(body.get("max_videos"), 50))))
        overview = raw.get("overview") or {}
        summary = {"product_sku": product_sku, "platform": platform, "overview": overview, "provider_status": raw.get("metadata", {}).get("provider_status"), "message": raw.get("error") or ""}
        suggestions = []
        if _int(overview.get("total_videos")) > 0:
            suggestions = _upsert_suggestions(run_id, product_sku, platform, raw)
        summary["suggestions_created"] = len(suggestions)
        _update_run(run_id, "success", summary=summary, raw=raw)
        get_conn().execute(
            "UPDATE vkpi_monitored_products SET last_monitored_at=?, last_run_id=? WHERE product_sku=?",
            (_utcnow(), int(run_id), product_sku),
        )
        get_conn().commit()
        return {"run_id": run_id, "status": "success", "summary": summary, "result": raw, "suggestions": suggestions}
    except Exception as exc:
        _update_run(run_id, "failed", error=str(exc))
        raise


def upsert_monitored_product(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    sku = str(body.get("product_sku") or body.get("sku") or "").strip()
    if not sku:
        raise ValueError("product_sku required")
    platforms = body.get("platforms") or body.get("monitor_platforms") or DEFAULT_PLATFORMS[:4]
    if not isinstance(platforms, list):
        platforms = [str(platforms)]
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_monitored_products
            (product_sku, product_name, series, mount, monitor_platforms_json, keywords_json, enabled, created_by_staff_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_sku) DO UPDATE SET
            product_name=excluded.product_name,
            series=excluded.series,
            mount=excluded.mount,
            monitor_platforms_json=excluded.monitor_platforms_json,
            keywords_json=excluded.keywords_json,
            enabled=excluded.enabled
        """,
        (sku, str(body.get("product_name") or body.get("name") or sku), str(body.get("series") or ""), str(body.get("mount") or ""), _json(platforms), _json(body.get("keywords") or []), _db_bool(str(body.get("enabled")).lower() not in {"false", "0", "no"}), _actor(staff) or None, _utcnow()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_monitored_products WHERE product_sku=?", (sku,)).fetchone()
    return {"product": dict(row) if row else {}}


def list_monitored_products(limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    rows = get_conn().execute("SELECT * FROM vkpi_monitored_products ORDER BY enabled DESC, product_sku ASC LIMIT ?", (max(1, min(300, int(limit or 100))),)).fetchall()
    return {"products": [dict(row) for row in rows]}


def delete_monitored_product(product_sku: str) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    get_conn().execute("UPDATE vkpi_monitored_products SET enabled=? WHERE product_sku=?", (_db_bool(False), str(product_sku),))
    get_conn().commit()
    return {"status": "disabled", "product_sku": product_sku}


def list_runs(limit: int = 50, run_type: str = "") -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    where = "WHERE run_type=?" if run_type else ""
    params = (run_type, max(1, min(200, int(limit or 50)))) if run_type else (max(1, min(200, int(limit or 50))),)
    rows = get_conn().execute(f"SELECT * FROM vkpi_analytics_runs {where} ORDER BY triggered_at DESC, id DESC LIMIT ?", params).fetchall()
    return {"runs": [dict(row) for row in rows]}


def get_run(run_id: int) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    row = get_conn().execute("SELECT * FROM vkpi_analytics_runs WHERE id=?", (int(run_id),)).fetchone()
    if not row:
        raise LookupError("analytics run not found")
    return {"run": dict(row)}


def suggestions_overview() -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    try:
        row = get_conn().execute("SELECT * FROM vkpi_suggestions_overview").fetchone()
        if row:
            return dict(row)
    except Exception as exc:
        logger.warning("vkpi suggestions overview view failed, falling back to grouped status: %s", exc)
    rows = get_conn().execute("SELECT status, COUNT(*) AS n FROM vkpi_outreach_suggestions GROUP BY status").fetchall()
    return {f"{row['status']}_count": int(row["n"] or 0) for row in rows}


def list_suggestions(status: str = "new", limit: int = 100, product_sku: str = "") -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    where: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        where.append("status=?")
        params.append(status)
    if product_sku:
        where.append("source_product_sku=?")
        params.append(product_sku)
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(f"SELECT * FROM vkpi_outreach_suggestions {clause} ORDER BY priority DESC, score DESC, detected_at DESC LIMIT ?", (*params, max(1, min(500, int(limit or 100))))).fetchall()
    return {"suggestions": [dict(row) for row in rows]}
