"""MY KOL 数值跟进:被追踪视频的快照趋势(纯读、零 LLM、零 provider)。

数据源三件套(迁移 283/284/285):
  vkpi_kol_video_metric_tracking   —— 显式追踪订阅(active/paused)
  vkpi_content_metric_snapshots    —— 追加式观测账本(success/failed/legacy_current_only)
  vkpi_kol_video_evidence          —— latest 读模型(标题/平台/发布时间)

口径:
  * 7 天 / 30 天增量 = 最近一次 success 快照 − 基线快照;基线 = 窗口起点之前
    最近的一次 success 快照;找不到那么老的基线但有更早样本 → partial(如实标注
    实际覆盖天数);只有 1 个样本 → insufficient_history。
  * 日均增速 = 增量 / 实际覆盖天数(覆盖不足 1 天不外推,给 None)。
  * legacy_current_only 行不参与趋势(它是迁移基线,不是实测)。
  * 「下次刷新」只是按调度层级(hot 6h / warm 24h / cold 7d + 失败退避 24h)
    的估算;调度闸关闭时如实给 scheduler_disabled,不给假时间。
红线:纯 SELECT;绝不写 viltrox_fit_score / 不触 rule_v0;SQL 全 ? 占位。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.domains.content_metric_snapshots import (
    MAX_SNAPSHOTS_PER_EVIDENCE,
    _parse_timestamp,
    metric_or_none,
)
from app.domains.kol.video_metric_schedule import (
    FAILED_REFRESH_BACKOFF,
    TASK_KEY,
    TIER_CADENCES,
    _first_utc,
    _tier_for_publish_time,
)


CONTRACT = "my_kol_metric_trends_v1"
WINDOW_DAYS: dict[str, int] = {"7d": 7, "30d": 30}
METRIC_KEYS: tuple[str, ...] = ("views", "likes", "comments")
SCHEDULER_INTERVAL_HOURS = 1
MAX_ITEMS = 100
MAX_SERIES_POINTS = MAX_SNAPSHOTS_PER_EVIDENCE


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "t", "yes", "y", "on"}


def scheduler_gate(conn: Any) -> dict[str, Any]:
    """读 scheduler_tasks 注册表闸状态;表缺/读失败 → enabled=None(不装开)。"""

    enabled: bool | None = None
    try:
        row = conn.execute(
            "SELECT enabled FROM scheduler_tasks WHERE task_key=? LIMIT 1",
            (TASK_KEY,),
        ).fetchone()
        if row is not None:
            enabled = _truthy(dict(row).get("enabled"))
    except Exception:
        enabled = None
    return {
        "task_key": TASK_KEY,
        "enabled": enabled,
        "interval_hours": SCHEDULER_INTERVAL_HOURS,
        "cadence_hours": {
            tier: int(delta.total_seconds() // 3600) for tier, delta in TIER_CADENCES.items()
        },
        "failed_backoff_hours": int(FAILED_REFRESH_BACKOFF.total_seconds() // 3600),
        "provider_calls_performed": False,
    }


def _window_delta(
    successful: list[dict[str, Any]],
    *,
    latest: dict[str, Any],
    latest_at: datetime,
    days: int,
    metric: str,
) -> dict[str, Any]:
    latest_value = metric_or_none(latest.get(metric))
    if latest_value is None:
        return {"delta": None, "daily_avg": None, "status": "metric_missing", "covered_days": None, "baseline_at": None, "baseline_value": None}
    target = latest_at - timedelta(days=days)
    baseline: dict[str, Any] | None = None
    oldest: dict[str, Any] | None = None
    for row in successful:
        if metric_or_none(row.get(metric)) is None:
            continue
        at = _parse_timestamp(row.get("fetched_at"))
        if at is None or at >= latest_at:
            continue
        if oldest is None or at < (_parse_timestamp(oldest.get("fetched_at")) or at):
            oldest = row
        if at <= target and (baseline is None or at > (_parse_timestamp(baseline.get("fetched_at")) or at)):
            baseline = row
    status = "ready"
    if baseline is None:
        if oldest is None:
            return {"delta": None, "daily_avg": None, "status": "insufficient_history", "covered_days": None, "baseline_at": None, "baseline_value": None}
        baseline = oldest
        status = "partial"
    baseline_at = _parse_timestamp(baseline.get("fetched_at"))
    baseline_value = metric_or_none(baseline.get(metric))
    if baseline_at is None or baseline_value is None:
        return {"delta": None, "daily_avg": None, "status": "insufficient_history", "covered_days": None, "baseline_at": None, "baseline_value": None}
    covered = (latest_at - baseline_at).total_seconds() / 86400.0
    delta = latest_value - baseline_value
    daily_avg = round(delta / covered, 2) if covered >= 1.0 else None
    return {
        "delta": delta,
        "daily_avg": daily_avg,
        "status": status,
        "covered_days": round(covered, 2),
        "baseline_at": _iso(baseline_at),
        "baseline_value": baseline_value,
    }


def _snapshot_point(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fetched_at": _iso(_parse_timestamp(row.get("fetched_at"))),
        "status": _text(row.get("status")),
        "views": metric_or_none(row.get("views")),
        "likes": metric_or_none(row.get("likes")),
        "comments": metric_or_none(row.get("comments")),
        "shares": metric_or_none(row.get("shares")),
        "error_code": _text(row.get("error_code")) or None,
    }


def _next_refresh(
    *,
    tracking_status: str,
    gate_enabled: bool | None,
    published_at: datetime | None,
    last_attempt_at: datetime | None,
    last_failed: bool,
    now: datetime,
) -> dict[str, Any]:
    tier = _tier_for_publish_time(published_at, now)
    cadence = TIER_CADENCES[tier]
    if last_failed:
        cadence = max(cadence, FAILED_REFRESH_BACKOFF)
    if tracking_status != "active":
        return {"tier": tier, "estimated_at": None, "reason": "tracking_paused"}
    if gate_enabled is not True:
        return {
            "tier": tier,
            "estimated_at": None,
            "reason": "scheduler_disabled" if gate_enabled is False else "scheduler_state_unknown",
        }
    due_at = (last_attempt_at + cadence) if last_attempt_at else now
    # 调度每小时扫一遍,真正入队时刻在 due 之后的下一整点扫描;估算只给「不早于」。
    return {"tier": tier, "estimated_at": _iso(max(due_at, now)), "reason": "estimated_by_cadence"}


def build_tracked_item(
    tracking: dict[str, Any],
    snapshots: list[dict[str, Any]],
    *,
    now: datetime,
    gate_enabled: bool | None,
) -> dict[str, Any]:
    ordered = sorted(
        snapshots,
        key=lambda row: (
            _parse_timestamp(row.get("fetched_at")) or datetime.min.replace(tzinfo=timezone.utc),
            _int(row.get("id")),
        ),
    )
    successful = [row for row in ordered if _text(row.get("status")) == "success"]
    failed = [row for row in ordered if _text(row.get("status")) == "failed"]
    latest = successful[-1] if successful else None
    latest_at = _parse_timestamp(latest.get("fetched_at")) if latest else None
    last_attempt = ordered[-1] if ordered else None
    windows: dict[str, Any] = {}
    for label, days in WINDOW_DAYS.items():
        if latest is None or latest_at is None:
            windows[label] = {
                metric: {"delta": None, "daily_avg": None, "status": "insufficient_history", "covered_days": None, "baseline_at": None, "baseline_value": None}
                for metric in METRIC_KEYS
            }
            continue
        windows[label] = {
            metric: _window_delta(successful, latest=latest, latest_at=latest_at, days=days, metric=metric)
            for metric in METRIC_KEYS
        }
    published_at = _first_utc(
        tracking.get("published_at_norm"),
        tracking.get("publish_date"),
        tracking.get("posted_at"),
    )
    last_attempt_at = max(
        [value for value in (
            _parse_timestamp(last_attempt.get("fetched_at")) if last_attempt else None,
            _parse_timestamp(tracking.get("last_enqueued_at")),
        ) if value is not None],
        default=None,
    )
    tracking_status = _text(tracking.get("status")) or "active"
    if latest is None:
        history = "never_measured"
    elif len(successful) < 2:
        history = "single_sample"
    else:
        history = "ready"
    return {
        "evidence_id": _int(tracking.get("evidence_id")),
        "kol_pool_id": _int(tracking.get("kol_pool_id")),
        "kol_name": _text(tracking.get("kol_name")),
        "title": _text(tracking.get("video_title")) or _text(tracking.get("title")),
        "content_url": _text(tracking.get("content_url")),
        "platform": _text(tracking.get("platform")).lower(),
        "published_at": _iso(published_at),
        "tracking": {
            "status": tracking_status,
            "source": _text(tracking.get("source")),
            "pause_reason": _text(tracking.get("pause_reason")),
            "last_enqueued_at": _iso(_parse_timestamp(tracking.get("last_enqueued_at"))),
            "last_enqueue_status": _text(tracking.get("last_enqueue_status")),
            "history": history,
            "next_refresh": _next_refresh(
                tracking_status=tracking_status,
                gate_enabled=gate_enabled,
                published_at=published_at,
                last_attempt_at=last_attempt_at,
                last_failed=bool(last_attempt) and _text(last_attempt.get("status")) == "failed",
                now=now,
            ),
        },
        "latest": _snapshot_point(latest) if latest else None,
        "last_attempt": _snapshot_point(last_attempt) if last_attempt else None,
        "sample_count": len(successful),
        "failed_count": len(failed),
        "attempt_count": len(ordered),
        "history_capped": len(ordered) >= MAX_SERIES_POINTS,
        "windows": windows,
        "series": [_snapshot_point(row) for row in successful[-MAX_SERIES_POINTS:]],
    }


def _snapshots_for(conn: Any, evidence_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({_int(value) for value in evidence_ids if _int(value) > 0})
    grouped: dict[int, list[dict[str, Any]]] = {evidence_id: [] for evidence_id in ids}
    if not ids:
        return grouped
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT id, evidence_id, fetched_at, views, likes, comments, shares,
                   status, error_code,
                   ROW_NUMBER() OVER (
                       PARTITION BY evidence_id ORDER BY fetched_at DESC, id DESC
                   ) AS row_num
            FROM vkpi_content_metric_snapshots
            WHERE evidence_id IN ({placeholders})
              AND status IN ('success', 'failed')
        )
        SELECT id, evidence_id, fetched_at, views, likes, comments, shares, status, error_code
        FROM ranked
        WHERE row_num <= ?
        ORDER BY evidence_id, fetched_at ASC, id ASC
        """,
        tuple(ids) + (MAX_SERIES_POINTS,),
    ).fetchall()
    for row in rows:
        item = dict(row)
        evidence_id = _int(item.get("evidence_id"))
        if evidence_id in grouped:
            grouped[evidence_id].append(item)
    return grouped


