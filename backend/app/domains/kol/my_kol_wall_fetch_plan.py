"""内容墙「去查最新内容」的报价器(纯读:零入队、零 provider、零写库)。

一句话:**点下去之前,先把「这次要去几个账号取内容」算清楚摆出来。**

设计取舍(2026-08-25,内容墙抓取车道):

1. **报次数,不报金额。** 金额要按平台映射到具体抓取器再乘输入量,中间每一步都会漂;
   报一个偏低的美元数比不报更危险。门面报「几个账号 + 合计几次取数」+ 本月额度已用几成
   (只读投影),精确账留给运维卡。
   **计量单位 = 一次真实的平台取数,不是「一个账号算一次」**:一个 YouTube 账号要取
   两次(先账号资料、再内容列表),TikTok / Instagram 各一次,Instagram 的账号资料
   空返回时还会兜底再取一次——所以报价同时给「至少」与「最多」。笼统写「各取一次」
   在全 YouTube 的名单上就是少报一半(2026-08-25 复核坐实)。
2. **窗口不放宽任何既有闸。** 每个账号每次仍然只取最近 ``WINDOW_POSTS`` 条(=既有
   enqueue 的 12 条上限),所选时间范围**不会**让我们去平台翻更多历史。因此
   「全部时间」的真实含义是「最近 12 条」,必须原样说给用户听,不许叫「全量」。
3. **时间窗的精确度按平台分档,如实标注。** YouTube / TikTok 的抓取器都认发布时间下限
   (``publishedAfter`` / ``oldestPostDate``),窗口由平台侧截取,是真窗口;Instagram
   的账号资料抓取器**没有日期字段**,只能取最近这一批,取回的内容可能落在所选范围之外
   ——这一档必须单独说清楚,不许为了文案整齐说成「精确过滤」(2026-08-25 复核坐实)。
4. **冷却用真源。** 刻意**不用** ``vkpi_kol_pool.last_scrape_at``(prod 实测全 NULL,
   当闸=没有闸),改查 ``vkpi_kol_url_deep_crawl_runs`` 的 ready 记录(任何入口取成功
   都算)+ 本车道自己 source 的近期入队(挡住结果还没回来就连点)。
5. **可写子集才进批量。** 内容墙看得见的是「收藏 ∪ 共享」,而付费动作围栏只认
   「本人收藏 / 管理层」。共享进来的账号在墙上照常显示,但报价里如实归入
   「同事分享给你的,不能从这里取」,绝不静默 403。

口径与内容墙完全同源:候选集直接复用 ``my_kol_board_ext_sql._COLLECTION_COND``,
保证「报价里的账号」就是「操作员眼前墙上的账号」。
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.access import scope as access_scope
from app.domains.kol import my_kol_paid_action_access
from app.domains.kol.my_kol_board_ext_sql import _COLLECTION_COND

# 本车道的 source 标记:冷却与每日量闸只数自己的活,不吃别的入口的账。
WALL_FETCH_SOURCE = "my_kol_content_wall_fetch"

# 每个账号每次取多少条内容。12 = 既有 enqueue_profile_deep_crawl_job 的硬上限,
# 也是内容监控(content_monitoring)的既定档位。**本车道不抬这个上限。**
WINDOW_POSTS = 12

# 候选集读取上限:防一次把全团队几千行拉进内存;超出即如实标 truncated。
CANDIDATE_SCAN_LIMIT = 400

_SUPPORTED_PLATFORMS = ("youtube", "instagram", "tiktok")

# 时间窗在平台侧的精确度。真实差异来自抓取器能力,不是我们的选择:
#   tiktok    → oldestPostDate,平台侧按发布时间截取(真窗口)
#   youtube   → publishedAfter,平台侧按发布时间截取(真窗口)
#   instagram → 账号资料抓取器没有日期字段,只能取最近这一批(可能超出所选范围)
_PLATFORM_WINDOW_EXACTNESS = {
    "tiktok": "date_pushdown",
    "youtube": "date_pushdown",
    "instagram": "recent_only",
}

_EXACTNESS_LABEL = {
    "date_pushdown": "按发布时间在平台侧截取",
    "recent_only": "只能取最近内容,平台不认发布时间",
}

# 一个账号取一次内容,在平台侧到底是几次真实取数。这是报价的**计量单位**:
#   youtube   → 2 次(先取账号资料拿到频道号,再取内容列表)
#   tiktok    → 1 次(账号资料与内容同一次带回)
#   instagram → 1 次;账号资料空返回时兜底再取一次 → 最多 2 次
_PLATFORM_FETCH_CALLS = {"youtube": 2, "tiktok": 1, "instagram": 1}
_PLATFORM_FETCH_CALLS_MAX = {"youtube": 2, "tiktok": 1, "instagram": 2}
# 未知平台不进候选集(_SUPPORTED_PLATFORMS 已挡);真到了这里按最保守的算,不少报。
_FETCH_CALLS_FALLBACK = 2


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _env_int(name: str, default: int, *, hard_cap: int, floor: int = 1) -> int:
    """env 可调,但代码里有硬顶——env 配再大也封顶。"""

    raw = os.getenv(name, "")
    value = _int(raw) if str(raw).strip() else default
    return max(floor, min(hard_cap, value))


def per_click_cap() -> int:
    return _env_int("VKPI_WALL_FETCH_PER_CLICK", 12, hard_cap=30)


def daily_cap() -> int:
    return _env_int("VKPI_WALL_FETCH_DAILY_MAX", 40, hard_cap=120)


def cooldown_hours() -> int:
    return _env_int("VKPI_WALL_FETCH_COOLDOWN_HOURS", 6, hard_cap=168)


def window_spec(days: int, *, now: datetime | None = None) -> dict[str, Any]:
    """把界面上的时间钮翻译成抓取参数。

    ``since`` 只在认发布时间下限的平台(YouTube / TikTok)真正生效;Instagram 的账号
    资料抓取器不认日期,取回的内容可能超出所选范围——报价按 ``recent_only`` 如实标注。
    ``max_posts`` 恒等于 WINDOW_POSTS——时间范围不会让我们去取更多条。
    """

    safe_days = max(0, min(_int(days), 365))
    moment = now or datetime.now(timezone.utc)
    since_iso = "" if not safe_days else (moment - timedelta(days=safe_days)).date().isoformat()
    return {
        "days": safe_days,
        "since": since_iso,
        "max_posts": WINDOW_POSTS,
    }


def window_label(days: int) -> str:
    safe_days = max(0, min(_int(days), 365))
    return "全部时间" if not safe_days else f"最近 {safe_days} 天"


def _candidate_rows(conn: Any, *, staff_scope_id: int, kol_pool_id: int) -> list[dict[str, Any]]:
    """内容墙同源候选集(收藏 ∪ 共享),已按平台与可抓地址过滤。

    ``duplicate_of_id IS NULL`` 与内容墙每一条 SQL 同口径:去重合并掉的账号操作员在墙上
    根本看不见,报价里冒出来只会让人对不上数,还会替一个不显示的账号花钱。

    禁 LIKE / 禁字面 percent:地址是否可用一律用 strpos 判断。
    """

    sid = max(0, _int(staff_scope_id))
    marks = ",".join("?" for _ in _SUPPORTED_PLATFORMS)
    single = max(0, _int(kol_pool_id))
    rows = conn.execute(
        f"""
        SELECT kp.id AS kol_pool_id,
               COALESCE(NULLIF(kp.display_name, ''), kp.handle, '') AS display_name,
               COALESCE(kp.platform, '') AS platform,
               COALESCE(kp.profile_url, '') AS profile_url
        FROM vkpi_kol_pool kp
        WHERE kp.duplicate_of_id IS NULL
          AND {_COLLECTION_COND}
          AND (? = 0 OR kp.id = ?)
          AND LOWER(COALESCE(kp.platform, '')) IN ({marks})
          AND strpos(LOWER(COALESCE(kp.profile_url, '')), ?) = 1
        ORDER BY kp.id
        LIMIT ?
        """,
        (
            sid, sid, sid, sid,
            single, single,
            *_SUPPORTED_PLATFORMS,
            "http",
            CANDIDATE_SCAN_LIMIT + 1,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _cooling_ids(conn: Any, pool_ids: list[int], hours: int) -> set[int]:
    """冷却期内刚取过 / 刚派过的账号。两个真源都查,任一命中即跳过。

    刻意**不用** ``vkpi_kol_pool.last_scrape_at``:prod 实测该列全 NULL,当闸=没有闸。
    """

    if not pool_ids:
        return set()
    marks = ",".join("?" for _ in pool_ids)
    window = max(1, _int(hours))
    cooling: set[int] = set()
    # 真源一:任何入口最近取成功过(ready)——别人刚取完,我们就没必要再花一次。
    fetched = conn.execute(
        f"""
        SELECT DISTINCT kol_pool_id AS kol_pool_id
        FROM vkpi_kol_url_deep_crawl_runs
        WHERE kol_pool_id IN ({marks})
          AND status = 'ready'
          AND created_at >= NOW() - make_interval(hours => ?)
        """,
        (*pool_ids, window),
    ).fetchall()
    cooling.update(_int(dict(row).get("kol_pool_id")) for row in fetched)
    # 真源二:本车道自己刚派过(结果还没回来时的连点保护)。
    # 刻意按**文本**比对 payload 里的 id,不做 CAST(... AS BIGINT):jsonb 里混进一条
    # 非数字 id 就会让整条查询运行期炸掉,那等于让冷却闸随机失灵。
    queued = conn.execute(
        f"""
        SELECT DISTINCT payload ->> 'kol_pool_id' AS kol_pool_id
        FROM apify_jobs
        WHERE job_type = 'kol_profile_deep_crawl'
          AND payload ->> 'source' = ?
          AND payload ->> 'kol_pool_id' IN ({marks})
          AND created_at >= NOW() - make_interval(hours => ?)
        """,
        (WALL_FETCH_SOURCE, *(str(pool_id) for pool_id in pool_ids), window),
    ).fetchall()
    cooling.update(_int(dict(row).get("kol_pool_id")) for row in queued)
    cooling.discard(0)
    return cooling


def _daily_used(conn: Any) -> int:
    """本车道过去 24 小时已经花掉的取数次数(只数自己的 source)。"""

    row = conn.execute(
        """
        SELECT COUNT(*) AS used
        FROM apify_jobs
        WHERE job_type = 'kol_profile_deep_crawl'
          AND payload ->> 'source' = ?
          AND created_at >= NOW() - make_interval(hours => 24)
        """,
        (WALL_FETCH_SOURCE,),
    ).fetchone()
    return _int(dict(row).get("used")) if row else 0


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


def _fetch_call_counts(planned_items: list[dict[str, Any]]) -> dict[str, Any]:
    """报价的计量单位:真实的平台取数次数(按平台分别计,不是「一个账号一次」)。

    ``total`` = 一定会发生的次数;``max_total`` = 含 Instagram 空返回兜底的上限。
    两个都报:少报会让人以为额度够,只报上限又会天天吓人。
    """

    by_platform: dict[str, dict[str, int]] = {}
    total = 0
    max_total = 0
    for item in planned_items:
        platform = str(item.get("platform") or "").lower()
        per = _PLATFORM_FETCH_CALLS.get(platform, _FETCH_CALLS_FALLBACK)
        per_max = _PLATFORM_FETCH_CALLS_MAX.get(platform, _FETCH_CALLS_FALLBACK)
        bucket = by_platform.setdefault(platform, {"accounts": 0, "per_account": per, "per_account_max": per_max, "calls": 0})
        bucket["accounts"] += 1
        bucket["calls"] += per
        total += per
        max_total += per_max
    return {"total": total, "max_total": max_total, "by_platform": by_platform}


def _skip(pool: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "kol_pool_id": _int(pool.get("kol_pool_id")),
        "name": str(pool.get("display_name") or "") or f"#{_int(pool.get('kol_pool_id'))}",
        "platform": str(pool.get("platform") or "").lower(),
        "reason": reason,
    }


def plan_hash(days: int, planned_ids: list[int], *, cooldown: int) -> str:
    """报价指纹:POST 回传后服务端重算比对,不一致就让操作员重看报价。

    只覆盖会改变「这次花多少」的输入:窗口、名单、冷却窗。
    """

    raw = "|".join(
        [
            "wall-fetch-v1",
            str(max(0, _int(days))),
            str(max(1, _int(cooldown))),
            ",".join(str(pool_id) for pool_id in sorted(planned_ids)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def plan_wall_fetch(
    conn: Any,
    *,
    staff: dict[str, Any] | None,
    staff_scope_id: int | None,
    kol_pool_id: int = 0,
    days: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """算出「这次要去几个账号取内容」,一条也不入队、一次 provider 也不调。"""

    sid = max(0, _int(staff_scope_id))
    single = max(0, _int(kol_pool_id))
    spec = window_spec(days, now=now)
    cooldown = cooldown_hours()
    rows = _candidate_rows(conn, staff_scope_id=sid, kol_pool_id=single)
    truncated = len(rows) > CANDIDATE_SCAN_LIMIT
    rows = rows[:CANDIDATE_SCAN_LIMIT]

    skipped: dict[str, list[dict[str, Any]]] = {
        "shared_readonly": [],
        "recently_fetched": [],
        "per_click_cap": [],
        "daily_cap": [],
    }

    # ① 可写子集:共享进来的账号只有可见性,付费动作围栏永不放行。
    writable: list[dict[str, Any]] = []
    for pool in rows:
        context = my_kol_paid_action_access.target_write_context(
            conn, kol_pool_id=_int(pool.get("kol_pool_id")), staff=staff
        )
        if context.get("can_run_paid_actions"):
            writable.append(pool)
        else:
            skipped["shared_readonly"].append(_skip(pool, str(context.get("reason") or "")))

    # ② 冷却:刚取过的不再花钱。
    cooling = _cooling_ids(conn, [_int(pool.get("kol_pool_id")) for pool in writable], cooldown)
    fresh = []
    for pool in writable:
        if _int(pool.get("kol_pool_id")) in cooling:
            skipped["recently_fetched"].append(_skip(pool, "recently_fetched"))
        else:
            fresh.append(pool)

    # ③ 每日量闸 → ④ 每次量闸。先按日剩余切,再按单次上限切,两刀各自记账。
    used_today = _daily_used(conn)
    day_cap = daily_cap()
    day_left = max(0, day_cap - used_today)
    over_daily = fresh[day_left:]
    fresh = fresh[:day_left]
    skipped["daily_cap"] = [_skip(pool, "daily_cap") for pool in over_daily]

    click_cap = per_click_cap()
    over_click = fresh[click_cap:]
    planned = fresh[:click_cap]
    skipped["per_click_cap"] = [_skip(pool, "per_click_cap") for pool in over_click]

    planned_items = [
        {
            "kol_pool_id": _int(pool.get("kol_pool_id")),
            "name": str(pool.get("display_name") or "") or f"#{_int(pool.get('kol_pool_id'))}",
            "platform": str(pool.get("platform") or "").lower(),
            # 未知平台按最保守的一档算:没证据说明它认发布时间。
            "window_exactness": _PLATFORM_WINDOW_EXACTNESS.get(
                str(pool.get("platform") or "").lower(), "recent_only"
            ),
        }
        for pool in planned
    ]
    planned_ids = [item["kol_pool_id"] for item in planned_items]

    # 精确度分档:只统计本次真的要去的账号,别拿没派的账号吓唬人。
    exactness_counts: dict[str, int] = {}
    for item in planned_items:
        key = str(item["window_exactness"])
        exactness_counts[key] = exactness_counts.get(key, 0) + 1

    fetch_calls = _fetch_call_counts(planned_items)

    return {
        "status": "ok",
        "days": spec["days"],
        "window_label": window_label(days),
        "kol_pool_id": single or None,
        "scope": "single" if single else ("team" if not sid else "own"),
        "scope_label": _scope_label(staff, sid, single),
        # 报价本体:这次要去几个账号,合计几次真实取数(按平台分别计数,不是各算一次)。
        "planned_count": len(planned_items),
        "planned": planned_items,
        "fetch_calls": fetch_calls,
        "posts_per_account": WINDOW_POSTS,
        "followups_suppressed": True,
        "requires_confirmation": not single,
        "skipped": skipped,
        "skipped_counts": {key: len(value) for key, value in skipped.items()},
        "candidates_total": len(rows),
        "candidates_truncated": truncated,
        "window": {
            "since": spec["since"],
            "max_posts": WINDOW_POSTS,
            "exactness_counts": exactness_counts,
            "exactness_labels": dict(_EXACTNESS_LABEL),
        },
        "limits": {
            "per_click": click_cap,
            "daily": day_cap,
            "daily_used": used_today,
            "daily_left": day_left,
            "cooldown_hours": cooldown,
        },
        "budget": _budget_headroom(),
        "plan_hash": plan_hash(spec["days"], planned_ids, cooldown=cooldown),
    }


def _scope_label(staff: dict[str, Any] | None, staff_scope_id: int, kol_pool_id: int) -> str:
    if kol_pool_id:
        return "所选账号"
    if not staff_scope_id and access_scope.can_view_all(staff):
        return "全团队收藏的账号"
    return "你收藏的账号"
