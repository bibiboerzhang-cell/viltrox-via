"""V-KPI product analysis and suggested outreach adapter."""
from __future__ import annotations

import json
import math
import os
import secrets
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db.connection import get_conn, is_postgres_runtime
from app.services.system import staff as staff_service
from app.services.vkpi import audit, kol_claims, link_center, platform_crawl_settings, workflow
from app.services.vkpi.schema_analytics import ensure_vkpi_analytics_schema
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id

DEFAULT_PLATFORMS = ["youtube", "instagram", "tiktok", "xiaohongshu", "facebook", "reddit", "x"]
CHINA_TZ = ZoneInfo("Asia/Shanghai")
OFFICIAL_ACCOUNT_KEYWORDS = ("viltrox", "唯卓仕")
PLATFORM_EQUIVALENTS = {
    "youtube": ["youtube", "yt"],
    "yt": ["youtube", "yt"],
    "instagram": ["instagram", "ig"],
    "ig": ["instagram", "ig"],
    "tiktok": ["tiktok", "tt"],
    "tt": ["tiktok", "tt"],
    "xiaohongshu": ["xiaohongshu", "xhs"],
    "xhs": ["xiaohongshu", "xhs"],
    "facebook": ["facebook", "fb"],
    "fb": ["facebook", "fb"],
    "x": ["x", "twitter"],
    "twitter": ["x", "twitter"],
    "bilibili": ["bilibili", "bili"],
    "bili": ["bilibili", "bili"],
}
BUYER_INTENT_TERMS = {
    "review": "评测意图",
    "vs": "对比选购",
    "comparison": "对比选购",
    "compare": "对比选购",
    "should you buy": "购买决策",
    "best lens": "购买决策",
    "sample": "样片参考",
    "autofocus": "性能关注",
    "low light": "弱光场景",
    "portrait": "人像场景",
    "wedding": "婚礼/商业拍摄",
    "filmmaker": "视频创作者",
    "cinematic": "视频创作者",
    "photography": "摄影用户",
    "videography": "视频用户",
    "camera lens": "镜头购买意图",
    "镜头": "镜头购买意图",
    "评测": "评测意图",
    "对比": "对比选购",
    "样片": "样片参考",
    "人像": "人像场景",
    "视频": "视频用户",
}
COMPETITOR_TERMS = (
    "sigma",
    "tamron",
    "sony gm",
    "sony g master",
    "canon rf",
    "nikon z",
    "fujifilm xf",
    "fuji x",
    "samyang",
    "rokinon",
    "sirui",
    "laowa",
    "ttartisan",
    "7artisans",
    "meike",
    "zeiss",
    "voigtlander",
)


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _actor(staff: dict[str, Any] | None) -> int:
    return resolve_staff_id(staff) or 0


def _run_uid(run_type: str) -> str:
    return f"ana-{run_type}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def _db_bool(value: bool) -> bool | int:
    """Postgres has real booleans; SQLite uses 0/1 in the local fallback schema."""
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _china_today() -> str:
    return datetime.now(CHINA_TZ).date().isoformat()


def _platform_variants(platform: str) -> list[str]:
    clean = str(platform or "").strip().lower()
    return PLATFORM_EQUIVALENTS.get(clean, [clean] if clean else [])


def _is_official_account(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("handle", "channel_name", "profile_url")
    ).lower()
    return any(term in haystack for term in OFFICIAL_ACCOUNT_KEYWORDS)


def _text_for_relevance(row: dict[str, Any]) -> str:
    metadata = _loads_json(row.get("metadata_json"), {}) or {}
    video = metadata.get("video") if isinstance(metadata, dict) else {}
    bits = [
        row.get("source_product_sku"),
        row.get("source_video_title"),
        row.get("channel_name"),
        row.get("handle"),
        row.get("source_video_url"),
    ]
    if isinstance(video, dict):
        bits.extend([video.get("title"), video.get("description"), video.get("caption")])
    return " ".join(str(bit or "") for bit in bits).lower()


