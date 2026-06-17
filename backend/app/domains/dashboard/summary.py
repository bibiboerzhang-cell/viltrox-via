"""Dashboard summary assembly use cases."""
from __future__ import annotations

from typing import Any

from app.domains.dashboard.metric_maturity import (
    _OFFICIAL_CHANNEL_FILTER_SQL,
    dashboard_metric_maturity_contract,
    dashboard_window_metrics_contract,
    normalize_dashboard_scope,
)
from app.domains.channels.official import _account_group
from app.domains.dashboard.account_picker import build_dashboard_kpi
from app.db.connection import get_conn, table_exists
from app.domains.dashboard.recent_content import _dashboard_official_matrix_summary
from app.domains import lineage as metric_lineage
from app.domains.dashboard import decision_dashboard as decision_engine
from app.domains.access import scope
from app.domains.projects import stage_canonical
from app.domains.projects.workflow import staff_id as resolve_staff_id


# --- KOL 榜单 Viltrox 过滤口径(本会话 2026-06-16)-------------------------------
# 「KOL 榜单」只收 KOL 发的 Viltrox 相关视频。判定优先用结构化深析信号
# (vkpi_kol_llm_deep_analysis_results.llm_dimensions_11,Gemini final_v1,layer1_summary
# 含 brand_exposure / product_presence / content_summary);无深析的视频用关键词兜底
# (口径同 app/core/constants.VILTROX_BRAND_KEYWORDS / app/services/audit/similarity 的 is_viltrox)。
#
# 关键坑(已取证):深析里 463/468 的 layer1 字段是「字符串」而非结构化对象,且常出现否定句
# ——例如 "None. No Viltrox logos..."、"无 Viltrox 露出"、"完全没有 Viltrox 品牌露出"。
# 纯子串匹配 "viltrox" 会把这些非 Viltrox 视频(Rick Astley/Sony/Fujifilm 评测等)误收。
# 因此深析判定 = 字段提到 Viltrox 关键词 且 该字段不是否定句(NOT match 否定正则)。
# 取证口径(2026-06-16 线上库):全 evidence 2631;468 有 ready video_final_v1;裸子串 455「命中」
# 里含 ~30 条否定句(如 Rick Astley/Fujifilm/Sony/Hasselblad 评测被误收);否定感知后 ~425 条真 Viltrox。
# 无深析 2163 条里仅 305 条 title 关键词命中(故绝不能纯关键词,会漏掉大量真 Viltrox 视频)。
#
# SQL 文本直接拼字面量(全是受控常量,无外部输入);% 需写成 %% —— get_conn().execute() 不传 params
# 时 psycopg 仍把裸 % 当占位符(只允许 %s/%b/%t)。正则用 Postgres ~*(大小写不敏感)。
_VILTROX_KEYWORDS_SQL = (
    "viltrox",       # 英文品牌
    "唯卓仕",        # 中文品牌
    "nexusfocus",    # 子品牌
    "nexus focus f1",
)
# 品牌关键词正则(Postgres ~*,大小写不敏感)。
_VILTROX_KW_REGEX = "viltrox|唯卓仕|nexusfocus"
# 否定「占位」正则:把「明确说没有 Viltrox」的片段从文本里抹掉,再看是否还有 Viltrox 关键词存活。
# 这样能同时处理:① 否定词在前(no/none/zero/未提及/无/没有/未见 ... viltrox);
# ② 否定在后(viltrox ... 为 0 / 0% / 零 / 无露出 / neither / not mentioned/visible)。
# 抹掉后仍有关键词存活 = 这条视频真有 Viltrox 的肯定提及。比纯子串稳:Rick Astley「None. No Viltrox…」
# / Fujifilm「Viltrox is neither mentioned nor visible」/「无 Viltrox 露出」等会被正确排除。
_VILTROX_NEG_OCCURRENCE_REGEX = (
    "(no|none|zero|not|neither|without|未提及|未见|未出现|没有|无|缺位|未|零)"
    "[^。.!?；;\\n]{0,14}(viltrox|唯卓仕)"
    "|(viltrox|唯卓仕)[^。.!?；;\\n]{0,14}"
    # 注意:正则里出现的字面 % 必须写成 %%(execute 把裸 % 当 psycopg 占位符)。
    "(为 0|0 %%|0%%|零|无露出|缺位|neither|not mentioned|not visible|not present|not featured)"
)


def _viltrox_text_like(column_expr: str) -> str:
    """SQL bool expr: TRUE when column_expr text mentions any Viltrox brand keyword (keyword-only)."""
    clauses = [
        f"lower({column_expr}) LIKE '%%{kw}%%'" if kw.isascii() else f"{column_expr} LIKE '%%{kw}%%'"
        for kw in _VILTROX_KEYWORDS_SQL
    ]
    return "(" + " OR ".join(clauses) + ")"


