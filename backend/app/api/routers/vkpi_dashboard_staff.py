"""V-KPI command center, staff, and dashboard routes."""
from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.domains import dashboard as dashboard_domain
from app.services.vkpi import audit, channels, decision_engine, kol_pool, metric_lineage, scope, workflow
from app.services.vkpi.country_coords import country_geo, resolve_country_code
from app.services.vkpi.workflow import staff_id as resolve_staff_id

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-dashboard"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


def _recent_content_sort_key(row: dict[str, Any]) -> float:
    raw = str(
        row.get("posted_at")
        or row.get("published_at")
        or row.get("detected_at")
        or row.get("captured_at")
        or row.get("created_at")
        or ""
    )
    if not raw:
        return 0.0
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0


def _dashboard_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _dashboard_official_matrix_summary(limit: int = 20) -> dict[str, Any]:
    try:
        matrix = channels.official_account_matrix(limit=limit)
    except Exception:
        return {}
    platforms = matrix.get("platforms") if isinstance(matrix.get("platforms"), list) else []
    return {
        "account_count": _dashboard_int(matrix.get("account_count")),
        "post_count": _dashboard_int(matrix.get("post_count")),
        "total_views": _dashboard_int(matrix.get("total_views")),
        "platform_count": len(platforms),
        "source": "official-channel-matrix",
    }


