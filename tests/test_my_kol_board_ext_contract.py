"""MY KOL 看板聚合(my_kol_board_ext · M2)契约测试 —— 零 DB 依赖(mock conn)。

覆盖点(施工单验收口径,mock conn 模式照 test_voice_report_ext_contract):
  1. SQL 常量静态审查:参数化 ? / LIMIT 下推 / 零字面 percent、零 LIKE(标题命中
     strpos 参数化)/ 显示层宪法(contact_value、email、consent_basis、
     avatar、raw_platform_data 绝不入 SELECT);
  2. kpi_series:流量型(新视频)日轴 0 填齐 + 右沿=今天;存量型(收藏集粉丝)
     缺快照日 value=null 断点(绝不 0 填冒充掉粉);
  3. 环比口径:上窗等长参数下推;上窗无数 → delta_pct=null;有数 → 正确百分比;
  4. kol_views 点时读数:无 series 无 delta(诚实缺席),填充率分母 0 → null;
  5. funnel:13 真阶段(含 stage_canonical 未覆盖的 arrived)归并 8 段,
     未知新值落 other 诚实桶;
  6. fit_dist:十分位桶 + 未评分诚实桶;响应只有桶计数,零单 KOL 分数;
  7. contact_coverage / 私字段:contact_value、email 混进行里也绝不进响应;
  8. LIMIT 双层封顶:SQL 参数=模块常量,Python 层切片二次封顶;
  9. v_content 五档判定:latest ready final_v1 detected/products + project_id +
     中英文标题词 + explicit false / unknown；SQL 汇总、recent_videos、
     classify_v_content 纯函数同口径;行级名单
     v_kol_ids 去重升序/封顶 2000 truncated 如实标注/与 v_kol_count 一致;
     tiers_by_kol KOL 级去重计数与条数级 tiers 区分;
 10. 信封契约键 + 单组失败降级 {status:'error'} 不拖全响应 + scope 参数下推。
红线:纯读契约,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import my_kol_board_ext as ext  # noqa: E402


# ── mock conn(仿 test_voice_report_ext_contract._FakeConn)────────────────


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """routes: {SQL 常量: rows 或 callable(params)->rows};没配的 SQL 返回空。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        for known_sql, rows in self.routes.items():
            if sql == known_sql:
                return _FakeResult(rows(tuple(params)) if callable(rows) else rows)
        return _FakeResult([])


ALL_SQL_CONSTANTS = (
    ext.POOL_FOLLOWERS_DAY_SQL,
    ext.NEW_VIDEOS_DAY_SQL,
    ext.NEW_VIDEOS_COUNT_SQL,
    ext.OFFICIAL_DAY_SQL,
    ext.KOL_VIEWS_CURRENT_SQL,
    ext.FUNNEL_SQL,
    ext.PLATFORM_DIST_SQL,
    ext.FIT_DIST_SQL,
    ext.CONTACT_TYPES_SQL,
    ext.CONTACT_COVERAGE_SQL,
    ext.VIEWS_TOP_SQL,
    ext.RECENT_VIDEOS_SQL,
    ext.V_CONTENT_SUMMARY_SQL,
    ext.V_CONTENT_SUMMARY_MATERIALIZED_SQL,
)
ALL_SQL = "\n".join(ALL_SQL_CONSTANTS)

# 冻结 now = 2026-07-11 12:00 UTC;days=30 → 日轴 2026-06-12..2026-07-11(30 天)
FROZEN_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
SINCE_D, UNTIL_D = "2026-06-12", "2026-07-11"
PREV_SINCE_D, PREV_UNTIL_D = "2026-05-13", "2026-06-11"
SINCE_TS = "2026-06-12T00:00:00"
UNTIL_TS = "2026-07-11T12:00:00"
PREV_SINCE_TS = "2026-05-13T00:00:00"
PREV_UNTIL_TS = "2026-06-11T12:00:00"   # 上窗起点 + 同等流逝长度(29.5 天)

GROUP_KEYS = (
    "kpi_series", "funnel", "platform_dist", "fit_dist",
    "contact_coverage", "views_top", "recent_videos", "v_content",
)


def _build(conn, sid=None, days=30, monkeypatch=None):
    return ext.build_board_ext(conn, staff_scope_id=sid, days=days)


def _frozen(monkeypatch):
    monkeypatch.setattr(ext, "_now_utc", lambda: FROZEN_NOW)


def _v_summary_rows(summary, ids=(None,)):
    """Single-query wire shape: one repeated summary row per returned KOL id."""
    return [{**summary, "kol_pool_id": kid} for kid in ids]


# ── 1. SQL 常量静态审查 ─────────────────────────────────────────────────


def test_sql_constants_parameterized_with_limit_pushdown():
    limited = (
        ext.POOL_FOLLOWERS_DAY_SQL, ext.NEW_VIDEOS_DAY_SQL, ext.OFFICIAL_DAY_SQL,
        ext.FUNNEL_SQL, ext.PLATFORM_DIST_SQL, ext.FIT_DIST_SQL,
        ext.CONTACT_TYPES_SQL, ext.VIEWS_TOP_SQL, ext.RECENT_VIDEOS_SQL,
        ext.V_CONTENT_SUMMARY_SQL,
    )
    for sql in limited:
        assert "LIMIT ?" in sql
    # 所有查询均参数化；MY KOL 点时播放也必须携带 collection scope。
    for sql in ALL_SQL_CONSTANTS:
        assert "?" in sql


