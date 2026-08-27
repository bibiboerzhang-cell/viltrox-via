"""
domains/kol/profile_discovery_rounds.py — 在线发现的「分页 + 多轮」轮次闸。

车道 2 的落点。只管**供给侧的轮次**:哪条腿能翻页、还能不能再跑一轮、这一轮要花
多少次抓取/多少钱。零触 viltrox_fit_score / rule_v0,也不碰任何质量判据
(新鲜度天数、required_terms、器材证据、粉丝下限一律不动)。

■ 各平台到底能不能翻页(2026-08-25 逐个核对 actor 输入 schema,不许猜)
    youtube   ✅ 真分页。YouTube Data API ``search.list`` 收 ``pageToken``、回
              ``nextPageToken``(官方文档 developers.google.com/youtube/v3/docs/search/list)。
              本模块把「每个检索词变体各自的 nextPageToken」当作该腿的游标。
    instagram ❌ **actor 无分页**。``apify/instagram-hashtag-scraper`` 的输入 schema 只有
              hashtags / keywordSearch / resultsType / resultsLimit —— 没有 offset、cursor、
              page、skip、startFrom 任何一个。想要「第二页」只能把 resultsLimit 调大重抓,
              而它是**按 tag 计**的(实测单次 dataset 240~300 条 = 4~5 tag × 60),
              等于重复付一遍第一页的钱。所以这条腿如实上报「不支持分页」,绝不伪造游标。
    tiktok    ❌ **actor 无分页**。``clockworks/free-tiktok-scraper`` 的输入里
              resultsPerPage / maxProfilesPerQuery / searchSection …… 30 个字段中同样没有
              offset/cursor/page/skip。同上,如实上报不支持。

  因此 ``exhausted``(真的没有下一页)这个判定,只有 YouTube 腿能给出「还有下一页」的
  肯定证据;IG/TT 腿在第一轮之后恒为「本腿无下一页」——这是 actor 的事实,不是我们
  跑过一轮就认输。

■ 为什么多轮不会让在线段线性变慢(prod 实测依据见 profile_discovery_supply 模块头)
    在线段耗时 = 最慢那条腿。实测 108.5s 里 107s 是 IG 一条腿(hashtag 中位 45.1s +
    profile 富化中位 23s);YouTube 腿 <2s、TikTok 中位 10.7s。
    第 2 轮起**只跑能翻页的腿**(默认只有 YouTube),于是每多一轮 ≈ +2s,而不是 +108s。
    取舍写明白:第 2 轮起的新增供给是 YouTube 偏向的。要让 IG/TT 也参加多轮,必须
    显式改 env ``VKPI_DISCOVERY_PAGINATED_PLATFORMS``,并且那时每一轮都要过下面的钱闸。
    另外还有两道刹车:整体 deadline(默认 120s)与「上一轮零新增就停」。

■ 钱闸(用户口径:每日上限 $5;任何花钱动作不得自动武装)
    prod 只读复测(2026-08-25,vkpi_ai_cost_ledger 近 14 天):
        apify/instagram-hashtag-scraper   29 run  $16.569  → $0.571/次发现(占 93.7%)
        apify/instagram-profile-scraper  183 run  $ 1.422  → $0.049/次发现(≈6.3 run/次)
        clockworks/free-tiktok-scraper    26 run  $ 0.829  → $0.032/次发现
        streamers/youtube-scraper          0 run  $ 0      → YT 全程走 Data API 快路径
    同日 UTC 当天已花 $2.0073 / 17 run —— 每日 $5 的上限不是纸面数字,平常就用掉四成。
    基准是 per_platform_limit=20;IG 的 resultsLimit 按 tag 计,所以按上限线性外推
    (失败方向:高估,不会低估)。
    ``build_round_gate`` 在**每一轮开跑前**算出这一轮要启动几个 actor run、几次
    YouTube API 调用、预估多少美元,并与「今日已花」相加对上限;读不到台账就拒绝
    额外的付费轮(失败方向安全)。第 1 轮不受本闸约束——它是既有行为,由既有的
    apify_budget 月度硬闸兜底;本闸只管本批新引入的「额外轮次」。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.domains.kol.profile_discovery_supply import resolve_platform_limit

logger = get_logger(__name__)

# 能翻页的腿(env 可覆盖,逗号分隔)。默认只有 YouTube —— 见模块头 actor schema 核对。
PAGINATED_PLATFORMS_ENV = "VKPI_DISCOVERY_PAGINATED_PLATFORMS"
PAGINATED_PLATFORMS_DEFAULT: tuple[str, ...] = ("youtube",)

# 在线段整体墙钟上限(秒)。多轮不得让总耗时线性膨胀。
ONLINE_DEADLINE_ENV = "VKPI_DISCOVERY_ONLINE_DEADLINE_SECONDS"
ONLINE_DEADLINE_DEFAULT_SECONDS = 120.0
# 剩余时间不足这个数就别开新一轮:开了也只会被砍,钱花了结果拿不到。
MIN_ROUND_BUDGET_SECONDS = 6.0

# 在线发现每日花费上限(USD)。用户定的是 $5/天。
DAILY_BUDGET_ENV = "VKPI_DISCOVERY_DAILY_BUDGET_USD"
DAILY_BUDGET_DEFAULT_USD = 5.0

# 每条腿真正会启动的付费 actor(空 tuple = 该腿零 Apify run)。
DISCOVERY_ACTORS: dict[str, tuple[str, ...]] = {
    "instagram": ("apify/instagram-hashtag-scraper", "apify/instagram-profile-scraper"),
    "tiktok": ("clockworks/free-tiktok-scraper",),
    "youtube": (),
}
# 今日花费只认这几个 actor 的台账行(YT Apify 兜底 actor 也算进来:它一旦被触发就是真花钱)。
LEDGER_ACTORS: tuple[str, ...] = (
    "apify/instagram-hashtag-scraper",
    "apify/instagram-profile-scraper",
    "clockworks/free-tiktok-scraper",
    "streamers/youtube-scraper",
)

# prod 14 天实测的「每轮每平台」花费,基准 per_platform_limit=20(依据见模块头)。
MEASURED_BASELINE_LIMIT = 20
MEASURED_ROUND_COST_USD: dict[str, float] = {
    "instagram": 0.620,   # hashtag 0.571 + profile 富化 0.049(2026-08-25 复测)
    "tiktok": 0.032,
    "youtube": 0.0,
}
# YouTube 腿每轮的 API 调用面(检索词变体各一次 search.list + 1 次 channels.list)。
# ``YOUTUBE_MAX_QUERY_VARIANTS`` 只是**不知道真实变体数时**的兜底(旧的 ≤3 词块口径);
# 检索词车道的精准词梯会发更多条,所以传进来的真实数只受 ``CEILING`` 约束 —— 按 3 封顶
# 会把 4~5 条词的轮次**低估**成 301,那是预算闸最不能接受的失败方向。
YOUTUBE_MAX_QUERY_VARIANTS = 3
YOUTUBE_QUERY_VARIANTS_CEILING = 8
YOUTUBE_SEARCH_UNITS_PER_QUERY = 100
YOUTUBE_CHANNELS_LIST_UNITS = 1
YOUTUBE_API_CALLS_PER_ROUND = YOUTUBE_MAX_QUERY_VARIANTS + 1
YOUTUBE_QUOTA_UNITS_PER_ROUND = (
    YOUTUBE_SEARCH_UNITS_PER_QUERY * YOUTUBE_MAX_QUERY_VARIANTS + YOUTUBE_CHANNELS_LIST_UNITS
)


def youtube_quota_units(query_variants: Any = None) -> int:
    """这一轮 YouTube 腿要吃多少配额单位:search.list 100/变体 + channels.list 1。

    **2026-08-27 修「每轮固定按 301 预报、实际 201」的 50% 高估**:预报此前写死 3 条变体,
    真发出去的常常只有 2 条(候选装满提前 break)。虚高的账会让轮次守门按不存在的消耗
    提前收手、少跑轮次。调用方知道真实/计划变体数就传进来;不知道才退回旧上限
    (失败方向:高估,不会低估)。
    """
    try:
        variants = int(query_variants)
    except (TypeError, ValueError):
        variants = 0
    if variants <= 0:
        variants = YOUTUBE_MAX_QUERY_VARIANTS
    variants = min(variants, YOUTUBE_QUERY_VARIANTS_CEILING)
    return YOUTUBE_SEARCH_UNITS_PER_QUERY * variants + YOUTUBE_CHANNELS_LIST_UNITS


def _text(value: Any) -> str:
    return str(value or "").strip()


def _code(value: Any) -> str:
    return _text(value).lower()


def _positive_float(raw: Any) -> float | None:
    try:
        value = float(_text(raw))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def paginated_platforms() -> frozenset[str]:
    """第 2 轮起允许继续翻页的腿。env 空/垃圾 → 默认只有 YouTube(便宜且快)。"""
    raw = os.environ.get(PAGINATED_PLATFORMS_ENV)
    if raw is None:
        return frozenset(PAGINATED_PLATFORMS_DEFAULT)
    picked = {_code(part) for part in str(raw).split(",") if _code(part)}
    picked &= set(DISCOVERY_ACTORS)
    return frozenset(picked) if picked else frozenset(PAGINATED_PLATFORMS_DEFAULT)


def online_deadline_seconds() -> float:
    """在线段整体墙钟上限。配错 env 一律退回默认(不会把整段掐成 0 秒)。"""
    return _positive_float(os.environ.get(ONLINE_DEADLINE_ENV)) or ONLINE_DEADLINE_DEFAULT_SECONDS


def daily_budget_usd() -> float:
    """在线发现每日花费上限。env 非法 → 退回 $5(不会变成不设防的 0)。"""
    return _positive_float(os.environ.get(DAILY_BUDGET_ENV)) or DAILY_BUDGET_DEFAULT_USD


def empty_cursor() -> dict[str, Any]:
    return {"page_cursors": {}, "has_more": {}, "supported": {}}


def normalize_cursor(value: Any) -> dict[str, Any]:
    """把上一轮的 next_cursor 归一成 {page_cursors, has_more, supported};垃圾 → 空。"""
    raw = value if isinstance(value, dict) else {}
    out = empty_cursor()
    for key in ("page_cursors", "has_more", "supported"):
        section = raw.get(key)
        if isinstance(section, dict):
            out[key] = {_code(k): v for k, v in section.items() if _code(k)}
    return out


def leg_cursors(value: Any) -> dict[str, Any]:
    """取出 {平台: 该腿游标}。既吃完整游标体({page_cursors,has_more,supported}),
    也吃扁平的 {平台: 游标}(调用方两种形状都可能给)。垃圾 → {}。"""
    raw = value if isinstance(value, dict) else {}
    if "page_cursors" in raw:
        return normalize_cursor(raw)["page_cursors"]
    return {_code(k): v for k, v in raw.items() if _code(k) and v}


def pagination_state(platform_results: Any) -> dict[str, Any]:
    """从 provider 的 platform_results 收出「下一页游标 / 还有没有下一页」。

    只搬运 provider 层 metadata 里的事实:``next_page_cursor``(该腿的游标)与
    ``has_more``(该腿真的还有下一页)。metadata 没说 → 该腿 has_more=False,
    绝不因为「跑过一轮了」就替 actor 声称还有下一页,也不因为想多跑而伪造游标。
    """
    cursor = empty_cursor()
    rows = platform_results if isinstance(platform_results, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        platform = _code(row.get("platform"))
        if not platform:
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        supported = bool(meta.get("pagination_supported"))
        cursor["supported"][platform] = supported
        next_cursor = meta.get("next_page_cursor")
        if supported and next_cursor:
            cursor["page_cursors"][platform] = next_cursor
        cursor["has_more"][platform] = bool(supported and meta.get("has_more"))
    return {
        "next_page_cursors": cursor["page_cursors"],
        "has_more": any(cursor["has_more"].values()),
        "next_cursor": cursor,
    }


def platforms_for_round(round_no: int, platforms: Any, cursor: Any = None) -> list[str]:
    """第 N 轮(N≥2)该跑哪几条腿。

    条件是**并发**的两件事:① 这条腿在允许翻页的名单里(默认只有 YouTube,见模块头
    的成本/延迟取舍);② provider 上一轮如实报了「本腿还有下一页」—— 该腿要么真给了
    nextPageToken,要么本轮装满提前 break、还有检索词变体一次都没查过。
    两条缺一就不跑;这正是「exhausted 基于真的没有下一页」的落点。
    """
    if int(round_no or 0) <= 1:
        return [_code(item) for item in (platforms or []) if _code(item)]
    allowed = paginated_platforms()
    state = normalize_cursor(cursor)
    out: list[str] = []
    for item in platforms or []:
        platform = _code(item)
        if not platform or platform not in allowed:
            continue
        if not state["has_more"].get(platform):
            continue
        out.append(platform)
    return out


def round_cost_forecast(
    platforms: Any,
    *,
    round_no: int = 1,
    per_platform_limit: int = MEASURED_BASELINE_LIMIT,
    per_platform_limits: Any = None,
    youtube_query_variants: Any = None,
) -> dict[str, Any]:
    """这一轮要花多少次抓取 / 多少钱。跑前必须能报出来的那份账。

    口径:每条腿会启动几个 Apify actor run(付费)、几次 YouTube API 调用(免费但吃配额)、
    按 prod 实测单价线性外推的美元数。IG 的 resultsLimit 按 tag 计,线性外推正确;
    TT 的 resultsPerPage 会被检索词变体摊薄,线性外推是**高估**——失败方向安全。

    ``youtube_query_variants``:这一轮 YouTube 腿真会发几条检索词变体。传了就按它算配额
    (治「固定 301」的 50% 高估);不传退回旧上限,行为逐字不变。
    """
    legs = [_code(item) for item in (platforms or []) if _code(item)]
    by_platform: dict[str, dict[str, Any]] = {}
    apify_runs = 0
    api_calls = 0
    quota_units = 0
    estimated_usd = 0.0
    for platform in legs:
        limit = resolve_platform_limit(platform, int(per_platform_limit or MEASURED_BASELINE_LIMIT), per_platform_limits)
        runs = len(DISCOVERY_ACTORS.get(platform, ()))
        unit = MEASURED_ROUND_COST_USD.get(platform, 0.0)
        usd = round(unit * limit / float(MEASURED_BASELINE_LIMIT), 4)
        units = youtube_quota_units(youtube_query_variants) if platform == "youtube" else 0
        calls = (units - YOUTUBE_CHANNELS_LIST_UNITS) // YOUTUBE_SEARCH_UNITS_PER_QUERY + 1 if units else 0
        by_platform[platform] = {
            "requested_limit": limit,
            "apify_runs": runs,
            "youtube_api_calls": calls,
            "youtube_quota_units": units,
            "estimated_usd": usd,
        }
        apify_runs += runs
        api_calls += calls
        quota_units += units
        estimated_usd += usd
    return {
        "round_no": max(1, int(round_no or 1)),
        "platforms": legs,
        "apify_runs": apify_runs,
        "youtube_api_calls": api_calls,
        "youtube_quota_units": quota_units,
        "estimated_usd": round(estimated_usd, 4),
        "by_platform": by_platform,
    }


def forecast_line(forecast: dict[str, Any]) -> str:
    """一行人话的抓取账,用于日志与「跑前报账」。"""
    data = forecast if isinstance(forecast, dict) else {}
    return (
        f"round={data.get('round_no')} platforms={','.join(data.get('platforms') or []) or '-'} "
        f"apify_runs={data.get('apify_runs')} youtube_api_calls={data.get('youtube_api_calls')} "
        f"estimated_usd={data.get('estimated_usd')}"
    )


ROUND_PLAN_SCHEMA = "discovery_round_plan_v1"


def round_plan_record(
    *,
    forecasts: Any,
    round_gate: Any = None,
    provider_rounds: Any = None,
    actual_quota_units: Any = None,
    actual_apify_runs: Any = None,
) -> dict[str, Any]:
    """落库用的「这次搜索到底花了几次抓取」记录。纯记账,零判定。

    每一轮一条:跑了哪几条腿、启动几个 Apify run、几次 YouTube API 调用、预估多少美元;
    再带上轮次闸的判词(为什么停在第 N 轮)。诚实空态:没跑过在线段就只有零值。

    ``actual_*``(2026-08-27):provider metadata 报回来的**真实**消耗。此前这份记录只有预报,
    于是「预报 903、实际 603」在库里查不出来。有了实际值,预报误差是一条 SELECT 的事。
    """
    rows = [dict(item) for item in (forecasts or []) if isinstance(item, dict)]
    gate = round_gate if isinstance(round_gate, dict) else {}
    forecast_units = sum(int(row.get("youtube_quota_units") or 0) for row in rows)
    actual_units = None if actual_quota_units is None else max(0, int(actual_quota_units))
    return {
        "schema": ROUND_PLAN_SCHEMA,
        "rounds": rows,
        "provider_rounds": max(0, int(provider_rounds or 0)),
        "apify_runs_total": sum(int(row.get("apify_runs") or 0) for row in rows),
        "youtube_api_calls_total": sum(int(row.get("youtube_api_calls") or 0) for row in rows),
        "youtube_quota_units_total": forecast_units,
        # 真实消耗与预报误差(None = 本次没有可对账的实际值,不拿预报冒充实际)。
        "youtube_quota_units_actual": actual_units,
        "quota_forecast_delta_units": None if actual_units is None else forecast_units - actual_units,
        "apify_runs_actual": None if actual_apify_runs is None else max(0, int(actual_apify_runs)),
        "estimated_usd_total": round(sum(float(row.get("estimated_usd") or 0.0) for row in rows), 4),
        "daily_budget_usd": round(daily_budget_usd(), 4),
        "online_deadline_seconds": round(online_deadline_seconds(), 3),
        "paginated_platforms": sorted(paginated_platforms()),
        "stopped_by": _text(gate.get("stopped_by")) or None,
        "gate_verdicts": [
            {
                "allowed": bool(item.get("allowed")),
                "reason": _text(item.get("reason")),
                "seconds_left": item.get("seconds_left"),
                "today_spend_usd": item.get("today_spend_usd"),
            }
            for item in (gate.get("verdicts") or [])
            if isinstance(item, dict)
        ],
    }


def daily_discovery_spend_usd(conn: Any = None) -> dict[str, Any]:
    """今日(UTC)在线发现相关 actor 的**已记账**花费。只读 SELECT,零副作用。

    读不到(表缺/连接挂/权限)时 ``available=False`` —— 调用方必须按此**拒绝**额外的
    付费轮次:钱的方向上,读不到账本 = 不许再花。
    """
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = day_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    placeholders = ",".join(["?"] * len(LEDGER_ACTORS))
    try:
        if conn is None:
            from app.db.connection import get_conn

            conn = get_conn()
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(cost_usd), 0) AS spend_usd, COUNT(*) AS run_count
            FROM vkpi_ai_cost_ledger
            WHERE ai_provider = 'apify'
              AND occurred_at >= ?
              AND model_name IN ({placeholders})
            """,
            (cutoff, *LEDGER_ACTORS),
        ).fetchone()
    except Exception as exc:
        logger.warning(
            "discovery_daily_spend_unreadable reason=%s(额外付费轮次将被拒绝)",
            str(exc)[:200], exc_info=True,
        )
        return {"available": False, "spend_usd": 0.0, "run_count": 0, "since": cutoff}
    data = dict(row) if row else {}
    try:
        spend = float(data.get("spend_usd") or 0.0)
    except (TypeError, ValueError):
        spend = 0.0
    try:
        runs = int(data.get("run_count") or 0)
    except (TypeError, ValueError):
        runs = 0
    return {"available": True, "spend_usd": round(spend, 4), "run_count": runs, "since": cutoff}