def _content_intelligence(row: dict[str, Any]) -> dict[str, Any]:
    text = _text_for_relevance(row)
    matched_intents = [label for term, label in BUYER_INTENT_TERMS.items() if term in text]
    matched_competitors = [term for term in COMPETITOR_TERMS if term in text]
    mentions_viltrox = "viltrox" in text or "唯卓仕" in text
    product = str(row.get("source_product_sku") or "").strip()

    buyer_profile = "镜头购买决策人 / 摄影视频用户"
    viewer_profile = "关注镜头评测、样片、对比和拍摄场景的潜在买家"
    if any(label in matched_intents for label in ("视频创作者", "视频用户")):
        buyer_profile = "视频创作者 / 摄影器材升级用户"
        viewer_profile = "关注自动对焦、弱光、视频画质和实拍工作流的人群"
    elif any(label in matched_intents for label in ("人像场景", "婚礼/商业拍摄")):
        buyer_profile = "人像 / 婚礼 / 商业摄影用户"
        viewer_profile = "关注焦外、肤色、弱光和镜头性价比的人群"

    reasons: list[str] = []
    if mentions_viltrox:
        reasons.append("内容直接提到 Viltrox / 唯卓仕")
    if product:
        reasons.append(f"匹配监控产品 {product}")
    if matched_competitors:
        reasons.append("提到同级竞品：" + "、".join(matched_competitors[:3]))
    if matched_intents:
        reasons.append("存在购买/选型意图：" + "、".join(dict.fromkeys(matched_intents[:4])))
    if not reasons:
        reasons.append("与镜头、拍摄或相机用户场景相关")

    score_bonus = 0
    if mentions_viltrox:
        score_bonus += 18
    if matched_competitors:
        score_bonus += 12
    score_bonus += min(20, len(set(matched_intents)) * 5)

    return {
        "score_bonus": score_bonus,
        "relevance_reason": "；".join(reasons),
        "buyer_profile": buyer_profile,
        "viewer_profile": viewer_profile,
        "content_angle": " / ".join(dict.fromkeys(matched_intents[:3])) or "产品相关内容",
        "matched_competitors": matched_competitors[:5],
        "matched_intents": list(dict.fromkeys(matched_intents)),
        "mentions_viltrox": mentions_viltrox,
    }


def _provider_status_from_error(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, (ImportError, ModuleNotFoundError, NotImplementedError)):
        return "not_configured"
    if "not found" in message or "404" in message:
        return "provider_not_found"
    if "timeout" in message or "timed out" in message:
        return "provider_timeout"
    return "provider_error"


def _provider_error_payload(exc: Exception, *, platform: str, query: str) -> dict[str, Any]:
    status = _provider_status_from_error(exc)
    return {
        "query": query,
        "platform": platform,
        "overview": {"total_videos": 0, "total_views": 0, "total_likes": 0, "total_comments": 0},
        "comparison": {},
        "categories": {},
        "error": "平台抓取暂未配置或本次抓取失败，未生成假数据。",
        "metadata": {
            "provider_status": status,
            "provider_error": str(exc)[:500],
        },
    }


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
        if not raw.get("metadata", {}).get("provider_status"):
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
    except Exception:
        pass
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


def _find_matching_kol(row: dict[str, Any]) -> dict[str, Any] | None:
    ensure_vkpi_schema()
    explicit_id = _int(row.get("existing_kol_id"))
    conn = get_conn()
    if explicit_id:
        found = conn.execute("SELECT * FROM kols WHERE id=?", (explicit_id,)).fetchone()
        if found:
            return dict(found)
    handle = str(row.get("handle") or row.get("channel_name") or "").strip().lower()
    if not handle:
        return None
    variants = _platform_variants(str(row.get("platform") or ""))
    if not variants:
        variants = [str(row.get("platform") or "").strip().lower()]
    placeholders = ",".join("?" for _ in variants)
    like_handle = f"%/{handle}%"
    found = conn.execute(
        f"""
        SELECT *
        FROM kols
        WHERE lower(platform) IN ({placeholders})
          AND (
            lower(channel_name)=?
            OR lower(channel_url) LIKE ?
            OR lower(profile_url) LIKE ?
          )
        ORDER BY id DESC
        LIMIT 1
        """,
        (*variants, handle, like_handle, like_handle),
    ).fetchone()
    return dict(found) if found else None


