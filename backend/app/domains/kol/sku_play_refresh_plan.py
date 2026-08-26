"""单品播放「重新实测」的报价器 + 硬闸(纯读:零入队、零 provider、零写库)。

一句话:**点下去之前,先把「这次要去平台取几次数」算清楚,并且把上限算在服务端。**

为什么必须有这一层(2026-08-25 对抗复核坐实的 HIGH):单品行的「重新实测 N 条」
原本没有单次上限、没有每日上限、没有冷却,二次确认只报个数字不封顶——绕开前端
就是一个无上限的批量花钱按钮。历史教训在案:Apify 单账号 $199 曾被烧到只剩 $3.4。

三道闸的口径**照抄同波内容墙侧**(``my_kol_wall_fetch_plan``),不另发明一套:
  * 单次上限 per_click = 12(env 可调,硬顶 30);
  * 每日上限 daily     = 40(env 可调,硬顶 120);
  * 冷却     cooldown  = 6 小时(env 可调,硬顶 168)。
闸一律在**服务端**判:前端只负责把服务端算出来的数字如实显示。

计量单位 = 真实取数次数。已核实:一条视频的重新实测在 worker 里只走一次
``_fetch_video_metadata``(见 video_metric_refresh.run_video_metric_refresh_for_job),
三个平台都是**一条视频一次**,所以 ``fetch_per_video = 1``、
``fetch_calls_total = planned_count``,不笼统写「各取一次」再让人自己猜。

冷却用真源(不用任何"看着像"的列):
  ① 最近一次**成功实测**(vkpi_content_metric_snapshots.status='success');
  ② 本车道自己近期入队(apify_jobs.payload->>'source' = 本车道 source)——
     结果还没回来时的连点保护。
另外「已经在队列里/进行中」的行单列一档 ``already_in_flight``:它们不会重复排、
不产生新花费,所以**不计入**报价数字,但要如实报出来,不许静默消失。

「最近 24 小时 / 冷却窗内」两个窗口都从同一次近期任务读里切,读的那一句走方言分支:
PG 用 ``make_interval``(时间收口在库侧),本地 sqlite 假库没有它,改用调用方算好的
ISO 边界文本比较(与 ``my_kol_video_recovery._is_sqlite`` 同款判据)。这样同一份闸
逻辑既在 PG 正确,又能被 sqlite 假库的测试真钉住。行数上限 ``RECENT_JOB_SCAN``
远大于每日硬顶 120,数不到才是不可能的。

候选集与界面同源:直接复用单品播放总览的三表相交 + 收藏 ∪ 共享口径,保证
「报价里的视频」就是「操作员眼前表格里的视频」。
红线:纯 SELECT;SQL 全 ? 占位;禁 LIKE / 字面 %;聚合带 AS;domains 不 import api/workers。
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.content_metric_snapshots import _parse_timestamp
from app.domains.kol import sku_play_refresh_state as _state

# 本车道 source 标记:每日量闸与冷却只数自己的活,不吃别的入口的账。
SKU_PLAY_REFRESH_SOURCE = "my_kol_sku_play_refresh"

# 一次报价最多看多少行候选(与总览一页 800 行同量级,超出如实标 truncated)。
CANDIDATE_SCAN_LIMIT = 400

# 近期任务扫描行数:只用来数「最近 24 小时」与「冷却窗内」两件事。
# 每日硬顶 120 ≪ 600,扫不到的行必然更早,不会漏数。
RECENT_JOB_SCAN = 600

REFRESH_JOB_TYPE = _state.REFRESH_JOB_TYPE
ACTIVE_REFRESH_STATUSES = _state.ACTIVE_REFRESH_STATUSES

SKIP_BUCKETS = (
    "shared_readonly",
    "already_in_flight",
    "recently_measured",
    "daily_cap",
    "per_click_cap",
)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _env_int(name: str, default: int, *, hard_cap: int, floor: int = 1) -> int:
    """env 可调,但代码里有硬顶——env 配再大也封顶。"""

    raw = os.getenv(name, "")
    value = _int(raw) if str(raw).strip() else default
    return max(floor, min(hard_cap, value))


def per_click_cap() -> int:
    return _env_int("VKPI_SKU_PLAY_REFRESH_PER_CLICK", 12, hard_cap=30)


def daily_cap() -> int:
    return _env_int("VKPI_SKU_PLAY_REFRESH_DAILY_MAX", 40, hard_cap=120)


def cooldown_hours() -> int:
    return _env_int("VKPI_SKU_PLAY_REFRESH_COOLDOWN_HOURS", 6, hard_cap=168)


def _candidate_rows(
    conn: Any,
    *,
    staff_scope_id: int,
    sku_code: str,
    evidence_id: int = 0,
) -> list[dict[str, Any]]:
    """报价候选集 = 单品播放总览同源(links × tracking × evidence,收藏 ∪ 共享)。

    只读、无时间函数:这一句在 PG 与 sqlite 假库上是同一句。
    """

    sid = max(0, _int(staff_scope_id))
    single = max(0, _int(evidence_id))
    rows = conn.execute(
        """
        SELECT DISTINCT l.evidence_id AS evidence_id,
               e.kol_pool_id AS kol_pool_id,
               LOWER(COALESCE(e.platform, '')) AS platform,
               COALESCE(NULLIF(e.video_title, ''), e.title, '') AS video_title,
               COALESCE(NULLIF(kp.display_name, ''), kp.handle, '') AS kol_name
        FROM vkpi_kol_video_product_links l
        JOIN vkpi_kol_video_metric_tracking t ON t.evidence_id = l.evidence_id
        JOIN vkpi_kol_video_evidence e ON e.id = l.evidence_id
        LEFT JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
        WHERE e.is_active IS NOT FALSE
          AND l.product_sku = ?
          AND (? = 0 OR l.evidence_id = ?)
          AND (
            EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f
                    WHERE f.kol_pool_id = e.kol_pool_id AND (? = 0 OR f.staff_id = ?))
            OR EXISTS (SELECT 1 FROM vkpi_kol_pool_members sm
                       WHERE sm.kol_pool_id = e.kol_pool_id AND (? = 0 OR sm.staff_id = ?))
          )
        ORDER BY l.evidence_id
        LIMIT ?
        """,
        (
            _text(sku_code),
            single, single,
            sid, sid, sid, sid,
            CANDIDATE_SCAN_LIMIT + 1,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _in_flight_ids(conn: Any, evidence_ids: list[int]) -> set[int]:
    """已经在队列里 / 进行中的行:不会重复排,也不产生新花费。"""

    if not evidence_ids:
        return set()
    marks = ",".join("?" for _ in evidence_ids)
    statuses = sorted(ACTIVE_REFRESH_STATUSES)
    status_marks = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT DISTINCT CAST(payload ->> 'evidence_id' AS TEXT) AS evidence_id
        FROM apify_jobs
        WHERE job_type = ?
          AND status IN ({status_marks})
          AND CAST(payload ->> 'evidence_id' AS TEXT) IN ({marks})
        """,
        (
            REFRESH_JOB_TYPE,
            *statuses,
            *(str(value) for value in evidence_ids),
        ),
    ).fetchall()
    found = {_int(dict(row).get("evidence_id")) for row in rows}
    found.discard(0)
    return found