def build_round_gate(
    *,
    legs_for_round: Callable[[int], list[str]],
    per_platform_limit: int = MEASURED_BASELINE_LIMIT,
    per_platform_limits: Any = None,
    started_monotonic: float | None = None,
    deadline_seconds: float | None = None,
    budget_usd: float | None = None,
    spend_reader: Callable[[], dict[str, Any]] | None = None,
    progress_reader: Callable[[], int] | None = None,
) -> Callable[[int], dict[str, Any]]:
    """造第 2 轮起的准入闸。返回 ``gate(round_no) -> verdict``。

    verdict 恒带 ``allowed`` / ``reason`` / ``forecast``,拒绝时 reason 是可落库的机器码:
      ``no_paginated_leg_left``   真的没有还能翻页的腿(= actor 无分页 或 已到最后一页)
      ``no_progress_last_round``  上一轮 provider 一个候选都没给 → 再翻页也是白花
      ``online_deadline_exhausted`` 在线段整体墙钟不够再跑一轮
      ``daily_budget_exhausted``  今日已花 + 本轮预估 > 每日上限
      ``daily_budget_unreadable`` 台账读不到 → 钱的方向上一律拒绝
    """
    started = float(started_monotonic if started_monotonic is not None else time.monotonic())
    deadline = float(deadline_seconds if deadline_seconds is not None else online_deadline_seconds())
    cap = float(budget_usd if budget_usd is not None else daily_budget_usd())
    read_spend = spend_reader or daily_discovery_spend_usd

    def _verdict(allowed: bool, reason: str, forecast: dict[str, Any], **extra: Any) -> dict[str, Any]:
        payload = {
            "allowed": bool(allowed),
            "reason": reason,
            "forecast": forecast,
            "seconds_left": round(max(0.0, deadline - (time.monotonic() - started)), 3),
            "daily_budget_usd": round(cap, 4),
            **extra,
        }
        logger.info(
            "discovery_round_gate allowed=%s reason=%s %s seconds_left=%s",
            payload["allowed"], reason or "-", forecast_line(forecast), payload["seconds_left"],
        )
        return payload

    def gate(round_no: int) -> dict[str, Any]:
        legs = [_code(item) for item in (legs_for_round(int(round_no)) or []) if _code(item)]
        forecast = round_cost_forecast(
            legs,
            round_no=int(round_no),
            per_platform_limit=per_platform_limit,
            per_platform_limits=per_platform_limits,
        )
        if not legs:
            return _verdict(False, "no_paginated_leg_left", forecast)
        if progress_reader is not None:
            last_yield = int(progress_reader() or 0)
            if last_yield <= 0:
                # 上一轮一个候选都没出 → 再翻一页大概率还是空,别拿时间和钱去赌。
                return _verdict(False, "no_progress_last_round", forecast, last_round_yield=last_yield)
        if (deadline - (time.monotonic() - started)) < MIN_ROUND_BUDGET_SECONDS:
            return _verdict(False, "online_deadline_exhausted", forecast)
        estimated = float(forecast.get("estimated_usd") or 0.0)
        if estimated <= 0:
            return _verdict(True, "", forecast, spend_checked=False)
        spend = read_spend() or {}
        if spend.get("available") is not True:
            return _verdict(False, "daily_budget_unreadable", forecast, spend_checked=True)
        spent = float(spend.get("spend_usd") or 0.0)
        if spent + estimated > cap:
            return _verdict(
                False, "daily_budget_exhausted", forecast,
                spend_checked=True, today_spend_usd=round(spent, 4),
            )
        return _verdict(True, "", forecast, spend_checked=True, today_spend_usd=round(spent, 4))

    return gate