def test_my_kol_content_queries_use_favorite_or_authorized_share_scope():
    """MY KOL 内容指标必须同时接受本人收藏和授权共享，不能扫全 evidence。"""
    scoped_sqls = (
        ext.KOL_VIEWS_CURRENT_SQL,
        ext.CONTACT_TYPES_SQL,
        ext.VIEWS_TOP_SQL,
        ext.V_CONTENT_SUMMARY_SQL,
    )
    for sql in scoped_sqls:
        assert "vkpi_kol_pool_favorites" in sql
        assert "vkpi_kol_pool_members" in sql
        assert "kp.duplicate_of_id IS NULL" in sql
    # scope 只裁行，不改变合作项目证据的五档语义。
    assert "project_linked" in ext.V_CONTENT_CLASSIFIED_CTE
    assert "WHEN s.project_linked THEN 'cooperation'" in ext.V_CONTENT_CLASSIFIED_CTE


def test_sql_compat_redlines_no_percent_no_like():
    """compat 红线:SQL 字符串零字面 percent;标题命中走 strpos 参数化,不用 LIKE。"""
    assert "%" not in ALL_SQL
    assert " LIKE " not in f" {ALL_SQL.upper()} ".replace("\n", " ")
    assert "strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?)" in ext.V_CONTENT_SUMMARY_SQL


def test_v_content_queries_share_latest_ready_five_tier_cte():
    """聚合、名单与 recent 共用同一 CTE，只消费 final_v1 结构化证据。"""
    queries = (ext.RECENT_VIDEOS_SQL, ext.V_CONTENT_SUMMARY_SQL)
    for sql in queries:
        assert sql.startswith(ext.V_CONTENT_CLASSIFIED_CTE)
        assert "c.target_type = 'video'" in sql
        assert "c.derive_method = 'video_analysis_final_v1'" in sql
        assert "c.status = 'ready'" in sql
        assert "ORDER BY c.id DESC" in sql and "LIMIT 1" in sql
        assert "{raw_gemini_video,viltrox_detected}" in sql
        assert "{raw_gemini_video,viltrox_products_all}" in sql
        assert "analysis_confirmed" in sql and "not_related" in sql
        assert "e.is_active IS NOT FALSE" in sql
    assert ext.VILTROX_TITLE_TOKENS == ("viltrox", "唯卓仕", "唯卓")
    assert ext.V_CONTENT_CLASSIFIED_CTE.count("final_v1_competitor_mentions") == 1
    assert "c.result AS" not in ext.RECENT_VIDEOS_SQL


def test_v_content_summary_materializes_once_with_sqlite_compat_twin():
    """PostgreSQL materializes the scoped classification; sqlite keeps the same query shape."""
    pg = ext.V_CONTENT_SUMMARY_MATERIALIZED_SQL
    sqlite = ext.V_CONTENT_SUMMARY_SQL
    assert "scoped_v_content AS MATERIALIZED (" in pg
    assert "scoped_v_content AS (" in sqlite
    assert "MATERIALIZED" not in sqlite
    assert pg.replace("MATERIALIZED ", "") == sqlite
    for sql in (pg, sqlite):
        assert sql.count("WITH v_content_signals AS") == 1
        assert sql.count("FROM scoped_v_content") == 2
        assert "COUNT(DISTINCT CASE WHEN v_tier IN" in sql
        assert "LEFT JOIN v_content_ids ids ON TRUE" in sql
        assert "ORDER BY ids.kol_pool_id" in sql


def test_postgres_v_content_uses_materialized_query_once(monkeypatch):
    """Runtime abstraction selects PostgreSQL MATERIALIZED without changing params/output."""
    _frozen(monkeypatch)

    class PostgresCompatConnection(_FakeConn):
        pass

    summary = {
        "total_evidence": 3, "cooperation": 1, "analysis_confirmed": 1,
        "title_mention": 0, "not_related": 1, "undetermined": 0,
        "v_kol_count": 2, "cooperation_kols": 1,
        "analysis_confirmed_kols": 1, "title_mention_kols": 0,
        "not_related_kols": 1, "undetermined_kols": 0,
    }
    conn = PostgresCompatConnection(routes={
        ext.V_CONTENT_SUMMARY_MATERIALIZED_SQL: _v_summary_rows(summary, (7, 42)),
    })
    vc = ext._v_content(conn, 7684)
    assert vc["tiers"] == {
        "cooperation": 1, "analysis_confirmed": 1, "title_mention": 0,
        "not_related": 1, "undetermined": 0,
    }
    assert vc["v_kol_ids"] == [7, 42]
    assert conn.calls == [(ext.V_CONTENT_SUMMARY_MATERIALIZED_SQL, (
        *ext.VILTROX_TITLE_TOKENS, 7684, 7684, 7684, 7684,
        ext.V_KOL_IDS_ROWS_LIMIT,
    ))]


def test_sql_never_selects_private_columns():
    """显示层宪法:明文联系方式/个人内部字段一列不进任何 SELECT。"""
    for forbidden in (
        "contact_value", "consent_basis", "contact_source", "source_url",
        "evidence_text", "avatar", "raw_platform_data", "other_contacts_json",
        "viltrox_fit_reason", ".email", "profile_url",
    ):
        assert forbidden not in ALL_SQL, forbidden
    # fit 列只允许出现在分桶表达式(FIT_DIST_SQL),别处零触
    others = "\n".join(s for s in ALL_SQL_CONSTANTS if s is not ext.FIT_DIST_SQL)
    assert "viltrox_fit_score" not in others


