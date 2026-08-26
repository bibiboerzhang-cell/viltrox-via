"""单品播放总览「逐条重新实测」的只读投影(纯 SELECT,零 provider、零 LLM)。

单品播放表要让每一行单独重新实测,界面在点之前必须先回答三个问题:
  1. 这一行能不能点  —— 付费动作围栏(管理层 或 本人收藏;同事共享只读不给写);
  2. 这一行现在什么状态 —— 排队 / 进行中 / 被拦下 / 没成功 / 已完成,
     且「任务状态」与「数据新鲜度」分开,任务完成不等于读数已更新;
  3. 上次实测过去多久 —— 后端只给 ISO 时刻,相对时间由浏览器按本地时区渲染。

三者全部复用既有真源,不新造词表也不新造状态机:
  * my_kol_paid_action_access.target_write_context —— 与服务端写策略同源;
  * my_kol_video_recovery 的 TaskState 装配 —— 与内容墙 / KOL 详情字节一致;
  * video_metric_schedule.tier_for_evidence —— 采样档位(hot 6h / warm 24h /
    cold 7d)当新鲜度与「刚测过」判据,不另发明一套时间。

诚实口径:从未实测 = never(绝不当「刚刚」);任务失败不改实测时刻,所以失败
必须从任务态如实读出,不许靠实测时刻没变就一直显示「还没回来」。
红线:纯 SELECT;不入队、不写库;SQL 全 ? 占位;domains 层不 import workers/api。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.domains.kol import my_kol_paid_action_access as _access
from app.domains.kol import my_kol_video_recovery as _recovery
from app.domains.kol import video_metric_schedule as _schedule


# 一次 IN 查询的目标上限:与 my_kol_video_recovery.MAX_PAGE_SIZE 同口径。
# 单品播放一页最多 800 行,不分片会让第 201 行起静默变成「未请求过」的假象。
JOB_LOOKUP_CHUNK = _recovery.MAX_PAGE_SIZE
REFRESH_JOB_TYPE = _recovery.METRIC_JOB_TYPE
ACTIVE_REFRESH_STATUSES = _recovery.ACTIVE_TASK_STATUSES


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _chunked(values: list[int], size: int) -> Iterable[list[int]]:
    step = max(1, int(size))
    for start in range(0, len(values), step):
        yield values[start:start + step]


def latest_refresh_jobs(conn: Any, evidence_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """每条 evidence 最近一次重新实测任务(分片查询,绝不静默截断)。"""

    ids = sorted({_int(value) for value in evidence_ids if _int(value) > 0})
    jobs: dict[int, dict[str, Any]] = {}
    for chunk in _chunked(ids, JOB_LOOKUP_CHUNK):
        jobs.update(
            _recovery._latest_jobs_for_targets(
                conn,
                job_type=REFRESH_JOB_TYPE,
                target_ids=chunk,
            )
        )
    return jobs


def writable_by_kol(
    conn: Any,
    kol_pool_ids: Iterable[int],
    *,
    staff: dict[str, Any] | None,
) -> dict[int, dict[str, Any]]:
    """按 KOL 缓存一次围栏投影;同一 KOL 的多行共用,绝不逐行重算。"""

    contexts: dict[int, dict[str, Any]] = {}
    for value in kol_pool_ids:
        kol_pool_id = _int(value)
        if kol_pool_id <= 0 or kol_pool_id in contexts:
            continue
        try:
            contexts[kol_pool_id] = _access.target_write_context(
                conn,
                kol_pool_id=kol_pool_id,
                staff=staff,
            )
        except LookupError:
            contexts[kol_pool_id] = {
                "can_run_paid_actions": False,
                "reason": "kol_pool_not_found",
            }
    return contexts


def _freshness(measured_at: datetime | None, cadence_hours: int, now: datetime) -> str:
    if measured_at is None:
        return "never"
    age_hours = (now - measured_at).total_seconds() / 3600.0
    return "fresh" if age_hours < max(1, cadence_hours) else "stale"


def cadence_hours_for(row: dict[str, Any], now: datetime) -> int:
    """该视频的既有采样档位小时数(hot 6 / warm 24 / cold 168),不另发明数字。"""

    tier = _schedule.tier_for_evidence(row, now)
    cadence = _schedule.TIER_CADENCES.get(tier) or _schedule.TIER_CADENCES["cold"]
    return max(1, int(round(cadence.total_seconds() / 3600.0)))


def refresh_state(
    *,
    job: dict[str, Any] | None,
    measured_at: datetime | None,
    cadence_hours: int,
    tracking_status: str,
    sample_count: int,
    now: datetime,
) -> dict[str, Any]:
    """一行的重新实测任务态:复用 TaskState 契约,任务态与数据新鲜度分离。"""

    freshness = _freshness(measured_at, cadence_hours, now)
    return _recovery.metric_refresh_task_state(
        {
            "freshness": freshness,
            "last_success": {"fetched_at": _iso(measured_at)},
            "tracking_status": tracking_status or "unavailable",
            "sample_count": max(0, _int(sample_count)),
            "attempt_count": 0,
        },
        job,
    )


def annotate_items(
    conn: Any,
    items: list[dict[str, Any]],
    *,
    staff: dict[str, Any] | None,
    now: datetime,
) -> None:
    """就地补齐每行的 refresh / can_refresh / 冷却提示(纯读,不入队不写库)。

    每个 item 需要携带私有键 ``_measured_dt``(最近成功实测时刻)、``_publish_row``
    (发布时刻三列原样)、``_sample_count``(成功实测次数),补完由调用方剔除。
    """

    jobs = latest_refresh_jobs(conn, (item.get("evidence_id") for item in items))
    contexts = writable_by_kol(
        conn,
        (item.get("kol_pool_id") for item in items),
        staff=staff,
    )
    for item in items:
        evidence_id = _int(item.get("evidence_id"))
        measured_at = item.get("_measured_dt")
        cadence_hours = cadence_hours_for(item.get("_publish_row") or {}, now)
        item["refresh"] = refresh_state(
            job=jobs.get(evidence_id),
            measured_at=measured_at if isinstance(measured_at, datetime) else None,
            cadence_hours=cadence_hours,
            tracking_status=str(item.get("tracking_status") or ""),
            sample_count=_int(item.get("_sample_count")),
            now=now,
        )
        context = contexts.get(_int(item.get("kol_pool_id"))) or {
            "can_run_paid_actions": False,
            "reason": "my_kol_paid_action_write_forbidden",
        }
        item["can_refresh"] = bool(context.get("can_run_paid_actions"))
        item["refresh_forbidden_reason"] = (
            None if item["can_refresh"] else str(context.get("reason") or "") or None
        )
        item["refresh_cadence_hours"] = cadence_hours
        # 「刚测过」只是给操作员看的提示:后端不因此拒绝,界面也不假装禁用。
        item["recently_measured"] = bool(
            item["refresh"]["data"].get("freshness") == "fresh"
        )


def group_refresh_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    """单品行按钮要用的真数:可实测几条、几条还在路上。"""

    refreshable = sum(1 for item in items if item.get("can_refresh"))
    in_flight = sum(
        1
        for item in items
        if str((item.get("refresh") or {}).get("status") or "") in ACTIVE_REFRESH_STATUSES
    )
    return {"refreshable_videos": refreshable, "in_flight_videos": in_flight}


__all__ = [
    "ACTIVE_REFRESH_STATUSES",
    "JOB_LOOKUP_CHUNK",
    "REFRESH_JOB_TYPE",
    "annotate_items",
    "cadence_hours_for",
    "group_refresh_summary",
    "latest_refresh_jobs",
    "refresh_state",
    "writable_by_kol",
]
