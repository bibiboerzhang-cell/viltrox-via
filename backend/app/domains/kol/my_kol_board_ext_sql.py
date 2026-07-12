"""MY KOL 看板聚合的 SQL 常量与护栏常量(my_kol_board_ext 的拆分伴随文件)。

口径、诚实空态、显示层宪法与红线全文见 my_kol_board_ext.py 模块 docstring;
本文件只放静态常量,零逻辑零 IO(600 行红线拆分产物,契约测试经主模块引用)。

SQL 红线(契约测试静态审查):全参数化 ? 占位;零字面 percent、零 LIKE(标题
命中用 strpos 参数化);SQL 字符串内零注释(compat 把注释里的 ASCII 问号当占位
符);LIMIT 全下推;明文联系方式/单 KOL fit 分/个人内部字段一列不进 SELECT。
"""
from __future__ import annotations

BOARD_METHOD = "my_kol_board_ext_v1"
VILTROX_TOKEN = "viltrox"          # 标题命中词(strpos 参数,小写包含匹配)

# ── 护栏常量(测试直接断言;全部 SQL LIMIT ? + Python 层二次封顶双保险)──
SERIES_ROWS_LIMIT = 400            # 日聚合 GROUP BY 行封顶(days≤365 + 余量)
SERIES_MAX_DAYS = 370              # 日轴长度 Python 层封顶
FUNNEL_ROWS_LIMIT = 40             # 阶段分组行封顶(真库 13 个 raw 值 + 余量)
PLATFORM_ROWS_LIMIT = 20           # 平台分组 SQL 层封顶
PLATFORM_MAX_ITEMS = 20            # 平台条目 Python 层封顶
FIT_BUCKET_ROWS_LIMIT = 20         # fit 分桶行封顶(十分位至多 10 桶 + NULL 桶)
CONTACT_TYPE_ROWS_LIMIT = 20       # 联系方式类型分组 SQL 层封顶
CONTACT_TYPE_MAX_ITEMS = 20        # 类型条目 Python 层封顶
VIEWS_TOP_LIMIT = 12               # 播放榜 Top 12 封顶(SQL + Python 双封顶)
RECENT_VIDEOS_LIMIT = 60           # 内容墙最近采集视频封顶(SQL + Python 双封顶)
V_KOL_IDS_MAX = 2000               # v_kol_ids 行级名单契约封顶(Python 层切片)
V_KOL_IDS_ROWS_LIMIT = V_KOL_IDS_MAX + 1   # SQL 层多取 1 行,如实检测截断(不靠猜)

# 8 段展示漏斗 = stage_canonical.CANONICAL_STAGES 中真库有 raw 来源的 8 个阶段
# (13 真值:discovery/discovered→discovered,contacted/replied→contacted,agreed,
#  device_sent/shipped→shipped,received/arrived→delivered,content_posted→
#  content_published,measured→retrospective_ready,churned/cancelled→closed)。
FUNNEL_SEGMENTS: tuple[str, ...] = (
    "discovered", "contacted", "agreed", "shipped", "delivered",
    "content_published", "retrospective_ready", "closed",
)

# stage_canonical 未覆盖的真库存量值(census 2 行):arrived=样品已到达,
# gifted_funnel.SENT_STAGES 同列;只在读侧补映射到「已签收」,不改存储值。
EXTRA_RAW_TO_CANONICAL: dict[str, str] = {"arrived": "delivered"}

# ── SQL 常量(全参数化;零字面 percent;零注释;窗口/LIMIT 全下推)──────────
# 收藏集条件:收藏(vkpi_kol_pool_favorites)∪ 共享(vkpi_kol_pool_members,
# 迁移 159),与 my_kol_aggregate._pool_favorites 同两张表;首参=0 表示管理层
# 全团队口径(不按 staff 过滤),否则按该 staff 过滤。占位 4 个,_scope_params 供参。
_COLLECTION_COND = """(
        EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f
                WHERE f.kol_pool_id = kp.id AND (? = 0 OR f.staff_id = ?))
        OR EXISTS (SELECT 1 FROM vkpi_kol_pool_members sm
                   WHERE sm.kol_pool_id = kp.id AND (? = 0 OR sm.staff_id = ?))
      )"""