def test_limit_cap_constants():
    assert ext.SERIES_MAX_DAYS == 370
    assert ext.VIEWS_TOP_LIMIT == 12
    assert ext.PLATFORM_MAX_ITEMS == 20
    assert ext.FUNNEL_ROWS_LIMIT == 40
    assert ext.CONTACT_TYPE_MAX_ITEMS == 20


# ── 2. kpi_series:流量 0 填 / 存量 null 断点 / 右沿=今天 ─────────────────


def test_new_videos_zero_fill_and_axis_clamped_to_today(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.NEW_VIDEOS_DAY_SQL: [
            {"day": "2026-06-13", "n": 4},
            {"day": "2026-07-10", "n": 2},
        ],
    })
    body = _build(conn)
    series = body["kpi_series"]["series"]["new_videos"]
    assert len(series) == 30                       # 30 天连续日轴
    assert series[0] == {"date": SINCE_D, "count": 0}
    assert series[1] == {"date": "2026-06-13", "count": 4}
    assert series[-2] == {"date": "2026-07-10", "count": 2}
    assert series[-1] == {"date": UNTIL_D, "count": 0}   # 右沿=今天,零未来日
    assert sum(s["count"] for s in series) == 6


def test_pool_followers_gap_is_null_not_zero(monkeypatch):
    """存量型缺快照日=null 断点(0 填会冒充「掉粉到 0」——编数据)。"""
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.POOL_FOLLOWERS_DAY_SQL: lambda params: (
            [{"day": "2026-07-09", "total": 100}, {"day": "2026-07-10", "total": 120}]
            if params[4] == SINCE_D else []
        ),
    })
    body = _build(conn)
    series = body["kpi_series"]["series"]["pool_followers"]
    by_date = {s["date"]: s["value"] for s in series}
    assert by_date["2026-07-09"] == 100
    assert by_date["2026-07-10"] == 120
    assert by_date["2026-07-11"] is None           # 缺快照日 → null 断点
    assert by_date[SINCE_D] is None
    assert all(s["value"] != 0 for s in series)    # 绝不 0 填


# ── 3. 环比口径:等长上窗参数下推 + 上窗无数 → null ───────────────────────


def test_delta_windows_pushed_down_and_null_when_prev_missing(monkeypatch):
    _frozen(monkeypatch)

    def videos_count(params):
        return [{"n": 15}] if params[4] == SINCE_TS else [{"n": 10}]

    conn = _FakeConn(routes={
        ext.NEW_VIDEOS_COUNT_SQL: videos_count,
        ext.POOL_FOLLOWERS_DAY_SQL: lambda params: (
            [{"day": "2026-07-10", "total": 300}] if params[4] == SINCE_D else []
        ),
    })
    body = _build(conn)
    metrics = body["kpi_series"]["metrics"]

    # 流量型:15 vs 10 → +50.0%;时间戳窗两侧同取已流逝等长时段
    assert metrics["new_videos"] == {
        "current": 15, "previous": 10, "delta_pct": 50.0,
        "table": "vkpi_kol_video_evidence",
    }
    count_calls = [c[1] for c in conn.calls if c[0] == ext.NEW_VIDEOS_COUNT_SQL]
    assert count_calls == [
        (0, 0, 0, 0, SINCE_TS, UNTIL_TS),
        (0, 0, 0, 0, PREV_SINCE_TS, PREV_UNTIL_TS),
    ]

    # 存量型:上窗无快照 → previous=null、delta_pct=null(诚实省略药丸)
    pf = metrics["pool_followers"]
    assert pf["current"] == 300 and pf["previous"] is None and pf["delta_pct"] is None
    day_calls = [c[1] for c in conn.calls if c[0] == ext.POOL_FOLLOWERS_DAY_SQL]
    assert day_calls == [
        (0, 0, 0, 0, SINCE_D, UNTIL_D, ext.SERIES_ROWS_LIMIT),
        (0, 0, 0, 0, PREV_SINCE_D, PREV_UNTIL_D, ext.SERIES_ROWS_LIMIT),
    ]


def test_official_stock_delta_uses_latest_snapshot_day(monkeypatch):
    _frozen(monkeypatch)

    def official(params):
        if params[2] == SINCE_D:      # 本窗两天,取最近日 220
            return [
                {"day": "2026-07-09", "followers": 200, "views": 9000},
                {"day": "2026-07-10", "followers": 220, "views": 9500},
            ]
        return [{"day": "2026-06-10", "followers": 200, "views": 8000}]

    conn = _FakeConn(routes={ext.OFFICIAL_DAY_SQL: official})
    body = _build(conn)
    metrics = body["kpi_series"]["metrics"]
    assert metrics["official_followers"] == {
        "current": 220, "previous": 200, "delta_pct": 10.0, "table": "vkpi_channel_metrics",
    }
    assert metrics["official_views"]["current"] == 9500
    assert metrics["official_views"]["previous"] == 8000
    assert metrics["official_views"]["delta_pct"] == 18.8


