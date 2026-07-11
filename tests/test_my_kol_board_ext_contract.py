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
  9. v_content 三档判定:SQL 汇总口径 + classify_v_content 纯函数单测;
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
    ext.V_CONTENT_SQL,
    ext.V_KOL_COUNT_SQL,
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
    "contact_coverage", "views_top", "v_content",
)


def _build(conn, sid=None, days=30, monkeypatch=None):
    return ext.build_board_ext(conn, staff_scope_id=sid, days=days)


def _frozen(monkeypatch):
    monkeypatch.setattr(ext, "_now_utc", lambda: FROZEN_NOW)


# ── 1. SQL 常量静态审查 ─────────────────────────────────────────────────


def test_sql_constants_parameterized_with_limit_pushdown():
    limited = (
        ext.POOL_FOLLOWERS_DAY_SQL, ext.NEW_VIDEOS_DAY_SQL, ext.OFFICIAL_DAY_SQL,
        ext.FUNNEL_SQL, ext.PLATFORM_DIST_SQL, ext.FIT_DIST_SQL,
        ext.CONTACT_TYPES_SQL, ext.VIEWS_TOP_SQL,
    )
    for sql in limited:
        assert "LIMIT ?" in sql
    # 全池点时两条(KOL_VIEWS_CURRENT)外,其余全参数化
    for sql in ALL_SQL_CONSTANTS:
        if sql is ext.KOL_VIEWS_CURRENT_SQL:
            continue
        assert "?" in sql


def test_sql_compat_redlines_no_percent_no_like():
    """compat 红线:SQL 字符串零字面 percent;标题命中走 strpos 参数化,不用 LIKE。"""
    assert "%" not in ALL_SQL
    assert " LIKE " not in f" {ALL_SQL.upper()} ".replace("\n", " ")
    assert "strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?)" in ext.V_CONTENT_SQL
    assert "strpos" in ext.V_KOL_COUNT_SQL


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
    })
    flat = repr(_build(conn))
    for leaked in (
        "secret@example.com", "secret2@example.com", "+86-13800000000",
        "88.5", "contact_value", "viltrox_fit_score",
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
    assert by_sql[ext.VIEWS_TOP_SQL] == (ext.VIEWS_TOP_LIMIT,)
    assert by_sql[ext.PLATFORM_DIST_SQL][-1] == ext.PLATFORM_ROWS_LIMIT
    assert by_sql[ext.FUNNEL_SQL][-1] == ext.FUNNEL_ROWS_LIMIT
    assert by_sql[ext.FIT_DIST_SQL] == (ext.FIT_BUCKET_ROWS_LIMIT,)
    assert by_sql[ext.CONTACT_TYPES_SQL] == (ext.CONTACT_TYPE_ROWS_LIMIT,)


# ── 9. v_content 三档判定 ───────────────────────────────────────────────


def test_v_content_tiers_math(monkeypatch):
    _frozen(monkeypatch)
    conn = _FakeConn(routes={
        ext.V_CONTENT_SQL: [
            {"total_evidence": 2769, "cooperation": 806,
             "title_mention": 376, "overlap_both": 103},
        ],
        ext.V_KOL_COUNT_SQL: [{"n": 387}],
    })
    vc = _build(conn)["v_content"]
    assert vc["status"] == "ready"
    assert vc["total_evidence"] == 2769
    assert vc["v_kol_count"] == 387
    assert vc["tiers"] == {
        "cooperation": 806,
        "title_mention": 376,          # 任意标题命中(含与 cooperation 重叠)
        "title_mention_only": 273,     # 376 - 103
        "overlap_both": 103,
        "undetermined": 1690,          # 2769 - 806 - 273
    }
    # SQL 参数=词元常量(strpos 参数化,不区分大小写靠 lower)
    by_sql = {sql: params for sql, params in conn.calls}
    assert by_sql[ext.V_CONTENT_SQL] == (ext.VILTROX_TOKEN, ext.VILTROX_TOKEN)
    assert by_sql[ext.V_KOL_COUNT_SQL] == (ext.VILTROX_TOKEN,)


def test_classify_v_content_pure_function():
    """三档判定纯函数(M3 详情弹窗 / videos 增强复用):合作优先 > 标题命中 > 待定。"""
    assert ext.classify_v_content(123, "random title") == "cooperation"
    assert ext.classify_v_content(123, "VILTROX AF 85") == "cooperation"   # 合作优先
    assert ext.classify_v_content(None, "My VILTROX 85mm review") == "title_mention"
    assert ext.classify_v_content(None, "viltrox in lowercase") == "title_mention"
    assert ext.classify_v_content(None, "Sony FE 50mm review") == "undetermined"
    assert ext.classify_v_content(None, None) == "undetermined"
    assert ext.classify_v_content("", "") == "undetermined"
    assert ext.classify_v_content(0, "no project zero id") == "undetermined"


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
    scoped_sqls = (ext.FUNNEL_SQL, ext.PLATFORM_DIST_SQL, ext.CONTACT_COVERAGE_SQL)
    for sql in scoped_sqls:
        params = next(p for s, p in conn.calls if s == sql)
        assert params[:4] == (7684, 7684, 7684, 7684), sql
    official = next(p for s, p in conn.calls if s == ext.OFFICIAL_DAY_SQL)
    assert official[:2] == (7684, 7684)

    conn_all = _FakeConn()
    _build(conn_all, sid=None)
    for sql in scoped_sqls:
        params = next(p for s, p in conn_all.calls if s == sql)
        assert params[:4] == (0, 0, 0, 0), sql


def test_all_sql_readonly(monkeypatch):
    """整条链全 SELECT:纯读,零 INSERT/UPDATE/DELETE。"""
    _frozen(monkeypatch)
    conn = _FakeConn()
    _build(conn)
    assert conn.calls, "必须真的查询"
    for sql, _params in conn.calls:
        head = sql.strip().upper()
        assert head.startswith("SELECT")
        for verb in ("INSERT ", "UPDATE ", "DELETE "):
            assert verb not in head
