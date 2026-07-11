"""Dashboard summary company/official-matrix aggregators (moved from summary.py, behavior unchanged)."""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.domains.dashboard.metric_maturity import _OFFICIAL_CHANNEL_FILTER_SQL
from app.domains.channels.official import _account_group
from app.domains.dashboard.summary_rows import _as_int, _fetch_dicts, _row_value


_COMPANY_SERIES_METRICS = ("kol-count", "active-30d", "exposure", "engagement")
_COMPANY_SERIES_BASIS = {
    "kol-count": "Official accounts with a channel snapshot available as of each date.",
    "active-30d": (
        "Distinct official accounts with a post published or a positive post-metric "
        "delta observed in the rolling window ending on each date."
    ),
    "exposure": (
        "Rolling-window sum of positive views_delta_24h values observed for official "
        "accounts; missing daily rows are not carried forward."
    ),
    "engagement": (
        "100 * sum(as-of total_likes + total_comments) / sum(as-of total_views) "
        "across metric-complete official accounts."
    ),
}


def _series_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _channel_key(value: Any) -> str | None:
    if hasattr(value, "get"):
        value = value.get("channel_id", value.get("id"))
    if value is None or value == "":
        return None
    return str(value)


def _coverage_ratio(count: int, official_accounts: int) -> float | None:
    if official_accounts <= 0:
        return None
    return round(count / official_accounts, 6)


def _series_delta_pct(points: list[dict[str, Any]]) -> float | None:
    if len(points) < 2:
        return None
    first = float(points[0]["value"])
    last = float(points[-1]["value"])
    if first == 0:
        return None
    return round((last - first) / abs(first) * 100, 6)


def _series_coverage(
    stats: list[dict[str, int]],
    *,
    official_accounts: int,
    point_days: int,
    requires_baseline: bool,
) -> dict[str, Any]:
    latest = stats[-1] if stats else {
        "eligible": 0,
        "direct": 0,
        "carried": 0,
        "baseline_direct": 0,
        "baseline_carried": 0,
    }
    eligible_counts = [item["eligible"] for item in stats]
    minimum = min(eligible_counts) if eligible_counts else 0
    coverage = {
        "official_accounts": official_accounts,
        "calendar_days": len(stats),
        "point_days": point_days,
        "eligible_accounts_latest": latest["eligible"],
        "eligible_ratio_latest": _coverage_ratio(latest["eligible"], official_accounts),
        "eligible_accounts_min": minimum,
        "eligible_ratio_min": _coverage_ratio(minimum, official_accounts),
        "complete_days": sum(
            1 for count in eligible_counts if official_accounts > 0 and count == official_accounts
        ),
        "direct_snapshot_accounts_latest": latest["direct"],
        "carried_forward_accounts_latest": latest["carried"],
        "baseline_direct_accounts_latest": None,
        "baseline_carried_forward_accounts_latest": None,
    }
    if requires_baseline:
        coverage["baseline_direct_accounts_latest"] = latest["baseline_direct"]
        coverage["baseline_carried_forward_accounts_latest"] = latest["baseline_carried"]
    return coverage


def _empty_company_metric_series(
    official_accounts: int,
    *,
    window_days: int,
) -> dict[str, dict[str, Any]]:
    return {
        metric: {
            "points": [],
            "delta_pct": None,
            "window_days": window_days,
            "basis": {
                "definition": _COMPANY_SERIES_BASIS[metric],
                "state_method": (
                    "rolling post evidence; no carry-forward"
                    if metric == "active-30d"
                    else "rolling positive daily deltas; no carry-forward"
                    if metric == "exposure"
                    else "per-channel as-of carry-forward"
                ),
                "baseline_guard": None,
                "delta_method": "first-to-last point percentage; null when unavailable or the first value is zero",
                "tables": (
                    ["vkpi_employee_channels", "vkpi_channel_post_metrics"]
                    if metric == "active-30d"
                    else ["vkpi_employee_channels", "vkpi_channel_metrics"]
                ),
            },
            "coverage": _series_coverage(
                [],
                official_accounts=official_accounts,
                point_days=0,
                requires_baseline=False,
            ),
            "source_dates": [],
        }
        for metric in _COMPANY_SERIES_METRICS
    }