POOL_FOLLOWERS_DAY_SQL = f"""
    SELECT s.snapshot_date AS day, SUM(s.followers) AS total
    FROM vkpi_kol_fit_snapshot s
    JOIN vkpi_kol_pool kp ON kp.id = s.kol_pool_id
    WHERE kp.duplicate_of_id IS NULL
      AND {_COLLECTION_COND}
      AND s.snapshot_date >= CAST(? AS DATE)
      AND s.snapshot_date <= CAST(? AS DATE)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

NEW_VIDEOS_DAY_SQL = f"""
    SELECT CAST(e.created_at AS DATE) AS day, COUNT(*) AS n
    FROM vkpi_kol_video_evidence e
    JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
    WHERE kp.duplicate_of_id IS NULL
      AND {_COLLECTION_COND}
      AND e.created_at >= CAST(? AS TIMESTAMP)
      AND e.created_at < CAST(? AS TIMESTAMP)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

NEW_VIDEOS_COUNT_SQL = f"""
    SELECT COUNT(*) AS n
    FROM vkpi_kol_video_evidence e
    JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
    WHERE kp.duplicate_of_id IS NULL
      AND {_COLLECTION_COND}
      AND e.created_at >= CAST(? AS TIMESTAMP)
      AND e.created_at < CAST(? AS TIMESTAMP)
"""

OFFICIAL_DAY_SQL = """
    SELECT m.snapshot_date AS day,
           SUM(m.followers) AS followers,
           SUM(m.total_views) AS views
    FROM vkpi_channel_metrics m
    JOIN vkpi_employee_channels c ON c.id = m.channel_id
    WHERE c.deleted_at IS NULL
      AND (? = 0 OR c.staff_id = ?)
      AND m.snapshot_date >= CAST(? AS DATE)
      AND m.snapshot_date <= CAST(? AS DATE)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

KOL_VIEWS_CURRENT_SQL = """
    SELECT COUNT(*) AS total_evidence,
           COUNT(view_count) AS measured,
           COALESCE(SUM(view_count), 0) AS views_total
    FROM vkpi_kol_video_evidence
"""

FUNNEL_SQL = f"""
    SELECT a.stage AS stage, COUNT(*) AS n
    FROM vkpi_project_kol_assignments a
    JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
    WHERE kp.duplicate_of_id IS NULL
      AND {_COLLECTION_COND}
    GROUP BY 1
    ORDER BY 2 DESC
    LIMIT ?
"""

PLATFORM_DIST_SQL = f"""
    SELECT COALESCE(kp.platform, '') AS platform, COUNT(*) AS n
    FROM vkpi_kol_pool kp
    WHERE kp.duplicate_of_id IS NULL
      AND {_COLLECTION_COND}
    GROUP BY 1
    ORDER BY 2 DESC
    LIMIT ?
"""

FIT_DIST_SQL = """
    SELECT CASE WHEN kp.viltrox_fit_score IS NULL THEN NULL
                ELSE LEAST(CAST(FLOOR(kp.viltrox_fit_score / 10) AS INT), 9)
           END AS bucket,
           COUNT(*) AS n
    FROM vkpi_kol_pool kp
    WHERE kp.duplicate_of_id IS NULL
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

CONTACT_TYPES_SQL = """
    SELECT ct.contact_type AS contact_type, COUNT(*) AS n
    FROM vkpi_kol_pool_contacts ct
    GROUP BY 1
    ORDER BY 2 DESC
    LIMIT ?
"""

CONTACT_COVERAGE_SQL = f"""
    SELECT COUNT(*) AS total,
           SUM(CASE WHEN EXISTS (SELECT 1 FROM vkpi_kol_pool_contacts ct
                                 WHERE ct.kol_pool_id = kp.id)
                    THEN 1 ELSE 0 END) AS covered
    FROM vkpi_kol_pool kp
    WHERE kp.duplicate_of_id IS NULL
      AND {_COLLECTION_COND}
"""