# ── 4. kol_views 点时读数:无 series 无 delta,诚实缺席 ───────────────────


def test_kol_views_point_in_time_no_series_no_delta(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.KOL_VIEWS_CURRENT_SQL: [
            {"total_evidence": 2769, "measured": 2134, "views_total": 987654321},
        ],
    })
    kv = _build(conn)["kpi_series"]["kol_views"]
    assert kv["status"] == "point_in_time"
    assert kv["views_total"] == 987654321
    assert kv["measured"] == 2134 and kv["evidence_total"] == 2769
    assert kv["fill_rate"] == round(2134 / 2769, 4)   # 77.07%
    assert "series" not in kv and "delta_pct" not in kv   # 无时序无环比,诚实缺席
    assert "点时实测" in kv["basis"]
    assert kv["scope"]["membership"] == "favorite_or_authorized_share"


def test_kol_views_fill_rate_null_when_no_evidence(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.KOL_VIEWS_CURRENT_SQL: [{"total_evidence": 0, "measured": 0, "views_total": 0}],
    })
    kv = _build(conn)["kpi_series"]["kol_views"]
    assert kv["fill_rate"] is None                     # 分母 0 → null,不编 0%


# ── 5. funnel:13 真阶段归并 8 段 + arrived 补映射 + 未知落 other ─────────


def test_funnel_merges_13_raw_stages_into_8_segments(monkeypatch):
    _frozen(monkeypatch)
    census = [   # 真库 2026-07 census 全量 13 值
        {"stage": "device_sent", "n": 842}, {"stage": "content_posted", "n": 695},
        {"stage": "contacted", "n": 209}, {"stage": "discovered", "n": 170},
        {"stage": "churned", "n": 160}, {"stage": "agreed", "n": 39},
        {"stage": "replied", "n": 33}, {"stage": "discovery", "n": 23},
        {"stage": "shipped", "n": 7}, {"stage": "cancelled", "n": 6},
        {"stage": "arrived", "n": 2}, {"stage": "received", "n": 2},
        {"stage": "measured", "n": 1},
        {"stage": "weird_new_stage", "n": 5},   # 未知新值 → other 诚实桶
    ]
    conn = _FakeConn(routes={ext.FUNNEL_SQL: census})
    funnel = _build(conn)["funnel"]
    assert funnel["status"] == "ready"
    assert [i["stage"] for i in funnel["items"]] == list(ext.FUNNEL_SEGMENTS)
    by_stage = {i["stage"]: i for i in funnel["items"]}
    assert by_stage["discovered"]["count"] == 193      # discovery + discovered
    assert by_stage["contacted"]["count"] == 242       # contacted + replied
    assert by_stage["agreed"]["count"] == 39
    assert by_stage["shipped"]["count"] == 849         # device_sent + shipped
    assert by_stage["delivered"]["count"] == 4         # received + arrived(补映射)
    assert by_stage["delivered"]["raw_stages"] == ["arrived", "received"]
    assert by_stage["content_published"]["count"] == 695
    assert by_stage["retrospective_ready"]["count"] == 1
    assert by_stage["closed"]["count"] == 166          # churned + cancelled
    assert funnel["other"] == [{"stage": "weird_new_stage", "count": 5}]
    assert funnel["total"] == 2189 + 5
    assert all(i["label"] for i in funnel["items"])    # 中文标签齐


# ── 6. fit_dist:桶计数 + 未评分诚实桶,零单 KOL 分数 ─────────────────────


def test_fit_dist_buckets_and_unscored(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.FIT_DIST_SQL: [
            {"bucket": None, "n": 225},    # 未评分(NULL 分组)
            {"bucket": 4, "n": 100},
            {"bucket": 7, "n": 300},
            {"bucket": 9, "n": 50},
        ],
    })
    dist = _build(conn)["fit_dist"]
    assert dist["status"] == "ready"
    assert dist["unscored"] == 225
    assert dist["scored_total"] == 450
    assert dist["total"] == 675
    assert len(dist["buckets"]) == 10                  # 十分位固定 10 桶(0 计数也齐)
    by_label = {b["bucket"]: b["count"] for b in dist["buckets"]}
    assert by_label["40-49"] == 100
    assert by_label["70-79"] == 300
    assert by_label["90-100"] == 50
    assert by_label["0-9"] == 0
    flat = repr(dist)
    assert "viltrox_fit_score" not in flat             # 响应零列名零单 KOL 分数


# ── 7. contact_coverage / 私字段不外泄 ──────────────────────────────────


def test_contact_coverage_counts_only_and_share(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.CONTACT_TYPES_SQL: [
            # 行里混入明文也绝不进响应(显示层宪法回归)
            {"contact_type": "email", "n": 363, "contact_value": "leak@example.com"},
            {"contact_type": "website", "n": 138},
        ],
        ext.CONTACT_COVERAGE_SQL: [{"total": 719, "covered": 247}],
    })
    cov = _build(conn)["contact_coverage"]
    assert cov["types"][0] == {"contact_type": "email", "count": 363}
    assert cov["covered"] == 247 and cov["total"] == 719
    assert cov["coverage"] == round(247 / 719, 4)
    assert "leak@example.com" not in repr(cov)


def test_contact_coverage_share_null_when_empty_collection(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={ext.CONTACT_COVERAGE_SQL: [{"total": 0, "covered": 0}]})
    cov = _build(conn)["contact_coverage"]
    assert cov["coverage"] is None                     # 分母 0 → null,不编 0%


