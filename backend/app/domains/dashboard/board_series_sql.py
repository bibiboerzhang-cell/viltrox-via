"""板块 KPI 按日时序(board_series)· SQL 常量与护栏(600 行红线伴随文件)。

board_series.py 的常量拆分文件:护栏常量 + 全部 SQL 常量。逐板真实表名:
  projects   vkpi_projects / vkpi_project_stage_events / vkpi_project_content_posts /
             vkpi_sales_attributions
  events     vkpi_events / vkpi_event_expenses
  kol-profile vkpi_kol_video_evidence(kol_pool_id 过滤)
  autonomy   vkpi_action_inbox
  launchpad  vkpi_project_content_posts / vkpi_publish_approvals(迁移 173,探针)
  sku360     vkpi_products / vkpi_product_aliases / vkpi_kol_video_evidence
  creative   vkpi_analysis_cache(final_v1 ready)
  dealers    vkpi_dealers

compat 红线(与 voice_report_ext / my_kol_board_ext_sql 同款):
  - SQL 占位符全 ?,零拼接;SQL 字符串零字面 percent、零 LIKE、零注释
    (compat 把注释里的 ASCII 问号当占位符);
  - 时间列三族分治:timestamptz 列 CAST(? AS TIMESTAMPTZ) + AT TIME ZONE 'UTC' 取日;
    naive 列(vkpi_kol_video_evidence.created_at 库内约定 UTC)CAST(? AS TIMESTAMP)
    直接比较 + CAST(col AS DATE) 取日;date 列(vkpi_events.start_date)CAST(? AS DATE);
  - 扫描/日聚合 SQL 全部 LIMIT ? 下推(Python 层二次切片封顶);
  - 显示层宪法:SELECT 列白名单,零个人字段零明文联系方式
    (author_handle / contact_value / email / raw_data_json / payload_json 一列不进)。
红线:全只读;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

# ── 护栏常量(测试直接断言;SQL LIMIT ? + Python 切片双封顶)────────────────
SERIES_ROWS_LIMIT = 400        # 日聚合 GROUP BY 行封顶(日轴最长 366 天 + 余量)
SERIES_MAX_DAYS = 370          # 日轴长度 Python 层封顶
DAYS_MAX = 365                 # days 参数封顶
SKU_TITLE_SCAN_LIMIT = 6000    # sku360 标题扫描上限(本窗/上窗各一次)
SKU_ALIAS_ROWS_LIMIT = 200     # 别名行 SQL 层封顶(词表构建再经置信度/长度过滤)
CREATIVE_SCAN_LIMIT = 800      # creative 段级扫描上限(final_v1 ready 全库 ~500 条,留余量)
BOARD_SERIES_METHOD = "board_series_v1"

TABLE_PROBE_SQL = """
    SELECT table_name FROM information_schema.tables WHERE table_name = ? LIMIT 1
