"""按产品聚合的被追踪视频播放总览(波 D·B 车道,纯读、零 LLM、零 provider)。

回答「每个 SKU 下我关注的视频播放跑得怎么样」:
  vkpi_kol_video_product_links × vkpi_kol_video_metric_tracking ×
  vkpi_kol_video_evidence 三表相交 = 「有产品归属且在数据关注中」的视频,
  按 SKU 分组;播放数/点赞数/实测时间取 vkpi_content_metric_snapshots 最近一次
  success 快照;d1/d7/d30 增量口径复用 video_tracking_trends._window_delta
  (基线=窗口起点前最近 success;legacy_current_only 不算实测)。

可见范围与数值跟进总览同款收藏集口径(收藏 ∪ 授权共享):员工恒被
scope.effective_staff_id 压回本人,管理层缺省全团队、?staff_id= 看指定成员。
诚实空态:未实测一律 null(绝不编 0);total_views 只汇总实测过的视频。
红线:纯 SELECT;绝不触 viltrox_fit_score / rule_v0;SQL 全 ? 占位。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domains.access import scope
from app.domains.content_metric_snapshots import _parse_timestamp, metric_or_none
from app.domains.kol import video_tracking_trends as _trends


CONTRACT = "my_kol_sku_play_overview_v1"
WINDOW_DAYS: dict[str, int] = {"d1": 1, "d7": 7, "d30": 30}
MAX_ITEMS = 800

_TS_FLOOR = datetime.min.replace(tzinfo=timezone.utc)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _tracked_link_rows(conn: Any, sid: int, *, cap: int) -> tuple[list[dict[str, Any]], bool]:
    """SKU×被追踪视频行;DISTINCT 吞掉同 SKU 多 relation_type 的重复关联。"""

    rows = [
        dict(row)
        for row in conn.execute(
            """
        SELECT DISTINCT l.product_sku, l.evidence_id, e.kol_pool_id,
               COALESCE(e.content_url, '') AS content_url,
               LOWER(COALESCE(e.platform, '')) AS platform,
               COALESCE(NULLIF(e.video_title, ''), e.title, '') AS video_title,
               COALESCE(t.status, '') AS tracking_status,
               CASE
                 WHEN EXISTS (SELECT 1 FROM vkpi_kol_video_product_links lx
                              WHERE lx.evidence_id=l.evidence_id AND lx.product_sku=l.product_sku
                                AND lx.relation_type='confirmed') THEN 'confirmed'
                 WHEN EXISTS (SELECT 1 FROM vkpi_kol_video_product_links lx
                              WHERE lx.evidence_id=l.evidence_id AND lx.product_sku=l.product_sku
                                AND lx.relation_type='manual') THEN 'manual'
                 ELSE 'detected'
               END AS link_relation_type,
               COALESCE(NULLIF(kp.display_name, ''), kp.handle, '') AS kol_name,
               COALESCE(NULLIF(p.marketing_name, ''), NULLIF(p.model_name, ''), l.product_sku) AS sku_name
        FROM vkpi_kol_video_product_links l
        JOIN vkpi_kol_video_metric_tracking t ON t.evidence_id = l.evidence_id
        JOIN vkpi_kol_video_evidence e ON e.id = l.evidence_id
        LEFT JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
        LEFT JOIN vkpi_products p ON p.sku = l.product_sku
        WHERE e.is_active IS NOT FALSE
          AND (
            EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f
                    WHERE f.kol_pool_id = e.kol_pool_id AND (? = 0 OR f.staff_id = ?))
            OR EXISTS (SELECT 1 FROM vkpi_kol_pool_members sm
                       WHERE sm.kol_pool_id = e.kol_pool_id AND (? = 0 OR sm.staff_id = ?))
          )
        ORDER BY l.product_sku, l.evidence_id DESC
        LIMIT ?
        """,
            (sid, sid, sid, sid, int(cap) + 1),
        ).fetchall()
    ]
    truncated = len(rows) > cap
    return rows[:cap], truncated


def _measure_evidence(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """单视频最近实测 + d1/d7/d30 播放增量;从未实测一律 null,绝不编 0。"""

    ordered = sorted(
        snapshots,
        key=lambda row: (
            _parse_timestamp(row.get("fetched_at")) or _TS_FLOOR,
            _int(row.get("id")),
        ),
    )
    successful = [row for row in ordered if _text(row.get("status")) == "success"]
    latest = successful[-1] if successful else None
    latest_at = _parse_timestamp(latest.get("fetched_at")) if latest else None
    if latest is None or latest_at is None:
        return {
            "view_count": None,
            "like_count": None,
            "measured_at": None,
            "measured_dt": None,
            "delta": {label: None for label in WINDOW_DAYS},
        }
    delta = {
        label: _trends._window_delta(
            successful, latest=latest, latest_at=latest_at, days=days, metric="views"
        )["delta"]
        for label, days in WINDOW_DAYS.items()
    }
    return {
        "view_count": metric_or_none(latest.get("views")),
        "like_count": metric_or_none(latest.get("likes")),
        "measured_at": _iso(latest_at),
        "measured_dt": latest_at,
        "delta": delta,
    }


def _sum_or_none(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _build_group(sku_code: str, sku_name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    measured_dts = [item["_measured_dt"] for item in items if item["_measured_dt"] is not None]
    latest_dt = max(measured_dts) if measured_dts else None
    group = {
        "sku_code": sku_code,
        "sku_name": sku_name,
        "videos": len(items),
        "kols": len({item["kol_pool_id"] for item in items}),
        "latest_measured_at": _iso(latest_dt),
        "total_views": _sum_or_none([item["view_count"] for item in items]),
        "delta": {
            label: _sum_or_none([item["delta"][label] for item in items])
            for label in WINDOW_DAYS
        },
        "items": items,
        "_latest_dt": latest_dt,
    }
    items.sort(key=lambda it: (it["_measured_dt"] or _TS_FLOOR, it["evidence_id"]), reverse=True)
    return group


def build_sku_play_overview(
    conn: Any,
    *,
    staff: dict[str, Any] | None,
    staff_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """GET /my-kol/sku-play-overview 主体(调用方已做身份 403 检查;纯 SELECT)。"""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    sid = _int(scope.effective_staff_id(staff, staff_id))
    rows, truncated = _tracked_link_rows(conn, sid, cap=MAX_ITEMS)
    measured_by_evidence = {
        evidence_id: _measure_evidence(snapshots)
        for evidence_id, snapshots in _trends._snapshots_for(
            conn, (row.get("evidence_id") for row in rows)
        ).items()
    }

    grouped: dict[str, list[dict[str, Any]]] = {}
    sku_names: dict[str, str] = {}
    for row in rows:
        sku_code = _text(row.get("product_sku"))
        evidence_id = _int(row.get("evidence_id"))
        if not sku_code or evidence_id <= 0:
            continue
        measure = measured_by_evidence.get(evidence_id) or _measure_evidence([])
        sku_names.setdefault(sku_code, _text(row.get("sku_name")) or sku_code)
        grouped.setdefault(sku_code, []).append({
            "evidence_id": evidence_id,
            "kol_pool_id": _int(row.get("kol_pool_id")),
            "kol_name": _text(row.get("kol_name")),
            "platform": _text(row.get("platform")),
            "title": _text(row.get("video_title")),
            "content_url": _text(row.get("content_url")),
            "view_count": measure["view_count"],
            "like_count": measure["like_count"],
            "measured_at": measure["measured_at"],
            "delta": dict(measure["delta"]),
            "tracking_status": _text(row.get("tracking_status")),
            "link_relation_type": _text(row.get("link_relation_type")),
            "_measured_dt": measure["measured_dt"],
        })

    groups = [
        _build_group(sku_code, sku_names[sku_code], items)
        for sku_code, items in grouped.items()
    ]
    # 分组排序:最近实测在前(nulls last),同刻按 SKU 码;两次稳定排序实现。
    groups.sort(key=lambda group: group["sku_code"])
    groups.sort(key=lambda group: group["_latest_dt"] or _TS_FLOOR, reverse=True)

    evidence_ids = {item["evidence_id"] for items in grouped.values() for item in items}
    measured_ids = {
        item["evidence_id"]
        for items in grouped.values()
        for item in items
        if item["_measured_dt"] is not None
    }
    kol_ids = {item["kol_pool_id"] for items in grouped.values() for item in items}
    for group in groups:
        group.pop("_latest_dt", None)
        for item in group["items"]:
            item.pop("_measured_dt", None)

    return {
        "contract": CONTRACT,
        "generated_at": _iso(current),
        "summary": {
            "skus": len(groups),
            "videos": len(evidence_ids),
            "kols": len(kol_ids),
            "measured_videos": len(measured_ids),
        },
        "groups": groups,
        "truncated": truncated,
        "empty_reason": None if groups else "no_tracked_sku_videos",
    }


__all__ = ["CONTRACT", "MAX_ITEMS", "WINDOW_DAYS", "build_sku_play_overview"]