def _last_success_at(conn: Any, evidence_ids: list[int]) -> dict[int, datetime]:
    """每条视频最近一次**成功**实测时刻(失败快照不算实测,绝不当冷却依据)。"""

    if not evidence_ids:
        return {}
    marks = ",".join("?" for _ in evidence_ids)
    rows = conn.execute(
        f"""
        SELECT evidence_id AS evidence_id, MAX(fetched_at) AS last_success_at
        FROM vkpi_content_metric_snapshots
        WHERE evidence_id IN ({marks})
          AND status = 'success'
        GROUP BY evidence_id
        """,
        tuple(evidence_ids),
    ).fetchall()
    out: dict[int, datetime] = {}
    for row in rows:
        item = dict(row)
        moment = _parse_timestamp(item.get("last_success_at"))
        if moment is not None:
            out[_int(item.get("evidence_id"))] = moment
    return out


def _recent_window(conn: Any, hours: int, edge: datetime) -> tuple[str, tuple[Any, ...]]:
    """「最近 N 小时」的方言分支(与 my_kol_video_recovery 同款 sqlite 判据)。

    PG 走 ``make_interval``(索引/扫描都按时间收口);本地 sqlite 假库没有它,
    改用调用方算好的 ISO 边界文本比较——同一个 ``now`` 参数,测试才能真钉住。
    """

    if callable(getattr(conn, "executescript", None)):
        return "created_at >= ?", (edge.isoformat(timespec="seconds"),)
    return "created_at >= NOW() - make_interval(hours => ?)", (max(1, _int(hours)),)