def _has_contact_history(row: dict[str, Any], kol: dict[str, Any] | None) -> bool:
    if _is_official_account(row):
        return True
    if not kol:
        return False
    conn = get_conn()
    kol_id = _int(kol.get("id"))
    claims = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,)).fetchone()
    if _int(claims["n"] if claims else 0) > 0:
        return True
    projects = conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE kol_id=?", (kol_id,)).fetchone()
    if _int(projects["n"] if projects else 0) > 0:
        return True
    status = str(kol.get("contact_status") or "").strip().lower()
    if status and status not in {"cold", "new", "not_contacted", "not-contacted", "uncontacted", "待联系"}:
        return True
    if _int(kol.get("assigned_staff_id")) > 0:
        return True
    return False


def rank_uncontacted_suggestions(limit: int = 100, product_sku: str = "") -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_vkpi_analytics_schema()
    base_rows = list_suggestions(status="new", limit=max(100, min(1000, int(limit or 100) * 5)), product_sku=product_sku).get("suggestions") or []
    ranked: list[dict[str, Any]] = []
    for base in base_rows:
        row = dict(base)
        kol = _find_matching_kol(row)
        if _has_contact_history(row, kol):
            continue
        intel = _content_intelligence(row)
        quality_score = round(float(row.get("score") or 0) + float(intel.get("score_bonus") or 0), 2)
        if _int(row.get("source_view_count")) >= 100000:
            quality_score += 8
        row["quality_score"] = round(quality_score, 2)
        row["relevance_reason"] = intel["relevance_reason"]
        row["buyer_profile"] = intel["buyer_profile"]
        row["viewer_profile"] = intel["viewer_profile"]
        row["content_angle"] = intel["content_angle"]
        row["matched_competitors"] = intel["matched_competitors"]
        row["matched_intents"] = intel["matched_intents"]
        row["matched_kol_id"] = _int(kol.get("id")) if kol else None
        ranked.append(row)
    ranked.sort(key=lambda item: (float(item.get("quality_score") or 0), _int(item.get("source_view_count")), str(item.get("detected_at") or "")), reverse=True)
    return {"items": ranked[: max(1, min(100, int(limit or 100)))], "total_candidates": len(base_rows), "uncontacted_count": len(ranked)}


def _active_staff_members() -> list[dict[str, Any]]:
    members = staff_service.list_members().get("members") or []
    active = []
    for member in members:
        if str(member.get("active", 1)) in {"0", "false", "False"}:
            continue
        staff_id_value = _int(member.get("id") or member.get("staff_id") or member.get("user_id"))
        if not staff_id_value:
            continue
        row = dict(member)
        row["id"] = staff_id_value
        active.append(row)
    return active