# 深析信号:把 layer1 的 brand_exposure / product_presence / content_summary 三段拼起来,
# 抹掉「明确否定 Viltrox」的片段后仍有品牌关键词存活,即判定该视频肯定提及 Viltrox。
# 字段缺失用 '' 兜底避免 NULL 短路;拼接用 ' || ' 隔开防止跨字段误连成否定窗口。
_DEEP_COMBINED = (
    "("
    "COALESCE(d.llm_dimensions_11->'layer1_summary'->>'brand_exposure','')||' || '||"
    "COALESCE(d.llm_dimensions_11->'layer1_summary'->>'product_presence','')||' || '||"
    "COALESCE(d.llm_dimensions_11->'layer1_summary'->>'content_summary','')"
    ")"
)
_VILTROX_DEEP_SIGNAL = (
    f"(regexp_replace({_DEEP_COMBINED}, '{_VILTROX_NEG_OCCURRENCE_REGEX}', ' ', 'gi') ~* '{_VILTROX_KW_REGEX}')"
)

# 关键词兜底(只对无深析的视频用):标题/视频标题/URL 命中品牌词。
_VILTROX_KW_FALLBACK = _viltrox_text_like(
    "(COALESCE(e.title,'')||' '||COALESCE(e.video_title,'')||' '||COALESCE(e.content_url,''))"
)

# 一条 evidence(别名 e)算 Viltrox 相关 = 深析肯定提到 Viltrox,或(无深析时)关键词兜底命中。
# 有深析但不提 / 明确否定 Viltrox 的视频会被排除(非 Viltrox 内容不进榜),这正是本次目的。
_VILTROX_EVIDENCE_PREDICATE = f"""(
    EXISTS (
        SELECT 1 FROM vkpi_kol_llm_deep_analysis_results d
        WHERE d.source_evidence_id = e.id
          AND d.analysis_kind = 'video_final_v1'
          AND d.status = 'ready'
          AND {_VILTROX_DEEP_SIGNAL}
    )
    OR (
        NOT EXISTS (
            SELECT 1 FROM vkpi_kol_llm_deep_analysis_results d
            WHERE d.source_evidence_id = e.id
              AND d.analysis_kind = 'video_final_v1'
              AND d.status = 'ready'
        )
        AND {_VILTROX_KW_FALLBACK}
    )
)"""


def _viltrox_evidence_predicate() -> str:
    """Viltrox-only filter on alias `e` (vkpi_kol_video_evidence).

    Degrades gracefully: if the deep-analysis table is missing, fall back to keyword-only on title/url.
    复用提示:MY KOL 簇要做同样过滤时,直接拼接本 predicate(要求 SQL 里有别名 e 指向
    vkpi_kol_video_evidence;或调 _viltrox_field_positive / _VILTROX_DEEP_SIGNAL 复用深析口径)。
    """
    if not table_exists("vkpi_kol_llm_deep_analysis_results"):
        return _VILTROX_KW_FALLBACK
    return _VILTROX_EVIDENCE_PREDICATE


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: _row_value(row, key) for key in row.keys()}
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    try:
        return dict(row)
    except Exception:
        return {}


def _fetch_dicts(sql: str) -> list[dict[str, Any]]:
    return [_row_dict(row) for row in get_conn().execute(sql).fetchall()]


def _metric_item(row: dict[str, Any], metric_key: str) -> dict[str, Any]:
    return {
        "kol_id": _as_int(row.get("kol_id")),
        "kol_name": row.get("kol_name"),
        "handle": row.get("handle"),
        "profile_url": row.get("profile_url"),
        "platform": row.get("platform"),
        "title": row.get("title"),
        "url": row.get("url"),
        "value": _as_int(row.get(metric_key)),
        "view_count": _as_int(row.get("view_count")),
        "like_count": _as_int(row.get("like_count")),
        "comment_count": _as_int(row.get("comment_count")),
        "publish_date": row.get("publish_date"),
    }


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