"""

# ── projects ───────────────────────────────────────────────────────────

PROJECTS_NEW_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_projects
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

PROJECTS_NEW_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_projects
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

STAGE_EVENTS_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_project_stage_events
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

STAGE_EVENTS_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_project_stage_events
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

CONTENT_POSTED_DAY_SQL = """
    SELECT CAST((published_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_project_content_posts
    WHERE published_at >= CAST(? AS TIMESTAMPTZ)
      AND published_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

CONTENT_POSTED_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_project_content_posts
    WHERE published_at >= CAST(? AS TIMESTAMPTZ)
      AND published_at < CAST(? AS TIMESTAMPTZ)
"""

ATTR_REVENUE_DAY_SQL = """
    SELECT CAST((occurred_at AT TIME ZONE 'UTC') AS DATE) AS day,
           COALESCE(SUM(revenue_cents), 0) AS n
    FROM vkpi_sales_attributions
    WHERE occurred_at >= CAST(? AS TIMESTAMPTZ)
      AND occurred_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

ATTR_REVENUE_SUM_SQL = """
    SELECT COALESCE(SUM(revenue_cents), 0) AS n
    FROM vkpi_sales_attributions
    WHERE occurred_at >= CAST(? AS TIMESTAMPTZ)
      AND occurred_at < CAST(? AS TIMESTAMPTZ)
"""

# ── events ─────────────────────────────────────────────────────────────

EVENTS_NEW_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_events
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

EVENTS_NEW_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_events
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

EVENTS_STARTED_DAY_SQL = """
    SELECT start_date AS day, COUNT(*) AS n
    FROM vkpi_events
    WHERE start_date >= CAST(? AS DATE)
      AND start_date <= CAST(? AS DATE)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

EVENTS_STARTED_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_events
    WHERE start_date >= CAST(? AS DATE)
      AND start_date <= CAST(? AS DATE)
"""

EVENT_EXPENSES_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day,
           COALESCE(SUM(amount), 0) AS n
    FROM vkpi_event_expenses
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

EVENT_EXPENSES_SUM_SQL = """
    SELECT COALESCE(SUM(amount), 0) AS n
    FROM vkpi_event_expenses
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

# ── kol-profile(evidence.created_at 是 naive 列,库内约定 UTC)────────────

KOL_EVIDENCE_NEW_DAY_SQL = """
    SELECT CAST(created_at AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_kol_video_evidence
    WHERE kol_pool_id = ?
      AND is_active IS NOT FALSE
      AND created_at >= CAST(? AS TIMESTAMP)
      AND created_at < CAST(? AS TIMESTAMP)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

KOL_EVIDENCE_NEW_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_kol_video_evidence
    WHERE kol_pool_id = ?
      AND is_active IS NOT FALSE
      AND created_at >= CAST(? AS TIMESTAMP)
      AND created_at < CAST(? AS TIMESTAMP)
"""

KOL_EVIDENCE_PUB_DAY_SQL = """
    SELECT CAST((COALESCE(published_at_norm, publish_date) AT TIME ZONE 'UTC') AS DATE) AS day,
           COUNT(*) AS n
    FROM vkpi_kol_video_evidence
    WHERE kol_pool_id = ?
      AND is_active IS NOT FALSE
      AND COALESCE(published_at_norm, publish_date) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(published_at_norm, publish_date) < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

KOL_EVIDENCE_PUB_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_kol_video_evidence
    WHERE kol_pool_id = ?
      AND is_active IS NOT FALSE
      AND COALESCE(published_at_norm, publish_date) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(published_at_norm, publish_date) < CAST(? AS TIMESTAMPTZ)
"""

# ── autonomy ───────────────────────────────────────────────────────────

INBOX_SUGGESTED_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_action_inbox
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

INBOX_SUGGESTED_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_action_inbox
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

INBOX_EXECUTED_DAY_SQL = """
    SELECT CAST((updated_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_action_inbox
    WHERE status = ?
      AND updated_at >= CAST(? AS TIMESTAMPTZ)
      AND updated_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

INBOX_EXECUTED_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_action_inbox
    WHERE status = ?
      AND updated_at >= CAST(? AS TIMESTAMPTZ)
      AND updated_at < CAST(? AS TIMESTAMPTZ)
"""

INBOX_EXECUTED_STATUS = "executed"

# ── launchpad ──────────────────────────────────────────────────────────

CANDIDATES_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_project_content_posts
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

CANDIDATES_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_project_content_posts
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

APPROVALS_TABLE = "vkpi_publish_approvals"

APPROVALS_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_publish_approvals
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

APPROVALS_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_publish_approvals
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

# ── sku360(SKU 解析 + 别名词表 + 标题扫描;匹配全在 Python,零 LIKE)────────

SKU_PRODUCT_LOOKUP_SQL = """
    SELECT sku FROM vkpi_products WHERE LOWER(sku) = LOWER(?) LIMIT 1
"""

SKU_ALIAS_LOOKUP_SQL = """
    SELECT p.sku AS sku
    FROM vkpi_product_aliases a
    JOIN vkpi_products p ON p.sku = a.sku
    WHERE a.alias_norm = ?
    ORDER BY a.confidence DESC
    LIMIT 1
"""

SKU_ALIASES_SQL = """
    SELECT alias_norm, confidence
    FROM vkpi_product_aliases
    WHERE sku = ?
    ORDER BY confidence DESC, LENGTH(alias_norm) DESC
    LIMIT ?
"""

SKU_TITLE_DAY_SQL = """
    SELECT CAST((COALESCE(published_at_norm, publish_date) AT TIME ZONE 'UTC') AS DATE) AS day,
           COALESCE(NULLIF(title, ''), NULLIF(video_title, '')) AS title
    FROM vkpi_kol_video_evidence
    WHERE is_active IS NOT FALSE
      AND (NULLIF(title, '') IS NOT NULL OR NULLIF(video_title, '') IS NOT NULL)
      AND COALESCE(published_at_norm, publish_date) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(published_at_norm, publish_date) < CAST(? AS TIMESTAMPTZ)
    ORDER BY id DESC
    LIMIT ?
"""

# ── creative(段级素材真实落点=vkpi_analysis_cache final_v1 ready)──────────

CREATIVE_READY_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_analysis_cache
    WHERE target_type = 'video'
      AND derive_method = ?
      AND status = 'ready'
      AND created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

CREATIVE_READY_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_analysis_cache
    WHERE target_type = 'video'
      AND derive_method = ?
      AND status = 'ready'
      AND created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

CREATIVE_SEGMENT_ROWS_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, result
    FROM vkpi_analysis_cache
    WHERE target_type = 'video'
      AND derive_method = ?
      AND status = 'ready'
      AND created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    ORDER BY id DESC
    LIMIT ?
"""

# ── dealers ────────────────────────────────────────────────────────────

DEALERS_TOTAL_SQL = """
    SELECT COUNT(*) AS n FROM vkpi_dealers
"""

DEALERS_NEW_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_dealers
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

DEALERS_NEW_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_dealers
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""