def _is_manager_like(staff: dict[str, Any] | None) -> bool:
    if not staff:
        return True
    role = str(staff.get("role") or "").strip().lower()
    return int(staff.get("is_owner") or 0) == 1 or role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _upsert_digest(staff_id_value: int, digest_date: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
    now = _utcnow()
    uid = f"digest-{digest_date}-{staff_id_value}"
    if is_postgres_runtime():
        conn.execute(
            """
            INSERT INTO vkpi_staff_outreach_digests
                (digest_uid, staff_id, digest_date, generated_at, item_count, status, metadata_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(staff_id, digest_date) DO UPDATE SET
                generated_at=excluded.generated_at,
                item_count=excluded.item_count,
                status=excluded.status,
                metadata_json=excluded.metadata_json
            """,
            (uid, staff_id_value, digest_date, now, len(items), "ready", _json({"limit": len(items), "source": "daily_morning_sync"})),
        )
    else:
        conn.execute(
            """
            INSERT INTO vkpi_staff_outreach_digests
                (digest_uid, staff_id, digest_date, generated_at, item_count, status, metadata_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(staff_id, digest_date) DO UPDATE SET
                generated_at=excluded.generated_at,
                item_count=excluded.item_count,
                status=excluded.status,
                metadata_json=excluded.metadata_json
            """,
            (uid, staff_id_value, digest_date, now, len(items), "ready", _json({"limit": len(items), "source": "daily_morning_sync"})),
        )
    digest_row = conn.execute("SELECT * FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?", (staff_id_value, digest_date)).fetchone()
    digest_id = _int(digest_row["id"] if digest_row else 0)
    if digest_id:
        conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (digest_id,))
        for index, item in enumerate(items, start=1):
            conn.execute(
                """
                INSERT INTO vkpi_staff_outreach_digest_items
                    (digest_id, suggestion_id, rank, quality_score, relevance_reason, buyer_profile,
                     viewer_profile, content_angle, metadata_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    digest_id,
                    _int(item.get("id")),
                    index,
                    float(item.get("quality_score") or 0),
                    str(item.get("relevance_reason") or ""),
                    str(item.get("buyer_profile") or ""),
                    str(item.get("viewer_profile") or ""),
                    str(item.get("content_angle") or ""),
                    _json(
                        {
                            "source_product_sku": item.get("source_product_sku"),
                            "matched_competitors": item.get("matched_competitors"),
                            "matched_intents": item.get("matched_intents"),
                            "matched_kol_id": item.get("matched_kol_id"),
                        }
                    ),
                    now,
                ),
            )
    conn.commit()
    return {"digest_id": digest_id, "staff_id": staff_id_value, "item_count": len(items)}


def generate_daily_staff_outreach_digest(target_date: str | None = None, limit: int = 100, staff: dict[str, Any] | None = None, product_sku: str = "") -> dict[str, Any]:
    digest_date = str(target_date or _china_today())
    ranked = rank_uncontacted_suggestions(limit=limit, product_sku=product_sku)
    items = ranked.get("items") or []
    members = _active_staff_members() if _is_manager_like(staff) else []
    if not members and staff:
        sid = _actor(staff)
        if sid:
            members = [{"id": sid, "user_name": staff.get("name") or staff.get("email") or "staff"}]
    digests = [_upsert_digest(_int(member.get("id")), digest_date, items) for member in members]
    return {
        "status": "ok",
        "digest_date": digest_date,
        "staff_count": len(digests),
        "items_per_staff": len(items),
        "total_candidates": ranked.get("total_candidates", 0),
        "uncontacted_count": ranked.get("uncontacted_count", 0),
        "digests": digests,
    }


def daily_staff_outreach_digest_status(target_date: str | None = None, limit: int = 100, staff: dict[str, Any] | None = None, product_sku: str = "") -> dict[str, Any]:
    """Return the 08:00 China Top-100 digest status without fabricating KOL data."""
    ensure_vkpi_analytics_schema()
    digest_date = str(target_date or _china_today())
    safe_limit = max(1, min(100, int(limit or 100)))
    ranked = rank_uncontacted_suggestions(limit=safe_limit, product_sku=product_sku)
    members = _active_staff_members() if _is_manager_like(staff) else []
    if not members and staff:
        sid = _actor(staff)
        if sid:
            members = [{"id": sid, "user_name": staff.get("name") or staff.get("email") or "staff", "email": staff.get("email")}]

    conn = get_conn()
    staff_rows: list[dict[str, Any]] = []
    digest_count = 0
    ready_staff_count = 0
    items_total = 0
    last_generated_at = ""
    for member in members:
        sid = _int(member.get("id"))
        row = conn.execute(
            "SELECT * FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?",
            (sid, digest_date),
        ).fetchone()
        digest = dict(row) if row else {}
        item_count = _int(digest.get("item_count")) if digest else 0
        status = str(digest.get("status") or "not_generated")
        generated_at = str(digest.get("generated_at") or "")
        if digest:
            digest_count += 1
        if status == "ready" and item_count > 0:
            ready_staff_count += 1
        items_total += item_count
        if generated_at and generated_at > last_generated_at:
            last_generated_at = generated_at
        staff_rows.append(
            {
                "staff_id": sid,
                "name": member.get("name") or member.get("user_name") or member.get("email") or f"staff-{sid}",
                "email": member.get("email") or "",
                "status": status,
                "item_count": item_count,
                "generated_at": generated_at,
            }
        )

    flags = platform_crawl_settings.feature_flags().get("flags") or []
    digest_flag = next((dict(item) for item in flags if str(item.get("flag_key") or "") == "daily_staff_digest"), {})
    return {
        "status": "ok",
        "digest_date": digest_date,
        "scheduled_time": "08:00",
        "timezone": "Asia/Shanghai",
        "limit_per_staff": safe_limit,
        "feature_enabled": bool(_int(digest_flag.get("enabled"))),
        "staff_count": len(staff_rows),
        "digest_count": digest_count,
        "ready_staff_count": ready_staff_count,
        "items_total": items_total,
        "last_generated_at": last_generated_at,
        "total_candidates": ranked.get("total_candidates", 0),
        "uncontacted_count": ranked.get("uncontacted_count", 0),
        "rule": "仅推荐未联系、未认领、未建项目的 KOL；排除公司官方账号；按质量分、播放量和产品相关度排序，每员工最多 100 条。",
        "staff": staff_rows,
    }


def list_daily_staff_outreach_digest(staff_id: int, target_date: str | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    digest_date = str(target_date or _china_today())
    conn = get_conn()
    digest = conn.execute(
        "SELECT * FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?",
        (int(staff_id), digest_date),
    ).fetchone()
    if not digest:
        return {"digest": None, "items": [], "digest_date": digest_date}
    rows = conn.execute(
        """
        SELECT
            i.*,
            s.platform,
            s.handle,
            s.channel_name,
            s.follower_count,
            s.engagement_rate,
            s.avatar_url,
            s.profile_url,
            s.source_product_sku,
            s.source_video_url,
            s.source_video_title,
            s.source_view_count,
            s.source_like_count,
            s.source_published_at,
            s.score,
            s.status AS suggestion_status
        FROM vkpi_staff_outreach_digest_items i
        JOIN vkpi_outreach_suggestions s ON s.id = i.suggestion_id
        WHERE i.digest_id=?
        ORDER BY i.rank ASC
        LIMIT ?
        """,
        (_int(digest["id"]), max(1, min(100, int(limit or 100)))),
    ).fetchall()
    return {"digest": dict(digest), "items": [dict(row) for row in rows], "digest_date": digest_date}


def _suggestion_to_kol_lookup_body(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _loads_json(row.get("metadata_json"), {}) or {}
    profile_url = str(row.get("profile_url") or "").strip()
    source_video_url = str(row.get("source_video_url") or "").strip()
    handle = str(row.get("handle") or row.get("channel_name") or "").strip()
    return {
        "platform": row.get("platform"),
        "handle": handle,
        "handle_or_url": profile_url or handle,
        "url": profile_url,
        "channel_url": profile_url,
        "profile_url": profile_url,
        "avatar_url": row.get("avatar_url") or "",
        "follower_count": _int(row.get("follower_count")),
        "avg_views": _int(row.get("source_view_count")),
        "email": "",
        "contact_status": "claimed",
        "primary_category": row.get("source_product_sku") or "",
        "promoted_product": row.get("source_product_sku") or "",
        "channel_tags": ",".join(
            str(item)
            for item in [
                row.get("source_product_sku"),
                row.get("platform"),
                *(metadata.get("matched_intents") if isinstance(metadata.get("matched_intents"), list) else []),
            ]
            if item
        ),
        "notes": str(row.get("source_video_title") or ""),
        "contact_links": [item for item in [profile_url, source_video_url] if item],
        "contact_raw": {
            "source": "outreach_suggestion",
            "suggestion_id": row.get("id"),
            "source_product_sku": row.get("source_product_sku"),
            "source_video_url": source_video_url,
            "source_video_title": row.get("source_video_title"),
            "quality_score": row.get("quality_score") or row.get("score"),
            "metadata": metadata,
        },
        "create_if_missing": True,
    }


def claim_suggestion(suggestion_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Claim a suggested KOL and bridge it into the main KOL/claim lifecycle."""
    ensure_vkpi_analytics_schema()
    ensure_vkpi_schema()
    actor = _actor(staff)
    if not actor:
        raise ValueError("staff_id required")
    conn = get_conn()
    raw = conn.execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    if not raw:
        raise LookupError("suggestion not found")
    row = dict(raw)
    if str(row.get("status") or "new") not in {"new", "claimed"}:
        raise ValueError(f"suggestion is {row.get('status')}")
    if _is_official_account(row):
        raise ValueError("official account cannot be claimed as outreach suggestion")

    lookup = kol_claims.lookup(_suggestion_to_kol_lookup_body(row), staff=staff)
    kol = lookup.get("kol") or {}
    kol_id = _int(kol.get("id"))
    if not kol_id:
        raise LookupError("kol could not be created")

    existing_claim = lookup.get("claim") or {}
    if existing_claim:
        claim = existing_claim
        claim_status = "already_claimed"
        claim_staff_id = _int(claim.get("staff_id")) or actor
    else:
        claim_result = kol_claims.claim(
            kol_id,
            {
                "staff_id": actor,
                "expires_days": 14,
                "metadata": {
                    "source": "outreach_suggestion",
                    "suggestion_id": int(suggestion_id),
                    "source_product_sku": row.get("source_product_sku"),
                    "source_video_url": row.get("source_video_url"),
                    "quality_score": row.get("quality_score") or row.get("score"),
                },
            },
            staff=staff,
        )
        claim = claim_result.get("claim") or {}
        claim_status = "created"
        claim_staff_id = actor

    now = _utcnow()
    metadata = _loads_json(row.get("metadata_json"), {}) or {}
    metadata.update(
        {
            "claim_bridge": {
                "kol_id": kol_id,
                "claim_id": _int(claim.get("id")),
                "claim_status": claim_status,
                "claimed_by_staff_id": claim_staff_id,
                "claimed_at": now,
            }
        }
    )
    conn.execute(
        """
        UPDATE vkpi_outreach_suggestions
        SET status='claimed', existing_kol_id=?, claimed_by_staff_id=?, claimed_at=?, metadata_json=?
        WHERE id=?
        """,
        (kol_id, claim_staff_id or actor, now, _json(metadata), int(suggestion_id)),
    )
    conn.commit()
    audit.log_business_event(
        staff_id=actor,
        action_type="outreach_suggestion_claim",
        target_type="kol",
        target_id=kol_id,
        metadata={
            "suggestion_id": int(suggestion_id),
            "claim_id": _int(claim.get("id")),
            "claim_status": claim_status,
            "source_product_sku": row.get("source_product_sku"),
        },
    )
    updated = conn.execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    return {"suggestion": dict(updated) if updated else {}, "kol": kol, "claim": claim, "claim_status": claim_status}


def _short_link_url(slug: str) -> str:
    base_url = str(os.environ.get("VKPI_SHORTLINK_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip()
    return f"{base_url.rstrip('/')}/go/{slug}" if base_url else f"/go/{slug}"


def create_project_from_suggestion(suggestion_id: int, payload: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Claim a suggestion, create a project, and generate a Shopify short link."""
    payload = payload or {}
    claimed = claim_suggestion(suggestion_id, staff=staff)
    suggestion = claimed.get("suggestion") or {}
    kol = claimed.get("kol") or {}
    kol_id = _int(kol.get("id"))
    product_sku = str(payload.get("product_sku") or suggestion.get("source_product_sku") or "").strip()
    product_name = str(payload.get("product_name") or product_sku or "Viltrox Product").strip()
    handle = str(kol.get("channel_name") or suggestion.get("handle") or "KOL").strip()
    project = workflow.create_project(
        {
            "project_name": str(payload.get("project_name") or "").strip() or f"{product_name} KOL - {handle}",
            "kol_id": kol_id,
            "product_sku": product_sku,
            "product_name": product_name,
            "platform": suggestion.get("platform") or kol.get("platform") or "",
            "stage": str(payload.get("stage") or "discovery"),
            "source_type": "outreach_suggestion",
            "note": str(payload.get("note") or f"Created from suggestion {suggestion_id}"),
            "metadata": {
                "source": "outreach_suggestion",
                "suggestion_id": int(suggestion_id),
                "source_video_url": suggestion.get("source_video_url"),
                "source_video_title": suggestion.get("source_video_title"),
                "quality_score": suggestion.get("quality_score") or suggestion.get("score"),
            },
        },
        staff=staff,
    )
    project_id = _int(project.get("id"))
    if project_id and kol_id:
        get_conn().execute(
            "UPDATE vkpi_kol_claims SET project_id=COALESCE(project_id, ?), updated_at=? WHERE kol_id=? AND status='active'",
            (project_id, _utcnow(), kol_id),
        )

    link: dict[str, Any] = {}
    link_error = ""
    short_url = ""
    if project_id and payload.get("auto_create_link", True) is not False:
        destination_url = str(payload.get("destination_url") or payload.get("shopify_url") or os.environ.get("VKPI_DEFAULT_SHOPIFY_URL") or "https://www.viltrox.com/").strip()
        try:
            link = link_center.create_link(
                {
                    "destination_url": destination_url,
                    "link_type": "shopify",
                    "platform": "shopify",
                    "product_sku": product_sku,
                    "campaign_name": project.get("project_uid") or project.get("project_name") or "",
                    "kol_id": kol_id,
                    "project_id": project_id,
                    "status": "live",
                    "utm_source": suggestion.get("platform") or kol.get("platform") or "kol",
                    "utm_medium": "kol",
                    "utm_campaign": product_sku or "vkpi",
                    "utm_content": handle.lstrip("@"),
                    "metadata": {
                        "source": "outreach_suggestion",
                        "suggestion_id": int(suggestion_id),
                        "generated_for": "shopify",
                    },
                },
                staff=staff,
            )
            short_url = _short_link_url(str(link.get("slug") or ""))
            if short_url:
                get_conn().execute("UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?", (short_url, _utcnow(), project_id))
                project["shopify_link"] = short_url
        except Exception as exc:
            link_error = str(exc)

    now = _utcnow()
    metadata = _loads_json(suggestion.get("metadata_json"), {}) or {}
    metadata["project_bridge"] = {"project_id": project_id, "link_id": _int(link.get("id")), "link_error": link_error, "created_at": now}
    get_conn().execute("UPDATE vkpi_outreach_suggestions SET status='project_created', metadata_json=? WHERE id=?", (_json(metadata), int(suggestion_id)))
    get_conn().execute(
        "INSERT INTO vkpi_project_stage_events (project_id, from_stage, to_stage, event_type, actor_staff_id, note, source_ref_type, source_ref_id, effective_at, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, "suggestion", str(project.get("stage") or "discovery"), "suggestion_project_created", _actor(staff) or None, str(payload.get("note") or ""), "outreach_suggestion", str(suggestion_id), now, _json({"link_id": link.get("id"), "source": "outreach_suggestion"}), now),
    )
    get_conn().commit()
    audit.log_business_event(
        staff_id=_actor(staff),
        action_type="outreach_suggestion_create_project",
        target_type="project",
        target_id=project_id,
        metadata={"suggestion_id": int(suggestion_id), "kol_id": kol_id, "link_id": link.get("id"), "link_error": link_error},
    )
    updated = get_conn().execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    refreshed_claim = None
    if kol_id:
        refreshed_claim = get_conn().execute(
            "SELECT * FROM vkpi_kol_claims WHERE kol_id=? AND status='active' ORDER BY claimed_at DESC, id DESC LIMIT 1",
            (kol_id,),
        ).fetchone()
    return {
        "suggestion": dict(updated) if updated else {},
        "kol": kol,
        "claim": dict(refreshed_claim) if refreshed_claim else (claimed.get("claim") or {}),
        "project": project,
        "link": link,
        "short_url": short_url,
        "link_error": link_error,
        "adapter_status": "executed",
        "external_side_effect": True,
    }


def dismiss_suggestion(suggestion_id: int, reason: str = "", *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    actor = _actor(staff)
    get_conn().execute("UPDATE vkpi_outreach_suggestions SET status='dismissed', dismissed_by_staff_id=?, dismissed_at=?, dismissed_reason=? WHERE id=?", (actor or None, _utcnow(), str(reason or ""), int(suggestion_id)))
    get_conn().commit()
    row = get_conn().execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    return {"suggestion": dict(row) if row else {}}