def _build_roster_detail(active_roster_by_scope: dict[str, int]) -> dict[str, Any]:
    total_pool_row = get_conn().execute("SELECT COUNT(*) AS count FROM vkpi_kol_pool").fetchone()
    total_pool = _as_int(_row_value(total_pool_row, "count"))

    platform_rows = _fetch_dicts(
        """
        SELECT COALESCE(NULLIF(platform, ''), 'unknown') AS platform, COUNT(*) AS count
        FROM vkpi_kol_pool
        WHERE has_video_evidence = TRUE
        GROUP BY COALESCE(NULLIF(platform, ''), 'unknown')
        ORDER BY COUNT(*) DESC, platform
        """
    )
    active_pool = sum(_as_int(row.get("count")) for row in platform_rows) or 1
    by_platform = [
        {
            "platform": row.get("platform"),
            "count": _as_int(row.get("count")),
            "pct": _as_int(row.get("count")) / active_pool,
        }
        for row in platform_rows
    ]

    bucket_rows = _fetch_dicts(
        """
        WITH per_kol AS (
          SELECT
            a.kol_pool_id,
            COUNT(DISTINCT a.project_id) AS project_count,
            COUNT(*) FILTER (WHERE a.stage IN ('content_posted','reviewed')) AS published_rows,
            COUNT(*) FILTER (WHERE a.stage = 'device_sent') AS device_rows,
            -- C9 顺手修(2026-06-12):DB 存在 discovery/discovered 双拼写,pending 桶补上 discovery(23 行)。
            COUNT(*) FILTER (WHERE a.stage IN ('discovery','discovered','contacted','replied','agreed')) AS pending_rows,
            COUNT(*) FILTER (WHERE a.stage = 'churned') AS churned_rows,
            COALESCE(p.video_evidence_count, 0) AS video_evidence_count
          FROM vkpi_project_kol_assignments a
          JOIN vkpi_kol_pool p ON p.id = a.kol_pool_id
          GROUP BY a.kol_pool_id, p.video_evidence_count
        ), buckets AS (
          SELECT CASE
            WHEN project_count > 1 OR (published_rows > 0 AND video_evidence_count > 1) THEN 'long_term'
            WHEN published_rows > 0 THEN 'one_off'
            WHEN device_rows > 0 THEN 'in_production'
            WHEN pending_rows > 0 THEN 'pending'
            WHEN churned_rows > 0 THEN 'churned'
            ELSE 'pending'
          END AS bucket
          FROM per_kol
        )
        SELECT bucket, COUNT(*) AS count
        FROM buckets
        GROUP BY bucket
        """
    )
    bucket_counts = {row.get("bucket"): _as_int(row.get("count")) for row in bucket_rows}
    partnership_total = sum(bucket_counts.values()) or 1
    partnership_4tier = {
        "long_term": bucket_counts.get("long_term", 0),
        "in_production": bucket_counts.get("in_production", 0),
        "one_off": bucket_counts.get("one_off", 0),
        "pending": bucket_counts.get("pending", 0),
        "churned": bucket_counts.get("churned", 0),
    }
    partnership_4tier_pct = {
        key: value / partnership_total
        for key, value in partnership_4tier.items()
    }

    viltrox_evidence = _viltrox_evidence_predicate()
    views_rows = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.view_count IS NOT NULL
          AND {viltrox_evidence}
        ORDER BY e.view_count DESC NULLS LAST
        LIMIT 10
        """
    )
    # by_activity:按「Viltrox 相关视频数」排名(不用 pool.video_evidence_count,因为它含非 Viltrox 视频)。
    activity_rows = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COUNT(*) AS value
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE {viltrox_evidence}
        GROUP BY p.id, p.display_name, p.handle, p.profile_url, p.platform
        ORDER BY COUNT(*) DESC, p.display_name
        LIMIT 10
        """
    )
    recent_rows = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.view_count IS NOT NULL
          AND e.publish_date >= NOW() - INTERVAL '30 days'
          AND {viltrox_evidence}
        ORDER BY e.view_count DESC NULLS LAST
        LIMIT 10
        """
    )
    engagement_rows = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date,
               COALESCE(e.like_count, 0) + COALESCE(e.comment_count, 0) AS engagement_count
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE (e.like_count IS NOT NULL OR e.comment_count IS NOT NULL)
          AND {viltrox_evidence}
        ORDER BY COALESCE(e.like_count, 0) + COALESCE(e.comment_count, 0) DESC
        LIMIT 10
        """
    )

    company_trend_rows = _fetch_dicts(
        """
        SELECT snapshot_date::text AS date,
               COUNT(DISTINCT channel_id) AS account_count,
               COALESCE(SUM(total_views), 0) AS total_views,
               COALESCE(SUM(views_delta_24h), 0) AS views_delta_24h
        FROM vkpi_channel_metrics
        WHERE snapshot_date >= (SELECT MAX(snapshot_date) FROM vkpi_channel_metrics) - INTERVAL '6 days'
        GROUP BY snapshot_date
        ORDER BY snapshot_date
        """
    )
    company_trend = [
        {
            "date": row.get("date"),
            "account_count": _as_int(row.get("account_count")),
            "total_views": _as_int(row.get("total_views")),
            "views_delta_24h": _as_int(row.get("views_delta_24h")),
        }
        for row in company_trend_rows
    ]

    composition = {
        "signed": partnership_4tier["long_term"] + partnership_4tier["in_production"] + partnership_4tier["one_off"],
        "pending": partnership_4tier["pending"],
        "churned": partnership_4tier["churned"],
    }

    return {
        "total_pool": total_pool,
        "active_roster": active_roster_by_scope.get("all", 0),
        "company": _build_company_roster_detail(),
        "composition": composition,
        "partnership_4tier": partnership_4tier,
        "partnership_4tier_pct": partnership_4tier_pct,
        "by_platform": by_platform,
        "movers_tabs": {
            "by_views": [_metric_item(row, "view_count") for row in views_rows],
            "by_activity": [
                {
                    "kol_id": _as_int(row.get("kol_id")),
                    "kol_name": row.get("kol_name"),
                    "handle": row.get("handle"),
                    "profile_url": row.get("profile_url"),
                    "platform": row.get("platform"),
                    "value": _as_int(row.get("value")),
                }
                for row in activity_rows
            ],
            "by_recent": [_metric_item(row, "view_count") for row in recent_rows],
            "by_engagement": [_metric_item(row, "engagement_count") for row in engagement_rows],
        },
        "trend": {
            "all": {"status": "accumulating", "snapshot_days": 7, "required_days": 30},
            "kol": {"status": "accumulating", "snapshot_days": 7, "required_days": 30},
            "company": {"status": "real", "window_days": 7, "points": company_trend},
        },
    }