def _build_company_metric_series_from_snapshots(
    official_channel_ids: list[Any],
    snapshot_rows: list[dict[str, Any]],
    *,
    post_rows: list[dict[str, Any]] | None = None,
    window_days: int = 30,
) -> dict[str, dict[str, Any]]:
    """Build auditable official-account series without querying or mutating state.

    Each calendar date is reconstructed per channel with the latest snapshot at
    or before that date. This prevents a day with only 16/18 fresh rows from
    appearing as a matrix-wide cumulative-metric collapse.
    """
    days = max(1, int(window_days or 30))
    channel_keys: list[str] = []
    seen_keys: set[str] = set()
    for raw_channel_id in official_channel_ids:
        key = _channel_key(raw_channel_id)
        if key is not None and key not in seen_keys:
            channel_keys.append(key)
            seen_keys.add(key)
    official_accounts = len(channel_keys)

    # UNIQUE(channel_id, snapshot_date) is the production contract. The rank
    # still makes this pure function deterministic for legacy/fixture duplicates.
    daily: dict[str, dict[date, tuple[tuple[str, int], dict[str, Any]]]] = {
        key: {} for key in channel_keys
    }
    for row in snapshot_rows:
        key = _channel_key(_row_value(row, "channel_id"))
        snapshot_date = _series_date(_row_value(row, "snapshot_date"))
        if key not in seen_keys or snapshot_date is None:
            continue
        rank = (
            str(_row_value(row, "captured_at") or ""),
            _optional_int(_row_value(row, "id")) or -1,
        )
        state = {
            "source_date": snapshot_date,
            "posts_count": _optional_int(_row_value(row, "posts_count")),
            "views_delta_24h": _optional_int(_row_value(row, "views_delta_24h")),
            "total_views": _optional_int(_row_value(row, "total_views")),
            "total_likes": _optional_int(_row_value(row, "total_likes")),
            "total_comments": _optional_int(_row_value(row, "total_comments")),
        }
        existing = daily[key].get(snapshot_date)
        if existing is None or rank >= existing[0]:
            daily[key][snapshot_date] = (rank, state)

    histories: dict[str, tuple[list[date], list[dict[str, Any]]]] = {}
    all_snapshot_dates: list[date] = []
    for key in channel_keys:
        ordered = sorted(daily[key].items())
        dates = [snapshot_date for snapshot_date, _ in ordered]
        states = [ranked_state[1] for _, ranked_state in ordered]
        for index, state in enumerate(states):
            state["has_prior_snapshot"] = index > 0
        histories[key] = (dates, states)
        all_snapshot_dates.extend(dates)
    if not all_snapshot_dates:
        return _empty_company_metric_series(official_accounts, window_days=days)

    latest_date = max(all_snapshot_dates)
    target_dates = [latest_date - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    points: dict[str, list[dict[str, Any]]] = {metric: [] for metric in _COMPANY_SERIES_METRICS}
    stats: dict[str, list[dict[str, int]]] = {metric: [] for metric in _COMPANY_SERIES_METRICS}
    used_dates: dict[str, set[date]] = {metric: set() for metric in _COMPANY_SERIES_METRICS}

    def as_of(key: str, target: date) -> dict[str, Any] | None:
        dates, states = histories[key]
        index = bisect_right(dates, target) - 1
        return states[index] if index >= 0 else None

    def audit_stats(
        eligible: list[str],
        current: dict[str, dict[str, Any]],
        target: date,
        baseline: dict[str, dict[str, Any]] | None = None,
        baseline_date: date | None = None,
    ) -> dict[str, int]:
        direct = sum(1 for key in eligible if current[key]["source_date"] == target)
        baseline_direct = 0
        if baseline is not None and baseline_date is not None:
            baseline_direct = sum(
                1 for key in eligible if baseline[key]["source_date"] == baseline_date
            )
        return {
            "eligible": len(eligible),
            "direct": direct,
            "carried": len(eligible) - direct,
            "baseline_direct": baseline_direct,
            "baseline_carried": len(eligible) - baseline_direct if baseline is not None else 0,
        }

    def append_point(
        metric: str,
        target: date,
        value: int | float | None,
        eligible: list[str],
        current: dict[str, dict[str, Any]],
        metric_stats: dict[str, int],
        baseline: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        stats[metric].append(metric_stats)
        if value is None or not eligible:
            return
        point = {
            "date": target.isoformat(),
            "value": value,
            "covered_accounts": metric_stats["eligible"],
            "coverage_pct": _coverage_ratio(metric_stats["eligible"], official_accounts),
            "direct_snapshot_accounts": metric_stats["direct"],
            "carried_forward_accounts": metric_stats["carried"],
        }
        if baseline is not None:
            point["baseline_direct_accounts"] = metric_stats["baseline_direct"]
            point["baseline_carried_forward_accounts"] = metric_stats["baseline_carried"]
        points[metric].append(point)
        used_dates[metric].update(current[key]["source_date"] for key in eligible)
        if baseline is not None:
            used_dates[metric].update(baseline[key]["source_date"] for key in eligible)

    normalized_post_rows: list[dict[str, Any]] = []
    for row in post_rows or []:
        key = _channel_key(_row_value(row, "channel_id"))
        snapshot_date = _series_date(_row_value(row, "snapshot_date"))
        posted_at = _series_date(_row_value(row, "posted_at"))
        if key not in seen_keys or (snapshot_date is None and posted_at is None):
            continue
        positive_flag = _row_value(row, "has_positive_delta")
        has_positive_delta = (
            str(positive_flag).strip().lower() in {"1", "true", "t", "yes"}
            if positive_flag is not None
            else any(
                (_optional_int(_row_value(row, field)) or 0) > 0
                for field in ("views_delta", "likes_delta", "comments_delta")
            )
        )
        normalized_post_rows.append(
            {
                "channel_key": key,
                "snapshot_date": snapshot_date,
                "posted_at": posted_at,
                "has_positive_delta": has_positive_delta,
            }
        )

    for target in target_dates:
        current = {
            key: state for key in channel_keys if (state := as_of(key, target)) is not None
        }

        roster_keys = list(current)
        roster_stats = audit_stats(roster_keys, current, target)
        append_point(
            "kol-count",
            target,
            len(roster_keys) if roster_keys else None,
            roster_keys,
            current,
            roster_stats,
        )

        window_start = target - timedelta(days=days - 1)
        active_source_rows = [
            row for row in normalized_post_rows
            if (
                row["posted_at"] is not None
                and window_start <= row["posted_at"] <= target
            ) or (
                row["snapshot_date"] is not None
                and window_start <= row["snapshot_date"] <= target
                and row["has_positive_delta"]
            )
        ]
        active_keys = sorted({row["channel_key"] for row in active_source_rows})
        observed_post_keys = {
            row["channel_key"] for row in normalized_post_rows
            if (
                row["snapshot_date"] is not None and row["snapshot_date"] <= target
            ) or (
                row["posted_at"] is not None and row["posted_at"] <= target
            )
        }
        active_direct_keys = {
            row["channel_key"] for row in normalized_post_rows
            if row["snapshot_date"] == target or row["posted_at"] == target
        }
        active_stats = {
            "eligible": len(observed_post_keys),
            "direct": len(active_direct_keys),
            "carried": 0,
            "baseline_direct": 0,
            "baseline_carried": 0,
        }
        stats["active-30d"].append(active_stats)
        if normalized_post_rows:
            points["active-30d"].append(
                {
                    "date": target.isoformat(),
                    "value": len(active_keys),
                    "covered_accounts": len(observed_post_keys),
                    "coverage_pct": _coverage_ratio(len(observed_post_keys), official_accounts),
                    "direct_snapshot_accounts": len(active_direct_keys),
                    "carried_forward_accounts": 0,
                }
            )
            for row in active_source_rows:
                source_date = row["snapshot_date"] or row["posted_at"]
                if source_date is not None:
                    used_dates["active-30d"].add(source_date)

        exposure_states: list[dict[str, Any]] = []
        exposure_keys_set: set[str] = set()
        exposure_direct_keys: set[str] = set()
        for key in channel_keys:
            for snapshot_date, ranked_state in daily[key].items():
                if not window_start <= snapshot_date <= target:
                    continue
                state = ranked_state[1]
                exposure_states.append(state)
                exposure_keys_set.add(key)
                if snapshot_date == target:
                    exposure_direct_keys.add(key)
                used_dates["exposure"].add(snapshot_date)
        exposure_keys = sorted(exposure_keys_set)
        exposure_value = (
            sum(max(state["views_delta_24h"] or 0, 0) for state in exposure_states)
            if exposure_states
            else None
        )
        exposure_stats = {
            "eligible": len(exposure_keys),
            "direct": len(exposure_direct_keys),
            "carried": 0,
            "baseline_direct": 0,
            "baseline_carried": 0,
        }
        stats["exposure"].append(exposure_stats)
        if exposure_value is not None and exposure_keys:
            points["exposure"].append(
                {
                    "date": target.isoformat(),
                    "value": exposure_value,
                    "covered_accounts": len(exposure_keys),
                    "coverage_pct": _coverage_ratio(len(exposure_keys), official_accounts),
                    "direct_snapshot_accounts": len(exposure_direct_keys),
                    "carried_forward_accounts": 0,
                }
            )

        engagement_keys = [
            key for key, state in current.items()
            if all(state[field] is not None for field in ("total_views", "total_likes", "total_comments"))
        ]
        engagement_stats = audit_stats(engagement_keys, current, target)
        engagement_views = sum(current[key]["total_views"] for key in engagement_keys)
        engagement_value = None
        if engagement_keys and engagement_views > 0:
            interactions = sum(
                current[key]["total_likes"] + current[key]["total_comments"]
                for key in engagement_keys
            )
            engagement_value = round(interactions / engagement_views * 100, 6)
        append_point(
            "engagement", target, engagement_value, engagement_keys, current, engagement_stats
        )

    result = _empty_company_metric_series(official_accounts, window_days=days)
    for metric in _COMPANY_SERIES_METRICS:
        result[metric]["points"] = points[metric]
        result[metric]["delta_pct"] = _series_delta_pct(points[metric])
        result[metric]["coverage"] = _series_coverage(
            stats[metric],
            official_accounts=official_accounts,
            point_days=len(points[metric]),
            requires_baseline=False,
        )
        result[metric]["source_dates"] = [
            source_date.isoformat() for source_date in sorted(used_dates[metric])
        ]
    return result


def _build_company_metric_series(
    *,
    window_days: int = 30,
    lookback_days: int = 60,
) -> dict[str, dict[str, Any]]:
    """Read official snapshots and delegate all time-series math to the pure builder."""
    days = max(1, int(window_days or 30))
    lookback = max(days * 2, int(lookback_days or 60))
    official_rows = _fetch_dicts(
        f"""
        SELECT c.id AS channel_id
        FROM vkpi_employee_channels c
        WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        ORDER BY c.id
        """
    )
    official_channel_ids = [row.get("channel_id") for row in official_rows]
    if not official_channel_ids:
        return _empty_company_metric_series(0, window_days=days)

    snapshot_rows = _fetch_dicts(
        f"""
        WITH official AS (
            SELECT c.id
            FROM vkpi_employee_channels c
            WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        ),
        latest AS (
            SELECT MAX(m.snapshot_date) AS latest_date
            FROM vkpi_channel_metrics m
            JOIN official o ON o.id = m.channel_id
        )
        SELECT m.id, m.channel_id, m.snapshot_date, m.posts_count, m.views_delta_24h,
               m.total_views, m.total_likes, m.total_comments, m.captured_at
        FROM vkpi_channel_metrics m
        JOIN official o ON o.id = m.channel_id
        CROSS JOIN latest
        WHERE latest.latest_date IS NOT NULL
          AND m.snapshot_date >= latest.latest_date - INTERVAL '{lookback} days'
          AND m.snapshot_date <= latest.latest_date
        ORDER BY m.channel_id, m.snapshot_date, m.captured_at, m.id
        """
    )
    post_rows = _fetch_dicts(
        f"""
        WITH official AS (
            SELECT c.id
            FROM vkpi_employee_channels c
            WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        ),
        latest AS (
            SELECT MAX(m.snapshot_date) AS latest_date
            FROM vkpi_channel_metrics m
            JOIN official o ON o.id = m.channel_id
        )
        SELECT pm.channel_id, pm.snapshot_date, NULL::date AS posted_at,
               TRUE AS has_positive_delta
        FROM vkpi_channel_post_metrics pm
        JOIN official o ON o.id = pm.channel_id
        CROSS JOIN latest
        WHERE latest.latest_date IS NOT NULL
          AND pm.snapshot_date BETWEEN latest.latest_date - INTERVAL '{lookback} days' AND latest.latest_date
          AND (
              COALESCE(pm.views_delta, 0) > 0
              OR COALESCE(pm.likes_delta, 0) > 0
              OR COALESCE(pm.comments_delta, 0) > 0
          )
        GROUP BY pm.channel_id, pm.snapshot_date
        UNION ALL
        SELECT pm.channel_id, NULL::date AS snapshot_date, pm.posted_at::date AS posted_at,
               FALSE AS has_positive_delta
        FROM vkpi_channel_post_metrics pm
        JOIN official o ON o.id = pm.channel_id
        CROSS JOIN latest
        WHERE latest.latest_date IS NOT NULL
          AND pm.posted_at::date BETWEEN latest.latest_date - INTERVAL '{lookback} days' AND latest.latest_date
        GROUP BY pm.channel_id, pm.posted_at::date
        """
    )
    return _build_company_metric_series_from_snapshots(
        official_channel_ids,
        snapshot_rows,
        post_rows=post_rows,
        window_days=days,
    )


def _build_company_roster_detail() -> dict[str, Any]:
    """官方账号(18 个)真实口径子块,供 Dashboard Active Roster 详情的「公司账号」scope 消费。

    数据源(全部官方账号过滤,与 official.py / official_account_count 一致):
      - 账号 / followers / total_views:vkpi_employee_channels(deleted_at IS NULL AND status='active'
        AND _OFFICIAL_CHANNEL_FILTER_SQL)JOIN 每账号最新一条 vkpi_channel_metrics(snapshot_date/captured_at/id DESC)。
      - by_platform:官方账号按 platform 计数(6 平台)。
      - groups:由 account_handle 经 official._account_group 派生 main_brand/product_line/regional。
      - movers:vkpi_channel_post_metrics(每 (channel_id, post_uid) 取最新快照)JOIN 官方账号,按 views DESC 取前 10。
    输出键(前端按此解析,勿改):total_pool / active_roster / followers / total_views /
    by_platform[{platform,count,pct}] / groups{main_brand,product_line,regional} /
    movers[{kol_name,handle,platform,title,url,value,publish_date}]。
    """
    conn = get_conn()

    account_rows = _fetch_dicts(
        f"""
        WITH official AS (
          SELECT c.id, LOWER(c.platform) AS platform, c.account_handle
          FROM vkpi_employee_channels c
          WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        )
        SELECT o.id, o.platform, o.account_handle,
               COALESCE(m.followers, 0) AS followers,
               COALESCE(m.total_views, 0) AS total_views
        FROM official o
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT mm.id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = o.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        """
    )
    total_accounts = len(account_rows)
    followers = sum(_as_int(row.get("followers")) for row in account_rows)
    total_views = sum(_as_int(row.get("total_views")) for row in account_rows)

    platform_counts: dict[str, int] = {}
    group_counts = {"main_brand": 0, "product_line": 0, "regional": 0}
    for row in account_rows:
        platform = str(row.get("platform") or "unknown") or "unknown"
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        group_counts[_account_group(row.get("account_handle"))] += 1
    platform_total = total_accounts or 1
    by_platform = [
        {"platform": platform, "count": count, "pct": count / platform_total}
        for platform, count in sorted(
            platform_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    mover_rows = _fetch_dicts(
        f"""
        WITH official AS (
          SELECT c.id, c.account_handle, c.account_display_name
          FROM vkpi_employee_channels c
          WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        ),
        latest_posts AS (
          SELECT pm.channel_id, pm.platform, pm.title, pm.post_url AS url,
                 pm.views, pm.posted_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY pm.channel_id, pm.post_uid
                   ORDER BY pm.snapshot_date DESC, pm.captured_at DESC, pm.id DESC
                 ) AS rn
          FROM vkpi_channel_post_metrics pm
          JOIN official o ON o.id = pm.channel_id
        )
        SELECT o.account_handle, o.account_display_name,
               lp.platform, lp.title, lp.url, lp.views, lp.posted_at
        FROM latest_posts lp
        JOIN official o ON o.id = lp.channel_id
        WHERE lp.rn = 1 AND lp.views IS NOT NULL
        ORDER BY lp.views DESC NULLS LAST
        LIMIT 10
        """
    )
    movers = [
        {
            "kol_name": _row_value(row, "account_display_name") or _row_value(row, "account_handle"),
            "handle": _row_value(row, "account_handle"),
            "platform": _row_value(row, "platform"),
            "title": _row_value(row, "title"),
            "url": _row_value(row, "url"),
            "value": _as_int(row.get("views")),
            "publish_date": _row_value(row, "posted_at"),
        }
        for row in mover_rows
    ]

    return {
        "total_pool": total_accounts,
        "active_roster": total_accounts,
        "followers": followers,
        "total_views": total_views,
        "by_platform": by_platform,
        "groups": group_counts,
        "movers": movers,
    }


def _build_company_window_metrics() -> dict[str, Any]:
    """官方矩阵(18 个官方账号)真实 30d 窗口指标,供 Dashboard「公司账号」scope 的 Total Exposure / Engagement Rate 消费。

    与 _build_company_roster_detail 同口径(_OFFICIAL_CHANNEL_FILTER_SQL)。两项均为真实数,诚实标注 30d 与 lifetime:
      - exposure_30d:vkpi_channel_metrics 官方账号近 30 天(latest snapshot 起回溯 30 天)的逐日
        SUM(GREATEST(views_delta_24h, 0))——真实「30d 新增曝光增量」,绝不取 lifetime total_views 代替。
        (不用「latest total_views − earliest total_views」:部分账号首条快照为 0 回填,该差值会把 0→lifetime
         的跳变误计入 30d,远超真实增量;逐日 delta 才诚实。)
      - engagement_rate:每账号最新一条快照 SUM(total_likes + total_comments) / SUM(total_views),百分比
        (与全量 evidence ER 同单位:百分数,前端直接 toFixed 显示)。
    输出键:exposure_30d(int)/ engagement_rate(float 百分数 | None)/ snapshot_days(int)/
            total_views_lifetime(int,清楚标注为 lifetime,仅作 fallback/审计,默认不展示)。
    """
    conn = get_conn()
    exposure_row = conn.execute(
        f"""
        WITH official AS (
            SELECT c.id FROM vkpi_employee_channels c WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        ),
        latest AS (
            SELECT MAX(m.snapshot_date) AS latest_date
            FROM vkpi_channel_metrics m
            JOIN official o ON o.id = m.channel_id
        )
        SELECT
            COALESCE(SUM(GREATEST(COALESCE(m.views_delta_24h, 0), 0)), 0) AS exposure_30d,
            COUNT(DISTINCT m.snapshot_date) AS snapshot_days
        FROM vkpi_channel_metrics m
        JOIN official o ON o.id = m.channel_id
        CROSS JOIN latest
        WHERE latest.latest_date IS NOT NULL
          AND m.snapshot_date > latest.latest_date - INTERVAL '30 days'
          AND m.snapshot_date <= latest.latest_date
        """
    ).fetchone()

    engagement_row = conn.execute(
        f"""
        WITH official AS (
            SELECT c.id FROM vkpi_employee_channels c WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        ),
        latest_snap AS (
            SELECT m.total_views, m.total_likes, m.total_comments
            FROM vkpi_channel_metrics m
            JOIN official o ON o.id = m.channel_id
            WHERE m.id = (
                SELECT mm.id FROM vkpi_channel_metrics mm
                WHERE mm.channel_id = m.channel_id
                ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
                LIMIT 1
            )
        )
        SELECT
            COALESCE(SUM(total_likes), 0) AS total_likes,
            COALESCE(SUM(total_comments), 0) AS total_comments,
            COALESCE(SUM(total_views), 0) AS total_views
        FROM latest_snap
        """
    ).fetchone()

    exposure_30d = _as_int(_row_value(exposure_row, "exposure_30d"))
    snapshot_days = _as_int(_row_value(exposure_row, "snapshot_days"))
    likes = _as_int(_row_value(engagement_row, "total_likes"))
    comments = _as_int(_row_value(engagement_row, "total_comments"))
    views_lifetime = _as_int(_row_value(engagement_row, "total_views"))
    engagement_rate = ((likes + comments) / views_lifetime * 100) if views_lifetime > 0 else None
    return {
        "exposure_30d": exposure_30d,
        "engagement_rate": engagement_rate,
        "snapshot_days": snapshot_days,
        "total_views_lifetime": views_lifetime,
    }