def test_response_never_leaks_private_fields(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.VIEWS_TOP_SQL: [
            {"kol_pool_id": 9, "display_name": "n", "handle": "h", "platform": "youtube",
             "total_views": 5, "video_count": 1,
             "email": "secret@example.com", "contact_value": "+86-13800000000",
             "viltrox_fit_score": 88.5},
        ],
        ext.CONTACT_TYPES_SQL: [
            {"contact_type": "email", "n": 1, "contact_value": "secret2@example.com"},
        ],
        ext.RECENT_VIDEOS_SQL: [
            {"evidence_id": 3, "kol_pool_id": 9, "project_id": None, "content_url": "",
             "platform": "tiktok", "title": "t", "video_title": "", "thumbnail_url": "",
             "view_count": 1, "like_count": 0, "publish_date": "2026-07-01", "posted_at": None,
             "created_at": None, "evidence_type": "video", "kol_name": "n", "kol_handle": "h",
             "has_final_v1_cache": 0,
             "email": "secret3@example.com", "contact_value": "+86-13900000000",
             "viltrox_fit_score": 77.5},
        ],
    })
    flat = repr(_build(conn))
    for leaked in (
        "secret@example.com", "secret2@example.com", "secret3@example.com",
        "+86-13800000000", "+86-13900000000",
        "88.5", "77.5", "contact_value", "viltrox_fit_score",
    ):
        assert leaked not in flat


# ── 8. LIMIT 双层封顶 ───────────────────────────────────────────────────


def test_limits_pushed_down_and_python_side_capped(monkeypatch):
    _frozen(monkeypatch)
    over_views = [
        {"kol_pool_id": i, "display_name": f"d{i}", "handle": f"h{i}",
         "platform": "tiktok", "total_views": 1000 - i, "video_count": 1}
        for i in range(30)
    ]
    over_platforms = [{"platform": f"p{i}", "n": i + 1} for i in range(40)]
    conn = _FakeConn(routes={
        ext.VIEWS_TOP_SQL: over_views,
        ext.PLATFORM_DIST_SQL: over_platforms,
    })
    body = _build(conn)
    # Python 层二次封顶(哪怕 SQL 层被 mock 放水)
    assert len(body["views_top"]["items"]) == ext.VIEWS_TOP_LIMIT
    assert len(body["platform_dist"]["items"]) == ext.PLATFORM_MAX_ITEMS
    # SQL 层 LIMIT 参数 = 模块常量
    by_sql = {sql: params for sql, params in conn.calls}
    assert by_sql[ext.VIEWS_TOP_SQL] == (0, 0, 0, 0, ext.VIEWS_TOP_LIMIT)
    assert by_sql[ext.PLATFORM_DIST_SQL][-1] == ext.PLATFORM_ROWS_LIMIT
    assert by_sql[ext.FUNNEL_SQL][-1] == ext.FUNNEL_ROWS_LIMIT
    assert by_sql[ext.FIT_DIST_SQL] == (ext.FIT_BUCKET_ROWS_LIMIT,)
    assert by_sql[ext.CONTACT_TYPES_SQL] == (0, 0, 0, 0, ext.CONTACT_TYPE_ROWS_LIMIT)


# ── 8.5 recent_videos 内容墙(收藏集最近采集 + 缩略图自愈链)────────────────


def test_recent_videos_honest_nulls_bool_and_thumbnail_chain(monkeypatch):
    """view_count NULL 原样透出(未实测 ≠ 0);BOOLEAN 读回 1 → 真布尔;
    缩略图三件套=创意库自愈链(无 raw 无缓存 → youtube 派生兜底)。"""
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.RECENT_VIDEOS_SQL: [
            {"evidence_id": 11, "kol_pool_id": 101, "project_id": 7,
             "content_url": "https://www.youtube.com/watch?v=abcdefghijk",
             "platform": "youtube", "title": "Coop film", "video_title": "",
             "thumbnail_url": "", "view_count": None, "like_count": 5,
             "publish_date": "2026-07-10", "posted_at": None,
             "created_at": "2026-07-10T08:00:00", "evidence_type": "video",
             "kol_name": "Alpha", "kol_handle": "@alpha", "has_final_v1_cache": 1,
             "llm_viltrox_status": "present",
             "llm_viltrox_detected_text": "true",
             "llm_viltrox_products": '["AF 85mm F1.4 Pro"]',
             "llm_competitor_mentions": ["Sigma"], "v_tier": "analysis_confirmed"},
        ],
    })
    rv = _build(conn)["recent_videos"]
    assert rv["status"] == "ready"
    item = rv["items"][0]
    assert item["view_count"] is None                    # 未实测保持 null,绝不 0 填
    assert item["like_count"] == 5
    assert item["has_final_v1_cache"] is True            # BOOLEAN 读回 int 1 → 真布尔
    assert item["llm_viltrox_status"] == "present"
    assert item["llm_viltrox_detected"] is True
    assert item["llm_viltrox_products"] == ["AF 85mm F1.4 Pro"]
    assert item["llm_competitor_mentions"] == ["Sigma"]
    assert item["v_tier"] == "analysis_confirmed"
    assert item["project_id"] == 7 and item["kol_name"] == "Alpha"
    assert item["publish_date"] == "2026-07-10"
    # 三件套:raw 空、无本地缓存 → youtube 派生;best 链序 cached → raw → youtube
    assert item["thumbnail_url"] is None and item["cached_thumbnail_url"] is None
    assert item["youtube_thumbnail_url"] == "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg"
    assert item["best_thumbnail"] == item["youtube_thumbnail_url"]