VIEWS_TOP_SQL = """
    SELECT e.kol_pool_id AS kol_pool_id,
           COALESCE(NULLIF(kp.display_name, ''), kp.handle, '') AS display_name,
           COALESCE(kp.handle, '') AS handle,
           COALESCE(kp.platform, '') AS platform,
           SUM(e.view_count) AS total_views,
           COUNT(*) AS video_count
    FROM vkpi_kol_video_evidence e
    JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
    WHERE e.view_count IS NOT NULL
      AND e.is_active IS NOT FALSE
    GROUP BY 1, 2, 3, 4
    ORDER BY 5 DESC, 1 ASC
    LIMIT ?
"""

RECENT_VIDEOS_SQL = f"""
    SELECT e.id AS evidence_id,
           e.kol_pool_id AS kol_pool_id,
           e.project_id AS project_id,
           COALESCE(e.content_url, '') AS content_url,
           COALESCE(NULLIF(e.platform, ''), kp.platform, '') AS platform,
           COALESCE(e.title, '') AS title,
           COALESCE(e.video_title, '') AS video_title,
           e.thumbnail_url AS thumbnail_url,
           e.view_count AS view_count,
           e.like_count AS like_count,
           e.publish_date AS publish_date,
           e.posted_at AS posted_at,
           e.created_at AS created_at,
           COALESCE(e.evidence_type, 'video') AS evidence_type,
           COALESCE(NULLIF(kp.display_name, ''), kp.handle, '') AS kol_name,
           COALESCE(kp.handle, '') AS kol_handle,
           EXISTS (SELECT 1 FROM vkpi_analysis_cache c
                   WHERE c.target_type = 'video'
                     AND c.target_id = e.id::text
                     AND c.derive_method = 'video_analysis_final_v1'
                     AND c.status = 'ready') AS has_final_v1_cache
    FROM vkpi_kol_video_evidence e
    JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
    WHERE kp.duplicate_of_id IS NULL
      AND e.is_active IS NOT FALSE
      AND COALESCE(e.evidence_type, 'video') IN ('video', 'image')
      AND {_COLLECTION_COND}
    ORDER BY COALESCE(e.publish_date, e.posted_at, e.created_at) DESC NULLS LAST, e.id DESC
    LIMIT ?
"""

V_CONTENT_SQL = """
    SELECT COUNT(*) AS total_evidence,
           SUM(CASE WHEN e.project_id IS NOT NULL THEN 1 ELSE 0 END) AS cooperation,
           SUM(CASE WHEN strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?) > 0
                    THEN 1 ELSE 0 END) AS title_mention,
           SUM(CASE WHEN e.project_id IS NOT NULL
                     AND strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?) > 0
                    THEN 1 ELSE 0 END) AS overlap_both
    FROM vkpi_kol_video_evidence e
"""

V_KOL_COUNT_SQL = """
    SELECT COUNT(DISTINCT e.kol_pool_id) AS n
    FROM vkpi_kol_video_evidence e
    WHERE e.project_id IS NOT NULL
       OR strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?) > 0
"""

V_KOL_IDS_SQL = """
    SELECT DISTINCT e.kol_pool_id AS kol_pool_id
    FROM vkpi_kol_video_evidence e
    WHERE e.kol_pool_id IS NOT NULL
      AND (e.project_id IS NOT NULL
           OR strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?) > 0)
    ORDER BY 1
    LIMIT ?
"""

V_KOL_TIERS_SQL = """
    SELECT COUNT(DISTINCT CASE WHEN e.project_id IS NOT NULL
                               THEN e.kol_pool_id END) AS cooperation_kols,
           COUNT(DISTINCT CASE WHEN strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?) > 0
                               THEN e.kol_pool_id END) AS title_mention_kols
    FROM vkpi_kol_video_evidence e
"""