def _build_evidence_active_30d_summary(*, window_days: int = 30, staff_scope_id: int | None = None) -> dict[str, Any]:
    days = max(1, min(int(window_days or 30), 90))
    # P1 隔离:非 owner/admin 只算本人 KOL 的活跃;company(官方矩阵)是公共资产,保持全局。
    kol_scope = f"AND e.kol_pool_id IN ({_actor_kols_sql(staff_scope_id)})" if staff_scope_id else ""
    external_rows = _fetch_dicts(
        f"""
        SELECT
            COALESCE(p.dashboard_account_type, 'kol') AS account_type,
            COUNT(DISTINCT p.id) AS active_accounts,
            COUNT(*) AS evidence_count
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE COALESCE(e.is_active, TRUE) = TRUE
          AND COALESCE(e.evidence_type, 'video') = 'video'
          AND e.publish_date IS NOT NULL
          AND e.publish_date >= NOW() - INTERVAL '{days} days'
          {kol_scope}
        GROUP BY COALESCE(p.dashboard_account_type, 'kol')
        """
    )
    external_counts = {"kol": 0, "media": 0}
    external_evidence = {"kol": 0, "media": 0}
    for row in external_rows:
        account_type = str(row.get("account_type") or "kol")
        if account_type in external_counts:
            external_counts[account_type] = _as_int(row.get("active_accounts"))
            external_evidence[account_type] = _as_int(row.get("evidence_count"))

    official_row = get_conn().execute(
        f"""
        SELECT
            COUNT(DISTINCT channel_id) FILTER (
                WHERE posted_at::date >= CURRENT_DATE - INTERVAL '{days} days'
                   OR COALESCE(views_delta, 0) > 0
                   OR COALESCE(likes_delta, 0) > 0
                   OR COALESCE(comments_delta, 0) > 0
            ) AS active_accounts,
            COUNT(*) FILTER (
                WHERE posted_at::date >= CURRENT_DATE - INTERVAL '{days} days'
                   OR COALESCE(views_delta, 0) > 0
                   OR COALESCE(likes_delta, 0) > 0
                   OR COALESCE(comments_delta, 0) > 0
            ) AS signal_rows,
            COUNT(DISTINCT snapshot_date) AS snapshot_days
        FROM vkpi_channel_post_metrics pm
        JOIN vkpi_employee_channels c ON c.id = pm.channel_id
        WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        """
    ).fetchone()
    company_active = _as_int(_row_value(official_row, "active_accounts"))
    all_active = external_counts["kol"] + external_counts["media"] + company_active
    return {
        "all": all_active,
        "kol": external_counts["kol"],
        "media": external_counts["media"],
        "company": company_active,
        "owned": company_active,
        "window_days": days,
        "basis": {
            "kol_media": "vkpi_kol_video_evidence.publish_date within window",
            "company": "vkpi_channel_post_metrics posted_at or positive deltas within window",
        },
        "evidence_count_by_scope": external_evidence,
        "company_signal_rows": _as_int(_row_value(official_row, "signal_rows")),
        "company_snapshot_days": _as_int(_row_value(official_row, "snapshot_days")),
    }


