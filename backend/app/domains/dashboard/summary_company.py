"""Dashboard summary company/official-matrix aggregators (moved from summary.py, behavior unchanged)."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.dashboard.metric_maturity import _OFFICIAL_CHANNEL_FILTER_SQL
from app.domains.channels.official import _account_group
from app.domains.dashboard.summary_rows import _as_int, _fetch_dicts, _row_value


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