def test_recent_videos_limit_double_capped_scope_pushed_and_empty_reason(monkeypatch):
    _frozen(monkeypatch)
    over = [
        {"evidence_id": i, "kol_pool_id": 1, "project_id": None, "content_url": "",
         "platform": "tiktok", "title": f"t{i}", "video_title": "", "thumbnail_url": "",
         "view_count": 1, "like_count": 0, "publish_date": "2026-07-01", "posted_at": None,
         "created_at": None, "evidence_type": "video", "kol_name": "n", "kol_handle": "h",
         "has_final_v1_cache": 0}
        for i in range(ext.RECENT_VIDEOS_LIMIT + 25)
    ]
    conn = _FakeConn(routes={ext.RECENT_VIDEOS_SQL: over})
    body = _build(conn, sid=7684)
    # Python 层二次封顶(哪怕 SQL 层被 mock 放水)+ scope 4 参与 LIMIT 常量下推
    # (SQL 多取 1 行只用于 has_more 判定;无游标时 keyset 段 7 参短路为假 = 旧行为)
    assert len(body["recent_videos"]["items"]) == ext.RECENT_VIDEOS_LIMIT
    assert body["recent_videos"]["page"]["has_more"] is True
    params = next(p for s, p in conn.calls if s == ext.RECENT_VIDEOS_SQL)
    assert params == (
        *ext.VILTROX_TITLE_TOKENS, 7684, 7684, 7684, 7684,
        None, None, 0, 0,
        False, None, None, None, 0, None, 0,
        ext.RECENT_VIDEOS_LIMIT + 1,
    )
    # 空收藏集 → 诚实 empty + reason(不摆假卡)
    empty = _build(_FakeConn())["recent_videos"]
    assert empty["status"] == "empty" and "诚实空" in empty["reason"]


# ── 9. v_content 五档互斥判定 ───────────────────────────────────────────


def test_v_content_tiers_math(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.V_CONTENT_SUMMARY_SQL: _v_summary_rows({
            "total_evidence": 2769, "cooperation": 806,
            "analysis_confirmed": 400, "title_mention": 273,
            "not_related": 500, "undetermined": 790, "v_kol_count": 387,
            "cooperation_kols": 210, "analysis_confirmed_kols": 140,
            "title_mention_kols": 120, "not_related_kols": 240,
            "undetermined_kols": 330,
        }),
    })
    vc = _build(conn)["v_content"]
    assert vc["status"] == "ready"
    assert vc["total_evidence"] == 2769
    assert vc["v_kol_count"] == 387
    assert vc["v_related_evidence"] == 1479
    assert vc["tiers"] == {
        "cooperation": 806,
        "analysis_confirmed": 400,
        "title_mention": 273,
        "not_related": 500,
        "undetermined": 790,
    }
    assert vc["tiers_by_kol"] == {
        "cooperation_kols": 210,
        "analysis_confirmed_kols": 140,
        "title_mention_kols": 120,
        "not_related_kols": 240,
        "undetermined_kols": 330,
    }
    # 同一次查询传入标题词、team collection scope 和 2001 行读取封顶。
    calls = [(sql, params) for sql, params in conn.calls if sql == ext.V_CONTENT_SUMMARY_SQL]
    assert calls == [(ext.V_CONTENT_SUMMARY_SQL, (
        *ext.VILTROX_TITLE_TOKENS, 0, 0, 0, 0, ext.V_KOL_IDS_ROWS_LIMIT,
    ))]


def test_v_kol_ids_dedup_sorted_and_consistent_with_count(monkeypatch):
    """行级名单:去重升序(mock 放水乱序带重复也稳);未截断时长度=v_kol_count。"""
    _frozen(monkeypatch)
    summary = {
        "total_evidence": 10, "cooperation": 4, "analysis_confirmed": 1,
        "title_mention": 2, "not_related": 1, "undetermined": 2,
        "v_kol_count": 4,
    }
    conn = _FakeConn(routes={
        ext.V_CONTENT_SUMMARY_SQL: _v_summary_rows(
            summary, (42, 7, 42, 9001, 0, None, 13),
        ),
    })
    vc = _build(conn)["v_content"]
    assert vc["v_kol_ids"] == [7, 13, 42, 9001]        # 去重 + 升序,0/None 剔除
    assert vc["v_kol_ids_truncated"] is False
    assert "v_kol_ids_note" not in vc                   # 未截断不带 note
    assert len(vc["v_kol_ids"]) == vc["v_kol_count"]    # 名单与 KOL 级计数一致
    # LIMIT 下推=封顶+1(多取 1 行如实检测截断),词元参数化
    by_sql = {sql: params for sql, params in conn.calls}
    assert by_sql[ext.V_CONTENT_SUMMARY_SQL] == (
        *ext.VILTROX_TITLE_TOKENS, 0, 0, 0, 0, ext.V_KOL_IDS_ROWS_LIMIT,
    )
    assert ext.V_KOL_IDS_ROWS_LIMIT == ext.V_KOL_IDS_MAX + 1