def _recent_lane_jobs(conn: Any, *, hours: int, edge: datetime) -> list[dict[str, Any]]:
    """本车道最近 N 小时派出去的活(只取自己的 source;两个窗口在 Python 里再切)。"""

    clause, window_params = _recent_window(conn, hours, edge)
    rows = conn.execute(
        f"""
        SELECT CAST(payload ->> 'evidence_id' AS TEXT) AS evidence_id,
               created_at AS created_at
        FROM apify_jobs
        WHERE job_type = ?
          AND payload ->> 'source' = ?
          AND {clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        (REFRESH_JOB_TYPE, SKU_PLAY_REFRESH_SOURCE, *window_params, RECENT_JOB_SCAN),
    ).fetchall()
    return [dict(row) for row in rows]


def _lane_usage(
    conn: Any,
    *,
    now: datetime,
    cooldown: int,
) -> tuple[int, set[int]]:
    """(过去 24 小时本车道已花掉的取数次数, 冷却窗内本车道刚派过的视频)。"""

    cool_hours = max(1, _int(cooldown))
    scan_hours = max(24, cool_hours)
    day_edge = now - timedelta(hours=24)
    cool_edge = now - timedelta(hours=cool_hours)
    used = 0
    cooling: set[int] = set()
    for row in _recent_lane_jobs(conn, hours=scan_hours, edge=now - timedelta(hours=scan_hours)):
        moment = _parse_timestamp(row.get("created_at"))
        if moment is None:
            continue
        if moment >= day_edge:
            used += 1
        if moment >= cool_edge:
            evidence_id = _int(row.get("evidence_id"))
            if evidence_id > 0:
                cooling.add(evidence_id)
    return used, cooling


def _budget_headroom() -> dict[str, Any]:
    """本月额度只读投影。真闸永远在执行侧,这里只是让人先看一眼余量。"""

    try:
        from app.domains.costs import budget_readonly

        status = budget_readonly.get_budget_status_readonly("provider:apify")
    except Exception:  # noqa: BLE001 — 报价永远不能因为预算表读不到就整个失败
        return {"configured": False, "usage_ratio": None, "hard_stopped": False}
    if not status.get("configured"):
        return {"configured": False, "usage_ratio": None, "hard_stopped": False}
    ratio = status.get("usage_ratio")
    return {
        "configured": True,
        "usage_ratio": round(float(ratio), 4) if isinstance(ratio, (int, float)) else None,
        "hard_stopped": bool(status.get("hard_stopped")),
    }


def _item(row: dict[str, Any]) -> dict[str, Any]:
    evidence_id = _int(row.get("evidence_id"))
    return {
        "evidence_id": evidence_id,
        "kol_pool_id": _int(row.get("kol_pool_id")),
        "kol_name": _text(row.get("kol_name")),
        "platform": _text(row.get("platform")).lower(),
        "title": _text(row.get("video_title")),
    }


def _skip(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**_item(row), "reason": reason}


def plan_hash(sku_code: str, evidence_id: int, planned_ids: list[int], *, cooldown: int) -> str:
    """报价指纹:POST 回传后服务端重算比对,不一致就让操作员重看报价。

    只覆盖会改变「这次花多少」的输入:单品、单条限定、名单、冷却窗。
    """

    raw = "|".join(
        [
            "sku-play-refresh-v1",
            _text(sku_code),
            str(max(0, _int(evidence_id))),
            str(max(1, _int(cooldown))),
            ",".join(str(value) for value in sorted(planned_ids)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def plan_sku_play_refresh(
    conn: Any,
    *,
    staff: dict[str, Any] | None,
    staff_scope_id: int | None,
    sku_code: str,
    evidence_id: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """算出「这次要去平台取几次数」,一条也不入队、一次取数也不发生。"""

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    sid = max(0, _int(staff_scope_id))
    single = max(0, _int(evidence_id))
    cooldown = cooldown_hours()

    rows = _candidate_rows(
        conn, staff_scope_id=sid, sku_code=_text(sku_code), evidence_id=single
    )
    truncated = len(rows) > CANDIDATE_SCAN_LIMIT
    rows = rows[:CANDIDATE_SCAN_LIMIT]

    skipped: dict[str, list[dict[str, Any]]] = {key: [] for key in SKIP_BUCKETS}

    # ① 付费动作围栏:同事共享进来的红人只有可见性,写口永不放行。
    contexts = _state.writable_by_kol(
        conn, (row.get("kol_pool_id") for row in rows), staff=staff
    )
    writable: list[dict[str, Any]] = []
    for row in rows:
        context = contexts.get(_int(row.get("kol_pool_id"))) or {}
        if context.get("can_run_paid_actions"):
            writable.append(row)
        else:
            skipped["shared_readonly"].append(
                _skip(row, _text(context.get("reason")) or "my_kol_paid_action_write_forbidden")
            )

    ids = [_int(row.get("evidence_id")) for row in writable]

    # ② 已经在路上的:并入既有任务,不重复排、不重复花钱。
    in_flight = _in_flight_ids(conn, ids)
    pending = []
    for row in writable:
        if _int(row.get("evidence_id")) in in_flight:
            skipped["already_in_flight"].append(_skip(row, "already_in_flight"))
        else:
            pending.append(row)

    # ③ 冷却:刚成功实测过的 / 本车道刚派过的,这一轮不再花钱。
    used_today, lane_cooling = _lane_usage(conn, now=moment, cooldown=cooldown)
    successes = _last_success_at(conn, [_int(row.get("evidence_id")) for row in pending])
    cool_edge = moment - timedelta(hours=cooldown)
    fresh: list[dict[str, Any]] = []
    for row in pending:
        candidate_id = _int(row.get("evidence_id"))
        last_success = successes.get(candidate_id)
        if candidate_id in lane_cooling or (last_success is not None and last_success >= cool_edge):
            skipped["recently_measured"].append(_skip(row, "recently_measured"))
        else:
            fresh.append(row)

    # ④ 每日量闸 → ⑤ 每次量闸。先按日剩余切,再按单次上限切,两刀各自记账。
    day_cap = daily_cap()
    day_left = max(0, day_cap - used_today)
    skipped["daily_cap"] = [_skip(row, "daily_cap") for row in fresh[day_left:]]
    fresh = fresh[:day_left]

    click_cap = per_click_cap()
    skipped["per_click_cap"] = [_skip(row, "per_click_cap") for row in fresh[click_cap:]]
    planned_rows = fresh[:click_cap]

    planned = [_item(row) for row in planned_rows]
    planned_ids = [item["evidence_id"] for item in planned]

    return {
        "status": "ok",
        "sku_code": _text(sku_code),
        "evidence_id": single or None,
        "scope": "single" if single else "group",
        # 报价本体:这次去平台取几次数。一条视频 = 一次取数(worker 里只发一次)。
        "planned_count": len(planned),
        "planned": planned,
        "fetch_per_video": 1,
        "fetch_calls_total": len(planned),
        # 单条路径不弹确认框(与内容墙单账号路径同口径);批量恒需确认。
        "requires_confirmation": not single,
        "skipped": skipped,
        "skipped_counts": {key: len(value) for key, value in skipped.items()},
        "candidates_total": len(rows),
        "candidates_truncated": truncated,
        "limits": {
            "per_click": click_cap,
            "daily": day_cap,
            "daily_used": used_today,
            "daily_left": day_left,
            "cooldown_hours": cooldown,
        },
        "budget": _budget_headroom(),
        "plan_hash": plan_hash(sku_code, single, planned_ids, cooldown=cooldown),
    }


__all__ = [
    "CANDIDATE_SCAN_LIMIT",
    "RECENT_JOB_SCAN",
    "SKIP_BUCKETS",
    "SKU_PLAY_REFRESH_SOURCE",
    "cooldown_hours",
    "daily_cap",
    "per_click_cap",
    "plan_hash",
    "plan_sku_play_refresh",
]