_TRACKING_SELECT = """
    SELECT t.evidence_id, t.tracked_by_staff_id, t.status, t.source,
           t.last_enqueued_at, t.last_enqueue_status, t.pause_reason,
           t.created_at AS tracked_since,
           e.kol_pool_id, e.content_url, e.platform, e.title, e.video_title,
           e.published_at_norm, e.publish_date, e.posted_at,
           COALESCE(NULLIF(kp.display_name, ''), kp.handle, '') AS kol_name
    FROM vkpi_kol_video_metric_tracking t
    JOIN vkpi_kol_video_evidence e ON e.id=t.evidence_id
    LEFT JOIN vkpi_kol_pool kp ON kp.id=e.kol_pool_id
"""


def _summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "tracked_total": len(items),
        "active": sum(1 for item in items if item["tracking"]["status"] == "active"),
        "paused": sum(1 for item in items if item["tracking"]["status"] == "paused"),
        "measured": sum(1 for item in items if item["latest"] is not None),
        "with_history": sum(1 for item in items if item["tracking"]["history"] == "ready"),
        "views_latest_total": None,
        "windows": {},
    }
    measured_views = [item["latest"]["views"] for item in items if item["latest"] and item["latest"]["views"] is not None]
    if measured_views:
        summary["views_latest_total"] = sum(measured_views)
    for label in WINDOW_DAYS:
        block: dict[str, Any] = {}
        for metric in METRIC_KEYS:
            deltas = [
                item["windows"][label][metric]["delta"]
                for item in items
                if item["windows"][label][metric]["status"] in {"ready", "partial"}
                and item["windows"][label][metric]["delta"] is not None
            ]
            block[metric] = {"delta": sum(deltas) if deltas else None, "videos": len(deltas)}
        summary["windows"][label] = block
    return summary