def _build_evidence_metrics_summary(*, window_days: int = 30, staff_scope_id: int | None = None) -> dict[str, Any]:
    active_roster_by_scope = {
        account_type: _as_int(build_dashboard_kpi(account_type=account_type, staff_scope_id=staff_scope_id).get("active_roster"))
        for account_type in ("all", "kol", "media", "company")
    }
    # P1 隔离:非 owner/admin 的曝光/覆盖只算本人 KOL 的证据。
    evidence_where = f"WHERE kol_pool_id IN ({_actor_kols_sql(staff_scope_id)})" if staff_scope_id else ""
    row = get_conn().execute(
        f"""
        SELECT
            COUNT(*) AS evidence_total,
            COUNT(*) FILTER (WHERE view_count IS NOT NULL) AS view_covered,
            COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views,
            COALESCE(SUM(
                CASE
                    WHEN view_count IS NOT NULL THEN COALESCE(like_count, 0) + COALESCE(comment_count, 0)
                    ELSE 0
                END
            ), 0) AS total_engagement,
            MAX(metrics_scraped_at) AS last_refreshed_at
        FROM vkpi_kol_video_evidence
        {evidence_where}
        """
    ).fetchone()
    evidence_total = _as_int(_row_value(row, "evidence_total"))
    view_covered = _as_int(_row_value(row, "view_covered"))
    total_views = _as_int(_row_value(row, "total_views"))
    total_engagement = _as_int(_row_value(row, "total_engagement"))
    engagement_rate = (total_engagement / total_views) if total_views > 0 else None
    view_coverage_pct = (view_covered / evidence_total) if evidence_total > 0 else 0.0
    return {
        "active_roster_by_scope": active_roster_by_scope,
        "active_30d_by_scope": _build_evidence_active_30d_summary(window_days=window_days, staff_scope_id=staff_scope_id),
        "total_exposure": total_views,
        "engagement": {
            "total_engagement": total_engagement,
            "total_views": total_views,
            "engagement_rate": engagement_rate,
        },
        "coverage": {
            "evidence_total": evidence_total,
            "view_covered": view_covered,
            "view_coverage_pct": view_coverage_pct,
            "last_refreshed_at": _row_value(row, "last_refreshed_at"),
        },
        "roster_detail": _build_roster_detail(active_roster_by_scope),
    }


# 四环漏斗在役口径(与 pool_favorites.list_favorites 的排除集对齐)
_FUNNEL_EXCLUDED_STAGES_SQL = "('churned','cancelled','lost')"
# 已发布口径(波3 R1 裁定:content_posted/published)
# P14:已发布/执行阶段的 raw 别名统一从 stage_canonical 取,杜绝同一项目在 funnel 与
# active_campaigns 算法不同(此前 funnel=('content_posted','published')、active只认 content_posted、
# canonical 还有 content_published)。现 published=(content_posted,content_published,published)。
_FUNNEL_PUBLISHED_STAGES_SQL = stage_canonical.raw_sql_tuple("content_published")
_EXECUTION_STAGES_SQL = stage_canonical.raw_sql_tuple("shipped", "delivered", "content_published")


def _actor_projects_sql(staff_scope_id: int) -> str:
    """P1 隔离:某 staff「拥有」的项目 id 子查询(assigned / created / 被共享成员)。
    staff_scope_id 已是 scope.effective_staff_id 出来的可信 int(admin→None 不会进这里),
    内联整数安全(非注入面),与本文件既有 {days} f-string 口径一致。"""
    sid = int(staff_scope_id)
    return (
        f"SELECT id FROM vkpi_projects WHERE assigned_staff_id={sid} "
        f"OR created_by_staff_id={sid} "
        f"OR id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id={sid})"
    )


def _actor_kols_sql(staff_scope_id: int) -> str:
    """P1 隔离:某 staff「拥有」的 KOL(kol_pool_id)= 本人关注(收藏)∪ 本人项目里合作的 KOL。
    公司/官方账号属公共资产,不走此过滤(各聚合里 account_type<>'kol' / company 维度保持全局)。"""
    sid = int(staff_scope_id)
    return (
        f"SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE staff_id={sid} "
        f"UNION SELECT a.kol_pool_id FROM vkpi_project_kol_assignments a "
        f"WHERE a.project_id IN ({_actor_projects_sql(sid)})"
    )


