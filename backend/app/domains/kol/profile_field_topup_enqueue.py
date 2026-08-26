"""车道 1 —— **定点按需抓取**:把硬筛已经标好的「只差 country/language 未知」候选,
在搜索之后**按需**补齐一次轻量档案(拿国家 / 语言)。

用户策略(原话):「apify 每次搜索的时候定点抓取就行不用立马抓取」——所以这里**没有**
批量回填,只有「搜什么才补什么」。判定层
(``profile_recall_filter_modes.unknown_field_candidates``)负责标记,账本
(``profile_recall_funnel.RecallStageLedger.note_topup_candidates``)负责登记,本模块
负责**消费**——而且必须先过五道闸才允许花一分钱。

五道成本闸(全部默认保守,失败方向 = 不花钱):

1. **总开关默认 OFF**(``VKPI_FIELD_TOPUP_ENABLED``,缺省 ``0``)。没显式开,本模块
   只出计划、零入队。任何花钱动作都不自动武装。
2. **每次搜索上限**(``VKPI_FIELD_TOPUP_PER_SEARCH``,缺省 10,硬顶 25)。
3. **每日总量上限**(``VKPI_FIELD_TOPUP_DAILY_MAX``,缺省 50,硬顶 200)。超了当天不再补,
   并把 ``skipped.daily_budget`` 如实记账,不静默丢。
4. **冷却期**(``VKPI_FIELD_TOPUP_COOLDOWN_HOURS``,缺省 168 小时 = 7 天):同一个人在
   窗口内抓过(``vkpi_kol_url_deep_crawl_runs`` ready)或已被本车道入过队
   (``apify_jobs``)一律跳过。注意**不能**用 ``vkpi_kol_pool.last_scrape_at`` 做冷却——
   prod 2026-08-25 实测该列 2034 行**全为 NULL**,拿它当闸等于没有闸。
5. **平台白名单**(``VKPI_FIELD_TOPUP_PLATFORMS``,缺省三大平台):只有能拿到公开档案的
   平台才值得花钱。

入队前必须能报出「这一次要花多少次抓取」:``planned_fetch_count`` 就是这个数,
``plan_field_topup`` 在**零写库**的前提下算出来,总开关关着也照样算——让人在武装之前
先看见账单。``expected_field_fill`` 进一步按平台实测产出率给出「这些抓取预计能补上几个
字段」,原始实测见 ``MEASURED_FILL_RATE``。

**异步、绝不阻塞首屏**:本模块唯一的写动作是 ``INSERT apify_jobs``(batch 泳道)。
provider 调用发生在 worker 侧、走既有预算闸。被补的人**不会**出现在本次搜索结果里——
``applies_to_this_search`` 恒为 ``False``,诊断文案也照直说。

**复用既有围栏,不另起一套抓取**:入队走
``url_deep_crawl.enqueue_profile_deep_crawl_job``(与车道 D 懒回填、内容监控同一个入队器)。
``enforce_target_write`` 保持 ``False`` —— 那道围栏是 My-KOL 付费动作专用的行级归属闸
(``video_tracking._assert_target_writable`` → 要求候选已在操作员的 My-KOL 里),而库内召回
候选按定义**不在** My-KOL,开了它会对几乎所有候选 fail-closed。这里沿用与车道 D 相同的
后台授权路径:staff 身份只作归属记账(``triggered_by_user_id`` / ``staff_id``),
``source`` 独立成 ``kol_field_topup_on_search`` 以便把本车道的花费单独算账。
成本压到最低:``max_posts=1`` + 三个 suppress 开关(与内容监控同款最小档),
只抓档案,不跑代表视频 / final_v1 / 联系方式 / 账号档案抽取。

red line:零写 ``viltrox_fit_score``,零改 rule_v0,不放宽任何质量口径——本模块只决定
「去不去补数据」,完全不碰「什么算合格」。
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

FIELD_TOPUP_SCHEMA = "field_topup_v1"

#: 本车道入队任务的 ``payload.source``。独立命名 = 花费能单独算账、冷却能只看自己。
TOPUP_SOURCE = "kol_field_topup_on_search"

#: 硬顶。env 配再大也封顶,防「改个环境变量就无上限烧钱」。
HARD_CAP_PER_SEARCH = 25
HARD_CAP_DAILY = 200

DEFAULT_PER_SEARCH = 10
DEFAULT_DAILY_MAX = 50
DEFAULT_COOLDOWN_HOURS = 168
DEFAULT_PLATFORMS = ("youtube", "tiktok", "instagram")

#: 本车道只补这两个字段;与 ``TRI_STATE_FILTER_FIELDS`` 同源。
TOPUP_FIELDS = ("country", "language")

#: prod 2026-08-25 实测:一次定点抓取把字段补上的概率。样本取 ``source_type='manual'``
#: 的行——这批人入池时 country/language 皆空(未抓组 121 行里只有 1 行有 country),
#: 所以「已抓组的填充率」≈ 一次抓取的真实产出率,而不是导入数据的残留。
#: YouTube n=373(已抓)/33(未抓)、TikTok n=371/41、Instagram n=145/47。
#: 这是**观测值不是承诺**,只用于入队前把预期收益一并报出来。
MEASURED_FILL_RATE: dict[str, dict[str, float]] = {
    "youtube": {"country": 0.807, "language": 0.091},
    "tiktok": {"country": 0.143, "language": 0.288},
    "instagram": {"country": 0.0, "language": 0.0},
}
MEASURED_AT = "2026-08-25"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    """env 开关的唯一判读口径。缺省 / 空 / 0 / false / no / off 一律 = 关。"""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def topup_settings() -> dict[str, Any]:
    """每次调用现读 env(改值不改代码、不必重启),并夹进硬顶内。"""

    platforms = [
        item.strip().lower()
        for item in str(os.environ.get("VKPI_FIELD_TOPUP_PLATFORMS") or "").split(",")
        if item.strip()
    ] or list(DEFAULT_PLATFORMS)
    return {
        "enabled": _truthy(os.environ.get("VKPI_FIELD_TOPUP_ENABLED")),
        "per_search_max": max(
            1,
            min(
                _int(os.environ.get("VKPI_FIELD_TOPUP_PER_SEARCH"), DEFAULT_PER_SEARCH),
                HARD_CAP_PER_SEARCH,
            ),
        ),
        "daily_max": max(
            0,
            min(
                _int(os.environ.get("VKPI_FIELD_TOPUP_DAILY_MAX"), DEFAULT_DAILY_MAX),
                HARD_CAP_DAILY,
            ),
        ),
        "cooldown_hours": max(
            1,
            _int(os.environ.get("VKPI_FIELD_TOPUP_COOLDOWN_HOURS"), DEFAULT_COOLDOWN_HOURS),
        ),
        "platforms": sorted(set(platforms)),
        "source": TOPUP_SOURCE,
    }


def _candidate_ids(candidates: Any) -> tuple[list[int], dict[int, list[str]]]:
    """把账本里的标记清单收敛成 ``(去重后的 id 列表, id -> missing_fields)``。

    字段契约见 ``profile_recall_filter_modes.unknown_field_candidates``:
    ``kol_pool_id`` / ``handle`` / ``platform`` / ``missing_fields``。这里只认
    ``kol_pool_id`` 与 ``missing_fields``,其余一律回库现取——标记是线索,不是真相。
    """

    ordered: list[int] = []
    missing_by_id: dict[int, list[str]] = {}
    for candidate in candidates or ():
        if not isinstance(candidate, dict):
            continue
        pool_id = _int(candidate.get("kol_pool_id"))
        if pool_id <= 0:
            continue
        fields = sorted(
            {
                str(field).strip().lower()
                for field in candidate.get("missing_fields") or ()
                if str(field).strip().lower() in TOPUP_FIELDS
            }
        )
        if pool_id in missing_by_id:
            missing_by_id[pool_id] = sorted(set(missing_by_id[pool_id]) | set(fields))
            continue
        ordered.append(pool_id)
        missing_by_id[pool_id] = fields
    return ordered, missing_by_id


def _crawlable_rows(conn: Any, pool_ids: list[int], platforms: list[str]) -> list[dict[str, Any]]:
    """可抓性 + 平台白名单 + 粉丝倒序。纯 SELECT,零触 fit。"""

    if not pool_ids or not platforms:
        return []
    id_marks = ",".join("?" for _ in pool_ids)
    platform_marks = ",".join("?" for _ in platforms)
    rows = conn.execute(
        f"""
        SELECT id, platform, handle, profile_url, followers, country, language
        FROM vkpi_kol_pool
        WHERE id IN ({id_marks})
          AND duplicate_of_id IS NULL
          AND profile_url IS NOT NULL
          AND strpos(lower(profile_url), ?) = 1
          AND lower(COALESCE(platform, '')) IN ({platform_marks})
        ORDER BY followers DESC NULLS LAST, id ASC
        """,
        (*pool_ids, "http", *platforms),
    ).fetchall()
    return [dict(row) for row in rows]


def _cooling_ids(conn: Any, pool_ids: list[int], cooldown_hours: int) -> set[int]:
    """冷却期内已抓过 / 已入过队的人。两个真源都查,任一命中即跳过。

    刻意**不用** ``vkpi_kol_pool.last_scrape_at``:prod 实测该列全 NULL,当闸=没有闸。
    """

    if not pool_ids:
        return set()
    marks = ",".join("?" for _ in pool_ids)
    hours = max(1, int(cooldown_hours))
    cooling: set[int] = set()
    crawled = conn.execute(
        f"""
        SELECT DISTINCT kol_pool_id AS kol_pool_id
        FROM vkpi_kol_url_deep_crawl_runs
        WHERE kol_pool_id IN ({marks})
          AND status = 'ready'
          AND created_at >= NOW() - make_interval(hours => ?)
        """,
        (*pool_ids, hours),
    ).fetchall()
    cooling.update(_int(dict(row).get("kol_pool_id")) for row in crawled)
    # 刻意按**文本**比对 payload 里的 id,不做 CAST(... AS BIGINT):jsonb 里混进一条非数字
    # 的 kol_pool_id 就会让整条查询在运行期炸掉(且规划器不保证先过 source 过滤),
    # 那等于让冷却闸随机失灵。文本比对无此风险,Python 侧再转回 int。
    queued = conn.execute(
        f"""
        SELECT DISTINCT payload ->> 'kol_pool_id' AS kol_pool_id
        FROM apify_jobs
        WHERE job_type = 'kol_profile_deep_crawl'
          AND payload ->> 'source' = ?
          AND payload ->> 'kol_pool_id' IN ({marks})
          AND created_at >= NOW() - make_interval(hours => ?)
        """,
        (TOPUP_SOURCE, *(str(pool_id) for pool_id in pool_ids), hours),
    ).fetchall()
    cooling.update(_int(dict(row).get("kol_pool_id")) for row in queued)
    cooling.discard(0)
    return cooling


def _daily_used(conn: Any) -> int:
    """本车道过去 24 小时已经花掉的抓取次数(只数自己的 source)。"""

    row = conn.execute(
        """
        SELECT COUNT(*) AS used
        FROM apify_jobs
        WHERE job_type = 'kol_profile_deep_crawl'
          AND payload ->> 'source' = ?
          AND created_at >= NOW() - make_interval(hours => 24)
        """,
        (TOPUP_SOURCE,),
    ).fetchone()
    return _int(dict(row).get("used")) if row else 0


def _expected_fill(planned: list[dict[str, Any]]) -> dict[str, Any]:
    """按平台实测产出率,估这批抓取预计能补上几个字段。观测值,不是承诺。"""

    per_field = {field: 0.0 for field in TOPUP_FIELDS}
    unmeasured = 0
    for item in planned:
        rates = MEASURED_FILL_RATE.get(str(item.get("platform") or "").lower())
        if rates is None:
            unmeasured += 1
            continue
        for field in item.get("missing_fields") or ():
            if field in per_field:
                per_field[field] += float(rates.get(field, 0.0))
    return {
        "measured_at": MEASURED_AT,
        "by_field": {field: round(value, 2) for field, value in per_field.items()},
        "fields_total": round(sum(per_field.values()), 2),
        "unmeasured_platform_rows": unmeasured,
        "note": "按 prod 实测的单次抓取字段填充率估算;是观测值不是承诺。",
    }


def plan_field_topup(
    candidates: Any,
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """算出「这一次要花多少次抓取」。**零写库**,总开关关着也照算。

    顺序即优先级:粉丝倒序(有限预算先救大号),再切每次搜索上限,再切当天剩余预算。
    每一刀砍掉多少人都单独记账,没有静默丢弃。
    """

    config = dict(settings or topup_settings())
    marked_ids, missing_by_id = _candidate_ids(candidates)
    skipped = {
        "not_crawlable": 0,
        "already_filled": 0,
        "cooldown": 0,
        "per_search_cap": 0,
        "daily_budget": 0,
    }
    base = {
        "schema": FIELD_TOPUP_SCHEMA,
        "marked": len(marked_ids),
        "eligible": 0,
        "planned_fetch_count": 0,
        "planned": [],
        "skipped": dict(skipped),
        "skipped_total": 0,
        "daily": {"max": config["daily_max"], "used": 0, "remaining": config["daily_max"]},
        "settings": config,
    }
    if not marked_ids:
        return {**base, "status": "no_candidates"}

    conn = get_conn()
    try:
        rows = _crawlable_rows(conn, marked_ids, list(config["platforms"]))
        cooling = _cooling_ids(conn, [_int(row.get("id")) for row in rows], config["cooldown_hours"])
        used = _daily_used(conn)
    except Exception as exc:
        # 失败方向安全:探测不出来就当没有预算,零入队,但留声不静默。
        logger.warning(
            "field_topup_probe_failed marked=%s reason=%s",
            len(marked_ids), str(exc)[:200], exc_info=True,
        )
        return {**base, "status": "probe_failed", "reason": "topup_probe_failed"}

    skipped["not_crawlable"] = len(marked_ids) - len(rows)
    eligible: list[dict[str, Any]] = []
    for row in rows:
        pool_id = _int(row.get("id"))
        if pool_id in cooling:
            skipped["cooldown"] += 1
            continue
        # 标记是搜索那一刻拍的照;真相以库为准。字段在这中间已经被别的管线补上了,
        # 就别再花这笔钱。
        # 标记没写清缺哪个(理论上不会:钩子必给至少一个字段)就以库为准两个都看,
        # 而不是当成「已补齐」悄悄跳过。
        declared = missing_by_id.get(pool_id) or list(TOPUP_FIELDS)
        still_missing = [field for field in declared if not str(row.get(field) or "").strip()]
        if not still_missing:
            skipped["already_filled"] += 1
            continue
        eligible.append(
            {
                "kol_pool_id": pool_id,
                "platform": str(row.get("platform") or "").strip().lower(),
                "handle": str(row.get("handle") or ""),
                "profile_url": str(row.get("profile_url") or "").strip(),
                "followers": _int(row.get("followers")) if row.get("followers") is not None else None,
                "missing_fields": still_missing,
            }
        )

    remaining = max(0, _int(config["daily_max"]) - used)
    after_per_search = eligible[: config["per_search_max"]]
    skipped["per_search_cap"] = len(eligible) - len(after_per_search)
    planned = after_per_search[:remaining]
    skipped["daily_budget"] = len(after_per_search) - len(planned)

    return {
        **base,
        "status": "planned",
        "eligible": len(eligible),
        "planned_fetch_count": len(planned),
        "planned": planned,
        "skipped": dict(skipped),
        "skipped_total": sum(skipped.values()),
        "daily": {"max": config["daily_max"], "used": used, "remaining": remaining},
        "expected_field_fill": _expected_fill(planned),
    }


def _summary_line(result: dict[str, Any]) -> str:
    """给操作员看的一句话。大白话,不出现内部词。"""

    skipped = result.get("skipped") or {}
    blocked = (
        _int(skipped.get("cooldown"))
        + _int(skipped.get("daily_budget"))
        + _int(skipped.get("per_search_cap"))
    )
    if not _int(result.get("marked")):
        return "本次没有「只差国家/语言」的人选,无需补数据。"
    if result.get("status") == "disabled":
        return (
            f"本次标记 {_int(result.get('marked'))} 人待补,按当前设置需要 "
            f"{_int(result.get('planned_fetch_count'))} 次抓取;补数据开关未开启,未抓取。"
        )
    if result.get("status") == "dry_run":
        return (
            f"试算:本次标记 {_int(result.get('marked'))} 人待补,需要 "
            f"{_int(result.get('planned_fetch_count'))} 次抓取;本次只试算,未抓取。"
        )
    return (
        f"本次标记 {_int(result.get('marked'))} 人待补,已排队 {_int(result.get('enqueued'))} 人,"
        f"因额度或近期已抓跳过 {blocked} 人;补数据在后台进行,本次结果不含这批人。"
    )


def enqueue_field_topup_for_candidates(
    *,
    candidates: Any,
    session_id: int | None = None,
    staff: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """消费「只差 country/language」标记,按需入队一次轻量档案补齐。

    总开关默认 OFF —— 关着时只回计划(账单可见)、零入队。``dry_run=True`` 即使开着闸
    也只算不花。返回的诊断字段契约稳定,见模块 docstring。
    """

    plan = plan_field_topup(candidates)
    config = plan["settings"]
    honest = {
        **plan,
        "enabled": bool(config["enabled"]),
        "dry_run": bool(dry_run),
        "enqueued": 0,
        "already_queued": 0,
        "errors": 0,
        "items": [],
        # 诚实:补齐是后台的,补完的人最快也要下一次搜索才可能出现。
        "applies_to_this_search": False,
        "note": (
            "定点按需补齐:只对「其他维度都合格、只差 country/language」的候选补一次轻量档案。"
            "后台异步执行,本次搜索结果不含这批人。"
        ),
    }
    def _finish(patch: dict[str, Any]) -> dict[str, Any]:
        merged = {**honest, **patch}
        return {**merged, "summary_line": _summary_line(merged)}

    if plan["status"] in {"no_candidates", "probe_failed"}:
        return _finish({})
    if not config["enabled"]:
        return _finish({"status": "disabled", "reason": "topup_gate_off"})
    if dry_run:
        return _finish({"status": "dry_run"})
    if not plan["planned"]:
        return _finish(
            {"status": "no_budget" if plan["skipped"]["daily_budget"] else "nothing_to_enqueue"}
        )

    from app.domains.kol import url_deep_crawl

    items: list[dict[str, Any]] = []
    for target in plan["planned"]:
        try:
            result = url_deep_crawl.enqueue_profile_deep_crawl_job(
                target["profile_url"],
                kol_pool_id=target["kol_pool_id"],
                max_posts=1,
                mode="account_deep",
                representative_video_limit=1,
                staff=staff,
                search_session_id=int(session_id) if session_id else None,
                source=TOPUP_SOURCE,
                queue_lane="batch",
                suppress_final_v1=True,
                suppress_contact_followup=True,
                suppress_profile_followups=True,
            )
            items.append(
                {
                    "kol_pool_id": target["kol_pool_id"],
                    "platform": target["platform"],
                    "missing_fields": target["missing_fields"],
                    "status": str(result.get("status") or ""),
                    "job_id": result.get("job_id"),
                }
            )
        except Exception as exc:  # 单个失败不阻断其余;留声不静默
            logger.warning(
                "field_topup_enqueue_failed kol_pool_id=%s reason=%s",
                target["kol_pool_id"], str(exc)[:200], exc_info=True,
            )
            items.append(
                {
                    "kol_pool_id": target["kol_pool_id"],
                    "platform": target["platform"],
                    "missing_fields": target["missing_fields"],
                    "status": "error",
                    "error": str(exc)[:200],
                }
            )
    enqueued = sum(1 for item in items if item.get("status") == "queued")
    already = sum(1 for item in items if item.get("status") == "already_queued")
    errors = sum(1 for item in items if item.get("status") == "error")
    logger.info(
        "field_topup_enqueued session_id=%s marked=%s planned=%s queued=%s already=%s errors=%s",
        session_id, plan["marked"], plan["planned_fetch_count"], enqueued, already, errors,
    )
    return _finish(
        {
            "status": "ok",
            "enqueued": enqueued,
            "already_queued": already,
            "errors": errors,
            "items": items,
        }
    )


__all__ = [
    "FIELD_TOPUP_SCHEMA",
    "HARD_CAP_DAILY",
    "HARD_CAP_PER_SEARCH",
    "MEASURED_FILL_RATE",
    "TOPUP_FIELDS",
    "TOPUP_SOURCE",
    "enqueue_field_topup_for_candidates",
    "plan_field_topup",
    "topup_settings",
]