def _build(
    conn: Any,
    rows: list[dict[str, Any]],
    *,
    now: datetime | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], datetime]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    gate = scheduler_gate(conn)
    snapshots = _snapshots_for(conn, (row.get("evidence_id") for row in rows))
    items = [
        build_tracked_item(row, snapshots.get(_int(row.get("evidence_id")), []), now=current, gate_enabled=gate["enabled"])
        for row in rows
    ]
    return items, gate, current


def tracked_video_trends(
    conn: Any,
    *,
    kol_pool_id: int,
    now: datetime | None = None,
    limit: int = MAX_ITEMS,
) -> dict[str, Any]:
    """单 KOL 的被追踪视频趋势(调用方已做 assert_target_readable)。"""

    bound = max(1, min(MAX_ITEMS, _int(limit) or MAX_ITEMS))
    rows = [
        dict(row)
        for row in conn.execute(
            _TRACKING_SELECT
            + """
        WHERE e.kol_pool_id=? AND e.is_active IS NOT FALSE
        ORDER BY t.updated_at DESC, t.evidence_id DESC
        LIMIT ?
        """,
            (int(kol_pool_id), bound + 1),
        ).fetchall()
    ]
    truncated = len(rows) > bound
    rows = rows[:bound]
    items, gate, current = _build(conn, rows, now=now)
    return {
        "contract": CONTRACT,
        "kol_pool_id": int(kol_pool_id),
        "read_only": True,
        "generated_at": _iso(current),
        "scheduler": gate,
        "summary": _summarize(items),
        "items": items,
        "truncated": truncated,
        "empty_reason": None if items else "no_tracked_videos",
        "windows": WINDOW_DAYS,
    }