def test_v_kol_ids_capped_at_2000_with_truncated_note(monkeypatch):
    """封顶 2000:超出 → 切片 + truncated:true + note;v_kol_count 仍给全量真数。"""
    _frozen(monkeypatch)
    summary = {
        "total_evidence": 5000, "cooperation": 2500, "analysis_confirmed": 0,
        "title_mention": 0, "not_related": 1000, "undetermined": 1500,
        "v_kol_count": 2050,
    }
    conn = _FakeConn(routes={
        # 仿真 SQL LIMIT 2001；更多行不应进入 Python 结果。
        ext.V_CONTENT_SUMMARY_SQL: _v_summary_rows(
            summary, range(1, ext.V_KOL_IDS_ROWS_LIMIT + 1),
        ),
    })
    vc = _build(conn)["v_content"]
    assert ext.V_KOL_IDS_MAX == 2000
    assert len(vc["v_kol_ids"]) == ext.V_KOL_IDS_MAX    # Python 层切片封顶
    assert vc["v_kol_ids"] == list(range(1, ext.V_KOL_IDS_MAX + 1))   # id 升序前 2000
    assert vc["v_kol_ids_truncated"] is True
    assert "2000" in vc["v_kol_ids_note"] and "完整去重总数" in vc["v_kol_ids_note"]
    assert vc["v_kol_count"] == 2050                    # 完整去重总数仍单独给出


def test_classify_v_content_pure_function():
    """五档优先级:项目 > 受控tri-state > legacy > 标题 > 明确非相关 > 未判定。"""
    assert ext.classify_v_content(123, "random", final_v1_detected=False) == "cooperation"
    assert ext.classify_v_content(None, "random", final_v1_brand_status="present") == "analysis_confirmed"
    assert ext.classify_v_content(None, "random", final_v1_brand_status="absent") == "not_related"
    assert ext.classify_v_content(
        None,
        "Sony FE 50mm review",
        final_v1_brand_status="unknown",
        final_v1_detected=False,
    ) == "undetermined"
    assert ext.classify_v_content(None, "random", final_v1_detected=True) == "analysis_confirmed"
    assert ext.classify_v_content(None, "random", final_v1_detected=False,
                                  final_v1_products=["AF 85mm"]) == "analysis_confirmed"
    assert ext.classify_v_content(None, "My VILTROX 85mm review") == "title_mention"
    assert ext.classify_v_content(None, "唯卓仕 35mm 实拍", final_v1_detected=False) == "title_mention"
    assert ext.classify_v_content(None, "唯卓新品", final_v1_detected=False) == "title_mention"
    assert ext.classify_v_content(None, "Sony FE 50mm review", final_v1_detected=False) == "not_related"
    assert ext.classify_v_content(None, "Sony FE 50mm review") == "undetermined"
    assert ext.classify_v_content(None, None) == "undetermined"
    assert ext.classify_v_content("", "") == "undetermined"
    assert ext.classify_v_content(0, "no project zero id") == "undetermined"


def test_sql_and_pure_classifier_prioritise_new_tri_state_over_legacy_false():
    sql = ext.V_CONTENT_CLASSIFIED_CTE
    assert "brand_product_evidence,viltrox_status" in sql
    assert "s.final_v1_brand_status = 'present'" in sql
    assert "s.final_v1_brand_status = 'absent'" in sql
    assert "s.final_v1_brand_status = '' AND s.final_v1_detected = 'false'" in sql
    assert ext.classify_v_content(
        None,
        "Autofocus comparison",
        final_v1_brand_status="unknown",
        final_v1_detected=False,
    ) == "undetermined"


# ── 10. 信封契约 + 单组降级 + scope 参数 ─────────────────────────────────


def test_envelope_contract_keys(monkeypatch):
    _frozen(monkeypatch)
    body = _build(_FakeConn())
    assert set(body.keys()) == {
        "status", "days", "window", "staff_scope_id",
        *GROUP_KEYS,
        "method", "generated_at",
    }
    assert body["status"] == "ready"
    assert body["days"] == 30
    assert body["window"] == {
        "since": SINCE_D, "until": UNTIL_D,
        "prev_since": PREV_SINCE_D, "prev_until": PREV_UNTIL_D,
    }
    assert body["staff_scope_id"] is None      # 管理层全团队口径
    assert body["method"] == ext.BOARD_METHOD


def test_single_group_error_degrades_without_dragging_others(monkeypatch):
    _frozen(monkeypatch)

    def boom(_params):
        raise RuntimeError("funnel exploded")

    conn = _FakeConn(routes={ext.FUNNEL_SQL: boom})
    body = _build(conn)
    assert body["funnel"]["status"] == "error"
    assert "funnel exploded" in body["funnel"]["reason"]
    for key in GROUP_KEYS:
        if key != "funnel":
            assert body[key]["status"] != "error", key
    assert body["status"] == "ready"           # 信封不因单组失败而炸