def _dashboard_recent_official_content(limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    try:
        from app.services.vkpi import channels

        matrix = channels.official_account_matrix(limit=limit)
        for platform in matrix.get("platforms") or []:
            for account in platform.get("accounts") or []:
                for post in account.get("posts") or []:
                    rows.append(
                        {
                            "content_kind": "official",
                            "title": post.get("title") or "官方内容",
                            "url": post.get("url") or post.get("canonical_url") or "",
                            "platform": post.get("platform") or account.get("platform") or platform.get("platform") or "",
                            "account_handle": account.get("handle") or "",
                            "account_display_name": account.get("display_name") or account.get("handle") or "",
                            "posted_at": post.get("posted_at") or post.get("published_at") or "",
                            "views": _dashboard_int(post.get("views") or post.get("total_views") or post.get("play_count")),
                            "likes": _dashboard_int(post.get("likes") or post.get("like_count")),
                            "comments": _dashboard_int(post.get("comments") or post.get("comment_count")),
                            "shares": _dashboard_int(post.get("shares") or post.get("share_count")),
                            "media_type": post.get("content_type") or post.get("media_type") or post.get("media_kind") or "",
                            "thumbnail_url": post.get("thumbnail_url") or post.get("thumbnail") or post.get("media_url") or "",
                            "source_table": "vkpi_employee_channel_metrics",
                            "source_id": post.get("canonical_post_uid") or post.get("provider_post_id") or post.get("id") or post.get("url") or "",
                        }
                    )
    except Exception:
        rows = []

    if rows:
        return sorted(rows, key=_recent_content_sort_key, reverse=True)[:limit]

    from app.db.repositories.viltrox_matrix import get_latest_viltrox_scan_bundle

    bundle = get_latest_viltrox_scan_bundle()
    for post in bundle.get("posts") or []:
        rows.append(
            {
                "content_kind": "official",
                "title": post.get("title") or "官方内容",
                "url": post.get("post_url") or "",
                "platform": post.get("platform") or "",
                "account_handle": post.get("handle") or "",
                "account_display_name": post.get("name") or post.get("handle") or "",
                "posted_at": post.get("published_at") or "",
                "views": int(post.get("views") or 0),
                "likes": int(post.get("likes") or 0),
                "comments": int(post.get("comments") or 0),
                "shares": int(post.get("shares") or 0),
                "media_type": post.get("content_type") or "",
                "source_table": "viltrox_matrix_scan_posts",
                "source_id": post.get("post_url") or f"{post.get('account_id')}:{post.get('published_at')}",
            }
        )
    return sorted(rows, key=_recent_content_sort_key, reverse=True)[:limit]


def _dashboard_recent_ugc_content(limit: int) -> list[dict[str, Any]]:
    from app.services.vkpi import brand_signal_detector

    payload = brand_signal_detector.list_brand_signals(status="new", limit=limit)
    rows: list[dict[str, Any]] = []
    for signal in payload.get("signals") or []:
        source_url = str(signal.get("source_url") or signal.get("post_url") or "").strip()
        if not source_url:
            continue
        rows.append(
            {
                "content_kind": "ugc",
                "title": signal.get("title") or signal.get("signal_type") or signal.get("match_context") or "品牌提及",
                "url": source_url,
                "platform": signal.get("platform") or signal.get("source_platform") or "",
                "account_handle": signal.get("author_handle") or signal.get("handle") or signal.get("kol_entity_uid") or "",
                "posted_at": signal.get("published_at") or signal.get("detected_at") or "",
                "views": int(signal.get("views") or signal.get("view_count") or signal.get("impressions") or 0),
                "likes": int(signal.get("likes") or signal.get("like_count") or 0),
                "comments": int(signal.get("comments") or signal.get("comment_count") or 0),
                "shares": int(signal.get("shares") or signal.get("share_count") or 0),
                "media_type": signal.get("content_type") or "",
                "source_table": "vkpi_brand_signal",
                "source_id": signal.get("id"),
            }
        )
    return sorted(rows, key=_recent_content_sort_key, reverse=True)[:limit]


@router.get("/architecture")
def architecture(staff=Depends(require_tab("vkpi", "read"))):
    return workflow.architecture_summary()


@router.get("/dashboard")
def dashboard(
    window_days: int = 30,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        effective_staff_id = scope.effective_staff_id(staff, staff_id)
        result = (
            decision_engine.dashboard_view("staff", window_days=window_days, staff_id=effective_staff_id)
            if effective_staff_id
            else decision_engine.dashboard(window_days=window_days)
        )
        lineage = metric_lineage.dashboard_metrics(
            period_days=window_days,
            staff=staff,
            staff_id=effective_staff_id,
            generated_by_staff_id=resolve_staff_id(staff) or None,
        )
        result["metric_run"] = lineage.get("run") or {}
        result["metrics"] = lineage.get("metrics") or []
        official_summary = _dashboard_official_matrix_summary(limit=20)
        if official_summary:
            result["official_matrix_summary"] = official_summary
            summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
            summary["official_account_count"] = official_summary["account_count"]
            summary["official_post_count"] = official_summary["post_count"]
            summary["official_total_views"] = official_summary["total_views"]
            result["summary"] = summary
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return result


@router.get("/dashboard/revenue-trend")
def dashboard_revenue_trend(
    window_days: int = 7,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return decision_engine.revenue_trend(
            window_days=window_days,
            staff_id=scope.effective_staff_id(staff, staff_id),
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/dashboard/product-performance")
def dashboard_product_performance(
    window_days: int = 30,
    staff_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return decision_engine.product_performance(
            window_days=window_days,
            staff_id=scope.effective_staff_id(staff, staff_id),
            limit=limit,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/dashboard/kol-distribution")
def dashboard_kol_distribution(
    limit: int = Query(default=200, ge=1, le=250),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return real KOL country distribution for the premium dashboard map."""
    del staff
    conn = get_conn()
    distribution = kol_pool._country_distribution(conn, limit=limit)
    countries_by_code: dict[str, dict] = {}
    unmapped: list[dict] = []
    for row in distribution:
        raw_values = row.get("raw_values") if isinstance(row.get("raw_values"), list) else []
        code = resolve_country_code(row.get("country_code"), row.get("country_name"), *raw_values)
        geo = country_geo(code)
        if not geo:
            unmapped.append(row)
            continue
        item = countries_by_code.setdefault(
            geo["code"],
            {
                **geo,
                "count": 0,
                "share": 0.0,
                "exposure": 0,
                "platforms": [],
                "raw_values": [],
            },
        )
        item["count"] += int(row.get("kol_count") or 0)
        for raw in raw_values:
            if raw not in item["raw_values"]:
                item["raw_values"].append(raw)

    try:
        platform_rows = conn.execute(
            """
            SELECT country, platform, COUNT(*) AS n, COALESCE(SUM(avg_views), 0) AS exposure
            FROM vkpi_kol_pool
            WHERE country IS NOT NULL AND TRIM(country) != ''
            GROUP BY country, platform
            """
        ).fetchall()
    except Exception:
        platform_rows = []
    platform_buckets: dict[str, dict[str, dict[str, int]]] = {}
    for row in platform_rows:
        row_data = dict(row)
        raw_country = str(row_data.get("country") or "").strip()
        code = resolve_country_code(raw_country)
        geo = country_geo(code)
        if not geo or geo["code"] not in countries_by_code:
            continue
        platform = str(row_data.get("platform") or "unknown").strip() or "unknown"
        item = platform_buckets.setdefault(geo["code"], {}).setdefault(platform, {"count": 0, "exposure": 0})
        item["count"] += _dashboard_int(row_data.get("n"))
        item["exposure"] += _dashboard_int(row_data.get("exposure"))
    for code, platform_map in platform_buckets.items():
        country_item = countries_by_code.get(code)
        if not country_item:
            continue
        platforms = [
            {"platform": platform, "count": values["count"], "exposure": values["exposure"]}
            for platform, values in platform_map.items()
        ]
        platforms.sort(key=lambda item: (-int(item["count"] or 0), str(item["platform"])))
        country_item["platforms"] = platforms
        country_item["exposure"] = sum(int(item["exposure"] or 0) for item in platforms)

    mapped_kol_count = sum(int(item["count"] or 0) for item in countries_by_code.values())
    source_country_kol_count = mapped_kol_count + sum(int(item.get("kol_count") or 0) for item in unmapped)
    try:
        total_pool_rows = int((conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone() or {})["n"] or 0)
    except Exception:
        total_pool_rows = 0
    countries = sorted(countries_by_code.values(), key=lambda item: (-int(item["count"] or 0), str(item["code"])))
    for item in countries:
        item["share"] = round((int(item["count"] or 0) / mapped_kol_count) * 100, 2) if mapped_kol_count else 0.0

    return {
        "total_kol": mapped_kol_count,
        "mapped_kol_count": mapped_kol_count,
        "source_country_kol_count": source_country_kol_count,
        "total_pool_rows": total_pool_rows,
        "missing_country_count": max(0, total_pool_rows - source_country_kol_count),
        "countries": countries,
        "country_count": len(countries),
        "unmapped_count": len(unmapped),
        "unmapped_kol_count": source_country_kol_count - mapped_kol_count,
        "unmapped_sample": unmapped[:10],
        "data_source": "vkpi_kol_pool.country",
        "is_real": True,
    }


@router.get("/dashboard/agents-status")
def dashboard_agents_status(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only dashboard status for existing V-KPI agents."""
    del staff
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()
        kol_pool_total = int((row or {})["n"] or 0)
    except Exception:
        kol_pool_total = 0
    return dashboard_domain._build_dashboard_agents_status(kol_pool_total=kol_pool_total)


@router.get("/dashboard/copilot-brief")
def dashboard_copilot_brief(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return the latest read-only brief-agent artifact for Dashboard Copilot."""
    del staff
    return dashboard_domain._build_dashboard_copilot_brief()


@router.get("/dashboard/tasks")
def dashboard_tasks(
    limit: int = Query(default=6, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return dashboard task candidates from the latest recommendation-agent artifact."""
    del staff
    return dashboard_domain._build_dashboard_tasks(limit=limit)


@router.get("/dashboard/agents/inbox")
def dashboard_agents_inbox(
    limit: int = Query(default=50, ge=1, le=100),
    agent_id: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only inbox items from existing runtime/ops agent artifacts."""
    del staff
    return dashboard_domain._build_dashboard_agents_inbox(limit=limit, agent_id=agent_id)


@router.get("/dashboard/recent-content")
def dashboard_recent_content(
    limit: int = Query(default=12, ge=1, le=30),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return recent content rows for the glass dashboard content panel."""
    del staff
    official = _dashboard_recent_official_content(limit=limit)
    ugc = _dashboard_recent_ugc_content(limit=limit)
    items = sorted([*official, *ugc], key=_recent_content_sort_key, reverse=True)[:limit]
    counts: dict[str, int] = {}
    for item in items:
        kind = str(item.get("content_kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "items": items,
        "count": len(items),
        "kind_counts": counts,
        "sources": ["viltrox_matrix_scan_posts", "vkpi_brand_signal"],
        "is_real": True,
    }


@router.get("/staff-directory")
def staff_directory(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return decision_engine.staff_directory()


@router.get("/staff/{staff_id}/profile")
def staff_profile(
    staff_id: int,
    window: str = Query(default="month", pattern="^(today|day|daily|1d|7d|week|weekly|30d|month|monthly)$"),
    limit: int = Query(default=80, ge=1, le=300),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        result = decision_engine.staff_profile(staff_id, staff=staff, window=window, limit=limit)
        audit.log_sensitive_access(
            staff_id=resolve_staff_id(staff),
            action_type="view_staff_profile",
            resource_type="staff",
            resource_id=str(staff_id),
            page_path=f"/api/admin/vkpi/staff/{staff_id}/profile",
            metadata={"window": window, "limit": limit, "costs_visible": result.get("visibility", {}).get("costs_visible")},
        )
        audit.log_business_event(
            staff_id=resolve_staff_id(staff),
            action_type="staff_profile_view",
            target_type="staff",
            target_id=staff_id,
            detail="view employee V-KPI profile",
            metadata={"window": window, "limit": limit},
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/staff-kpi")
def staff_kpi(
    window: str = Query(default="month", pattern="^(today|day|daily|1d|7d|week|weekly|30d|month|monthly)$"),
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return decision_engine.staff_kpi(window=window, staff_id=scope.effective_staff_id(staff, staff_id))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/employee-workspace")
def employee_workspace(
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    effective_staff_id = scope.effective_staff_id(staff, staff_id) or resolve_staff_id(staff)
    return decision_engine.employee_workspace(int(effective_staff_id or 0))


@router.get("/dashboard/view/{view}")
def dashboard_view(
    view: str,
    window_days: int = 30,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        requested_staff_id = staff_id if staff_id is not None else staff_id_from_context(view, staff)
        effective_staff_id = scope.effective_staff_id(staff, requested_staff_id)
        result = decision_engine.dashboard_view(view, window_days=window_days, staff_id=effective_staff_id)
        try:
            lineage = metric_lineage.dashboard_metrics(
                period_days=window_days,
                staff=staff,
                staff_id=effective_staff_id,
                generated_by_staff_id=resolve_staff_id(staff) or None,
            )
            result["metric_run"] = lineage.get("run") or {}
            result["metrics"] = lineage.get("metrics") or []
        except Exception:
            result["metric_run"] = {}
            result["metrics"] = []
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


def staff_id_from_context(view: str, staff: dict) -> int | None:
    if str(view or "").strip().lower() in {"staff", "employee"}:
        return resolve_staff_id(staff) or None
    return None


@router.get("/workflow/stages")
def stages(staff=Depends(require_tab("vkpi", "read"))):
    return workflow.stage_config()