def _build_funnel_summary(staff_scope_id: int | None = None) -> dict[str, Any]:
    """四环漏斗聚合(波3 R1 / 施工卡 C9,2026-06-12)。全部真 SQL 聚合,零估算零硬编码。

    输出 shape(前端 v615 normalizers.ts 消费契约——键名勿改,前端按此解析):

    summary.funnel = {
        "favorites_total":  int,        # 环1 收藏:vkpi_kol_pool_favorites 总对数(staff×kol 对;体检日口径 772)
        "claimed_total":    int | None, # 环2 认领:vkpi_kol_claims status='active' 的 distinct kol_id。
                                        #   注意:claims 键在 kols.id(在役主表),与 kol_pool id 非同一维度,
                                        #   只能作为环序数读、不能与环1/环3 做 id 级联;表缺失时为 None。
        "in_project_total": int,        # 环3 进项目:vkpi_project_kol_assignments 在役
                                        #   (stage NOT IN churned/cancelled/lost)的 distinct kol_pool_id
        "published_total":  int,        # 环4 已发布:assignments stage IN (content_posted, published)
                                        #   的 distinct kol_pool_id
        "by_staff": [                   # 按 staff 分布,以收藏表为锚,favorites DESC 排序:
            {
                "staff_id":   int,         # vkpi_kol_pool_favorites.staff_id(真 staff.id)
                "name":       str | None,  # staff -> users.name(查不到时 null,前端显示 "Staff {id}")
                "favorites":  int,         # 该 staff 的收藏对数
                "in_project": int,         # 该 staff 收藏的 KOL 中已进项目(在役 assignment 存在)的 distinct kol 数
            },
        ],
        "favorites_kol_total": int,     # 辅助口径:收藏覆盖的 distinct KOL 数(体检日口径 715)
        "basis": {...},                 # 各环口径自述(诊断/审计用,前端可忽略)
    }
    """
    conn = get_conn()
    # P1 隔离:非 owner/admin 只看本人收藏 + 本人项目里的 KOL;owner/admin(staff_scope_id=None)看全局。
    fav_where = f"WHERE staff_id={int(staff_scope_id)}" if staff_scope_id else ""
    fav_where_f = f"WHERE f.staff_id={int(staff_scope_id)}" if staff_scope_id else ""  # by_staff 多表 join 需限定别名
    assign_where = f"WHERE project_id IN ({_actor_projects_sql(staff_scope_id)})" if staff_scope_id else ""

    favorites_row = conn.execute(
        f"""
        SELECT COUNT(*) AS favorites_total,
               COUNT(DISTINCT kol_pool_id) AS favorites_kol_total
        FROM vkpi_kol_pool_favorites
        {fav_where}
        """
    ).fetchone()
    favorites_total = _as_int(_row_value(favorites_row, "favorites_total"))
    favorites_kol_total = _as_int(_row_value(favorites_row, "favorites_kol_total"))

    claimed_total: int | None = None
    if table_exists("vkpi_kol_claims"):
        claimed_row = conn.execute(
            "SELECT COUNT(DISTINCT kol_id) AS claimed_total FROM vkpi_kol_claims WHERE status = 'active'"
        ).fetchone()
        claimed_total = _as_int(_row_value(claimed_row, "claimed_total"))

    assignment_row = conn.execute(
        f"""
        SELECT
            COUNT(DISTINCT kol_pool_id) FILTER (
                WHERE COALESCE(stage, '') NOT IN {_FUNNEL_EXCLUDED_STAGES_SQL}
            ) AS in_project_total,
            COUNT(DISTINCT kol_pool_id) FILTER (
                WHERE stage IN {_FUNNEL_PUBLISHED_STAGES_SQL}
            ) AS published_total
        FROM vkpi_project_kol_assignments
        {assign_where}
        """
    ).fetchone()

    by_staff_rows = _fetch_dicts(
        f"""
        SELECT f.staff_id,
               MAX(u.name) AS name,
               COUNT(DISTINCT f.id) AS favorites,
               COUNT(DISTINCT a.kol_pool_id) AS in_project
        FROM vkpi_kol_pool_favorites f
        LEFT JOIN staff st ON st.id = f.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        LEFT JOIN vkpi_project_kol_assignments a
               ON a.kol_pool_id = f.kol_pool_id
              AND COALESCE(a.stage, '') NOT IN {_FUNNEL_EXCLUDED_STAGES_SQL}
        {fav_where_f}
        GROUP BY f.staff_id
        ORDER BY COUNT(DISTINCT f.id) DESC, f.staff_id
        """
    )
    by_staff = [
        {
            "staff_id": _as_int(row.get("staff_id")),
            "name": row.get("name"),
            "favorites": _as_int(row.get("favorites")),
            "in_project": _as_int(row.get("in_project")),
        }
        for row in by_staff_rows
    ]

    return {
        "favorites_total": favorites_total,
        "claimed_total": claimed_total,
        "in_project_total": _as_int(_row_value(assignment_row, "in_project_total")),
        "published_total": _as_int(_row_value(assignment_row, "published_total")),
        "by_staff": by_staff,
        "favorites_kol_total": favorites_kol_total,
        "basis": {
            "favorites": "COUNT(*) FROM vkpi_kol_pool_favorites (staff×kol 对)",
            "claimed": "COUNT(DISTINCT kol_id) FROM vkpi_kol_claims WHERE status='active'(kols.id 维度;表缺失时 null)",
            "in_project": f"COUNT(DISTINCT kol_pool_id) FROM vkpi_project_kol_assignments WHERE stage NOT IN {_FUNNEL_EXCLUDED_STAGES_SQL}",
            "published": f"COUNT(DISTINCT kol_pool_id) FROM vkpi_project_kol_assignments WHERE stage IN {_FUNNEL_PUBLISHED_STAGES_SQL}",
            "by_staff": "以收藏表为锚:favorites=本人收藏对数;in_project=本人收藏 KOL 中存在在役 assignment 的 distinct kol 数",
        },
    }