def tracked_video_overview(
    conn: Any,
    *,
    staff_scope_id: int | None,
    now: datetime | None = None,
    limit: int = MAX_ITEMS,
) -> dict[str, Any]:
    """收藏集(收藏 ∪ 授权共享)口径的被追踪视频总览;0=管理层全团队。"""

    sid = _int(staff_scope_id)
    bound = max(1, min(MAX_ITEMS, _int(limit) or MAX_ITEMS))
    rows = [
        dict(row)
        for row in conn.execute(
            _TRACKING_SELECT
            + """
        WHERE e.is_active IS NOT FALSE
          AND (
            EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f
                    WHERE f.kol_pool_id = e.kol_pool_id AND (? = 0 OR f.staff_id = ?))
            OR EXISTS (SELECT 1 FROM vkpi_kol_pool_members sm
                       WHERE sm.kol_pool_id = e.kol_pool_id AND (? = 0 OR sm.staff_id = ?))
          )
        ORDER BY t.updated_at DESC, t.evidence_id DESC
        LIMIT ?
        """,
            (sid, sid, sid, sid, bound + 1),
        ).fetchall()
    ]
    truncated = len(rows) > bound
    rows = rows[:bound]
    items, gate, current = _build(conn, rows, now=now)
    return {
        "contract": CONTRACT,
        "read_only": True,
        "generated_at": _iso(current),
        "scope": {
            "mode": "staff_collection" if sid else "team_collection",
            "staff_scope_id": sid or None,
            "membership": "favorite_or_authorized_share",
        },
        "scheduler": gate,
        "summary": _summarize(items),
        "items": items,
        "truncated": truncated,
        "empty_reason": None if items else "no_tracked_videos",
        "windows": WINDOW_DAYS,
    }


__all__ = [
    "CONTRACT",
    "WINDOW_DAYS",
    "build_tracked_item",
    "scheduler_gate",
    "tracked_video_overview",
    "tracked_video_trends",
]