def test_staff_scope_params_pushed_down(monkeypatch):
    """员工 own-only:staff_scope_id 下推到收藏集条件 4 参;管理层 None → 0(全团队)。"""
    _frozen(monkeypatch)
    conn = _FakeConn()
    _build(conn, sid=7684)
    scoped_sqls = (
        ext.KOL_VIEWS_CURRENT_SQL,
        ext.FUNNEL_SQL,
        ext.PLATFORM_DIST_SQL,
        ext.CONTACT_TYPES_SQL,
        ext.CONTACT_COVERAGE_SQL,
        ext.VIEWS_TOP_SQL,
    )
    for sql in scoped_sqls:
        params = next(p for s, p in conn.calls if s == sql)
        assert params[:4] == (7684, 7684, 7684, 7684), sql
    recent = next(p for s, p in conn.calls if s == ext.RECENT_VIDEOS_SQL)
    assert recent[:3] == ext.VILTROX_TITLE_TOKENS
    assert recent[3:7] == (7684, 7684, 7684, 7684)
    official = next(p for s, p in conn.calls if s == ext.OFFICIAL_DAY_SQL)
    assert official[:2] == (7684, 7684)
    v_content_params = next(
        p for s, p in conn.calls if s == ext.V_CONTENT_SUMMARY_SQL
    )
    assert v_content_params[:3] == ext.VILTROX_TITLE_TOKENS
    assert v_content_params[3:7] == (7684, 7684, 7684, 7684)
    assert v_content_params[7:] == (ext.V_KOL_IDS_ROWS_LIMIT,)

    conn_all = _FakeConn()
    _build(conn_all, sid=None)
    for sql in scoped_sqls:
        params = next(p for s, p in conn_all.calls if s == sql)
        assert params[:4] == (0, 0, 0, 0), sql
    recent_all = next(p for s, p in conn_all.calls if s == ext.RECENT_VIDEOS_SQL)
    assert recent_all[3:7] == (0, 0, 0, 0)


def test_staff_a_b_content_metrics_are_isolated_while_global_fit_is_preserved(monkeypatch):
    """A/B 各只看收藏或被授权共享的 KOL；明确全池的 fit_dist 不随 staff 改变。"""
    _frozen(monkeypatch)

    def staff_id(params: tuple, offset: int = 0) -> int:
        scope = params[offset:offset + 4]
        assert len(scope) == 4 and len(set(scope)) == 1
        return int(scope[0])

    def current_views(params: tuple):
        sid = staff_id(params)
        return [{"total_evidence": 2, "measured": 2, "views_total": 1100 if sid == 101 else 2200}]

    def views_top(params: tuple):
        sid = staff_id(params)
        return [{
            "kol_pool_id": 1001 if sid == 101 else 2002,
            "display_name": "A shared creator" if sid == 101 else "B creator",
            "handle": "a_shared" if sid == 101 else "b_only",
            "platform": "youtube",
            "total_views": 1100 if sid == 101 else 2200,
            "video_count": 2,
        }]

    def contact_types(params: tuple):
        sid = staff_id(params)
        return [{"contact_type": "email" if sid == 101 else "website", "n": 1}]

    def coverage(params: tuple):
        sid = staff_id(params)
        return [{"total": 2 if sid == 101 else 1, "covered": 1}]

    def v_summary(params: tuple):
        sid = staff_id(params, 3)
        summary = {
            "total_evidence": 2 if sid == 101 else 1,
            "cooperation": 1,
            "analysis_confirmed": 0,
            "title_mention": 0,
            "not_related": 0,
            "undetermined": 1 if sid == 101 else 0,
            "v_kol_count": 1,
            "cooperation_kols": 1,
            "analysis_confirmed_kols": 0,
            "title_mention_kols": 0,
            "not_related_kols": 0,
            "undetermined_kols": 0,
        }
        kid = 1001 if sid == 101 else 2002
        return _v_summary_rows(summary, (kid,))

    routes = {
        ext.KOL_VIEWS_CURRENT_SQL: current_views,
        ext.VIEWS_TOP_SQL: views_top,
        ext.CONTACT_TYPES_SQL: contact_types,
        ext.CONTACT_COVERAGE_SQL: coverage,
        ext.V_CONTENT_SUMMARY_SQL: v_summary,
        ext.FIT_DIST_SQL: [{"bucket": 7, "n": 9}],
    }
    a = _build(_FakeConn(routes), sid=101)
    b = _build(_FakeConn(routes), sid=202)

    assert a["kpi_series"]["kol_views"]["views_total"] == 1100
    assert b["kpi_series"]["kol_views"]["views_total"] == 2200
    assert a["views_top"]["items"][0]["handle"] == "a_shared"
    assert b["views_top"]["items"][0]["handle"] == "b_only"
    assert a["contact_coverage"]["types"] == [{"contact_type": "email", "count": 1}]
    assert b["contact_coverage"]["types"] == [{"contact_type": "website", "count": 1}]
    assert a["v_content"]["v_kol_ids"] == [1001]
    assert b["v_content"]["v_kol_ids"] == [2002]
    assert a["v_content"]["scope"]["staff_scope_id"] == 101
    assert b["v_content"]["scope"]["staff_scope_id"] == 202
    assert a["fit_dist"] == b["fit_dist"]


def test_all_sql_readonly(monkeypatch):
    """整条链全 SELECT:纯读,零 INSERT/UPDATE/DELETE。"""
    _frozen(monkeypatch)
    conn = _FakeConn()
    _build(conn)
    assert conn.calls, "必须真的查询"
    for sql, _params in conn.calls:
        head = sql.strip().upper()
        assert head.startswith(("SELECT", "WITH"))
        for verb in ("INSERT ", "UPDATE ", "DELETE "):
            assert verb not in head