def _build_active_campaigns_summary(*, window_days: int = 30, staff_scope_id: int | None = None) -> dict[str, Any]:
    days = max(1, min(int(window_days or 30), 365))
    # P1 隔离:非 owner/admin 只看本人(assigned/created/被共享)项目;owner/admin 看全部。
    project_scope = (
        f"AND (p.assigned_staff_id={int(staff_scope_id)} OR p.created_by_staff_id={int(staff_scope_id)} "
        f"OR p.id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id={int(staff_scope_id)}))"
        if staff_scope_id else ""
    )
    rows = _fetch_dicts(
        f"""
        WITH assignment_stats AS (
          SELECT
            project_id,
            COUNT(DISTINCT kol_pool_id) AS kol_count,
            COUNT(DISTINCT kol_pool_id) FILTER (
              WHERE stage IN {_EXECUTION_STAGES_SQL}
            ) AS execution_kol_count,
            COUNT(DISTINCT kol_pool_id) FILTER (
              WHERE stage IN {_FUNNEL_PUBLISHED_STAGES_SQL}
            ) AS published_kol_count,
            BOOL_OR(stage IN {_EXECUTION_STAGES_SQL}) AS has_execution_stage
          FROM vkpi_project_kol_assignments
          GROUP BY project_id
        ),
        evidence_stats AS (
          SELECT
            project_id,
            COUNT(*) AS evidence_count,
            COUNT(*) FILTER (
              WHERE created_at >= NOW() - INTERVAL '{days} days'
            ) AS recent_evidence_count,
            COALESCE(SUM(view_count), 0) AS total_views,
            COALESCE(SUM(view_count) FILTER (
              WHERE created_at >= NOW() - INTERVAL '{days} days'
            ), 0) AS recent_views,
            BOOL_OR(created_at >= NOW() - INTERVAL '{days} days') AS has_recent_evidence
          FROM vkpi_kol_video_evidence
          WHERE project_id IS NOT NULL
            AND COALESCE(is_active, TRUE) = TRUE
            AND COALESCE(evidence_type, 'video') = 'video'
          GROUP BY project_id
        ),
        active_projects AS (
          SELECT
            p.id,
            p.project_uid,
            p.project_name,
            p.product_sku,
            p.product_name,
            p.stage,
            p.stage_status,
            p.source_type,
            p.updated_at,
            COALESCE(a.kol_count, 0) AS kol_count,
            COALESCE(a.execution_kol_count, 0) AS execution_kol_count,
            COALESCE(a.published_kol_count, 0) AS published_kol_count,
            COALESCE(e.evidence_count, 0) AS evidence_count,
            COALESCE(e.recent_evidence_count, 0) AS recent_evidence_count,
            COALESCE(e.total_views, 0) AS total_views,
            COALESCE(e.recent_views, 0) AS recent_views,
            COALESCE(a.has_execution_stage, FALSE) AS has_execution_stage,
            COALESCE(e.has_recent_evidence, FALSE) AS has_recent_evidence
          FROM vkpi_projects p
          LEFT JOIN assignment_stats a ON a.project_id = p.id
          LEFT JOIN evidence_stats e ON e.project_id = p.id
          WHERE COALESCE(p.stage, '') NOT IN (
              'closed', 'churned', 'cancelled', 'canceled', 'done', 'deleted',
              '已关闭', '已取消', '已中止', '合作中止'
            )
            AND COALESCE(p.stage_status, '') NOT IN (
              'closed', 'churned', 'cancelled', 'canceled', 'done', 'deleted',
              '已关闭', '已取消', '已中止', '合作中止'
            )
            AND COALESCE(p.source_type, '') <> 'codex_test'
            {project_scope}
        )
        SELECT *
        FROM active_projects
        WHERE has_execution_stage = TRUE OR has_recent_evidence = TRUE
        ORDER BY recent_evidence_count DESC, execution_kol_count DESC, updated_at DESC NULLS LAST, id DESC
        """
    )

    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:8]):
        recent_evidence_count = _as_int(row.get("recent_evidence_count"))
        execution_kol_count = _as_int(row.get("execution_kol_count"))
        signals: list[str] = []
        if execution_kol_count:
            signals.append(f"实操阶段 {execution_kol_count} KOL")
        if recent_evidence_count:
            signals.append(f"近 {days} 天出片 {recent_evidence_count}")
        items.append(
            {
                "id": _as_int(row.get("id")),
                "project_uid": row.get("project_uid"),
                "name": row.get("project_name"),
                "product": row.get("product_name") or row.get("product_sku"),
                "status": "active",
                "status_label": "进行中",
                "icon_key": "camera",
                "icon_color": ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"][index % 6],
                "kol_count": _as_int(row.get("kol_count")),
                "execution_kol_count": execution_kol_count,
                "published_count": _as_int(row.get("published_kol_count")),
                "recent_video_count": recent_evidence_count,
                "evidence_count": _as_int(row.get("evidence_count")),
                "total_views": _as_int(row.get("total_views")),
                "recent_views": _as_int(row.get("recent_views")),
                "active_signals": signals,
                "bottleneck_text": " · ".join(signals) or "符合当前 active campaign 口径",
            }
        )

    return {
        "active_count": len(rows),
        "items": items,
        "window_days": days,
        "criteria": {
            "project_status": "vkpi_projects.stage/stage_status not in closed/churned/cancelled/done/deleted/已关闭/已取消/已中止/合作中止",
            "signals": [
                "assignment stage in device_sent/received/content_posted",
                f"video evidence created in the last {days} days",
            ],
            "excluded_source_types": ["codex_test"],
        },
    }


def build_dashboard_summary(
    *,
    window_days: int = 30,
    staff_id: int | None = None,
    metric_scope: str = "all",
    staff: dict[str, Any],
) -> dict[str, Any]:
    normalized_scope = normalize_dashboard_scope(metric_scope)
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
    maturity_contract = dashboard_metric_maturity_contract()
    scope_maturity = maturity_contract["scopes"][normalized_scope]
    result["metric_contract"] = maturity_contract
    result["metric_maturity"] = scope_maturity
    result["metric_maturity_by_scope"] = maturity_contract["scopes"]
    window_metrics = dashboard_window_metrics_contract(maturity_contract)
    official_summary = _dashboard_official_matrix_summary(limit=20)
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    # P1 隔离:effective_staff_id 为 None=owner/admin(全局),否则=该员工(own-only)。
    # 四聚合统一吃这把 scope —— 此前它们各自全局口径,导致小号也看到全公司数据。
    summary["active_roster"] = int(build_dashboard_kpi(account_type="all", staff_scope_id=effective_staff_id).get("active_roster") or 0)
    summary["evidence_metrics"] = _build_evidence_metrics_summary(window_days=window_days, staff_scope_id=effective_staff_id)
    summary["active_campaigns"] = _build_active_campaigns_summary(window_days=window_days, staff_scope_id=effective_staff_id)
    # 波3 R1 / C9(2026-06-12):四环漏斗块(收藏→认领→进项目→已发布),shape 契约见 _build_funnel_summary docstring。
    summary["funnel"] = _build_funnel_summary(staff_scope_id=effective_staff_id)
    summary["metric_scope"] = normalized_scope
    summary["scope_label"] = scope_maturity["scope_label"]
    summary["snapshot_days"] = scope_maturity["snapshot_days"]
    summary["required_days"] = scope_maturity["required_days"]
    summary["maturity_label"] = scope_maturity["maturity_label"]
    summary["exposure_30d_by_scope"] = window_metrics["exposure_30d_by_scope"]
    summary["engagement_rate_by_scope"] = window_metrics["engagement_rate_by_scope"]
    summary["active_30d_by_scope"] = window_metrics["active_30d_by_scope"]
    # 公司账号(官方矩阵)真实 30d 窗口指标——additive 覆盖 company/owned 键。
    # post-level snapshot 未满 30d 前 window_metrics.owned 为 null(走「累积中」),
    # 但 channel-level vkpi_channel_metrics 已有 ~30d 真实快照,可诚实算出官方 30d 曝光增量与互动率。
    # 仅写 company/owned,绝不动 all/kol(KOL 窗口仍未就绪)。
    company_window = _build_company_window_metrics()
    company_exposure_30d = company_window.get("exposure_30d")
    company_engagement_rate = company_window.get("engagement_rate")
    if company_exposure_30d is not None:
        summary["exposure_30d_by_scope"]["company"] = company_exposure_30d
        summary["exposure_30d_by_scope"]["owned"] = company_exposure_30d
    if company_engagement_rate is not None:
        summary["engagement_rate_by_scope"]["company"] = company_engagement_rate
        summary["engagement_rate_by_scope"]["owned"] = company_engagement_rate
    summary["company_window_metrics"] = company_window
    if official_summary:
        result["official_matrix_summary"] = official_summary
        summary["official_account_count"] = official_summary["account_count"]
        summary["official_post_count"] = official_summary["post_count"]
        summary["official_total_views"] = official_summary["total_views"]
        summary["official_total_views_is_lifetime"] = True
        summary["official_total_views_30d"] = None
    result["summary"] = summary
    return result
