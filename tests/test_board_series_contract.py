"""板块 KPI 按日时序(board_series · 挂账迸发①)契约测试 —— 零 DB 依赖(mock conn)。

覆盖点(照 test_voice_report_ext_contract 模式):
  1. SQL 常量静态审查:全参数化 ? / 日聚合与扫描 LIMIT 下推 / 零字面 percent、
     零 LIKE / 显示层宪法(个人字段与明文联系方式一列不进 SELECT);
  2. 逐板契约(每板 ≥3 断言):日轴 0 填齐 / delta 口径(上窗 0 → null)/
     窗口参数下推(环比同等流逝窗)/ 表名 basis / 参数板缺参 400 语义;
  3. dealers 全表 0 行 → 板级诚实 empty(零 0 填平线);
  4. launchpad 审批表未建 → 指标级诚实 empty(探针判缺);
  5. sku360 词表匹配计数(token 边界)+ SKU 解析不到 LookupError;
  6. creative 段级计数与 creative_segments._decompose_video 同口径;
  7. 单指标失败降级 {status:'error'} 不拖累同板其余指标;
  8. board 非法抛 ValueError,SQL 零执行;整链只读(零 INSERT/UPDATE/DELETE)。
红线:纯读契约,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.dashboard import board_series, board_series_param, board_series_sql as bsql  # noqa: E402


# ── mock conn(仿 test_voice_report_ext_contract._FakeConn,按 SQL 常量路由结果)──


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
    bsql.TABLE_PROBE_SQL,
    bsql.PROJECTS_NEW_DAY_SQL, bsql.PROJECTS_NEW_COUNT_SQL,
    bsql.STAGE_EVENTS_DAY_SQL, bsql.STAGE_EVENTS_COUNT_SQL,
    bsql.CONTENT_POSTED_DAY_SQL, bsql.CONTENT_POSTED_COUNT_SQL,
    bsql.ATTR_REVENUE_DAY_SQL, bsql.ATTR_REVENUE_SUM_SQL,
    bsql.EVENTS_NEW_DAY_SQL, bsql.EVENTS_NEW_COUNT_SQL,
    bsql.EVENTS_STARTED_DAY_SQL, bsql.EVENTS_STARTED_COUNT_SQL,
    bsql.EVENT_EXPENSES_DAY_SQL, bsql.EVENT_EXPENSES_SUM_SQL,
    bsql.KOL_EVIDENCE_NEW_DAY_SQL, bsql.KOL_EVIDENCE_NEW_COUNT_SQL,
    bsql.KOL_EVIDENCE_PUB_DAY_SQL, bsql.KOL_EVIDENCE_PUB_COUNT_SQL,
    bsql.INBOX_SUGGESTED_DAY_SQL, bsql.INBOX_SUGGESTED_COUNT_SQL,
    bsql.INBOX_EXECUTED_DAY_SQL, bsql.INBOX_EXECUTED_COUNT_SQL,
    bsql.CANDIDATES_DAY_SQL, bsql.CANDIDATES_COUNT_SQL,
    bsql.APPROVALS_DAY_SQL, bsql.APPROVALS_COUNT_SQL,
    bsql.SKU_PRODUCT_LOOKUP_SQL, bsql.SKU_ALIAS_LOOKUP_SQL,
    bsql.SKU_ALIASES_SQL, bsql.SKU_TITLE_DAY_SQL,
    bsql.CREATIVE_READY_DAY_SQL, bsql.CREATIVE_READY_COUNT_SQL,
    bsql.CREATIVE_SEGMENT_ROWS_SQL,
    bsql.DEALERS_TOTAL_SQL, bsql.DEALERS_NEW_DAY_SQL, bsql.DEALERS_NEW_COUNT_SQL,
)
ALL_SQL = "\n".join(ALL_SQL_CONSTANTS)

# 冻结 now = 2026-07-12 12:00 UTC → 30 天窗 [2026-06-13, 2026-07-12](今天为进行中日)
FROZEN_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
CUR_TZ = ("2026-06-13T00:00:00+00:00", FROZEN_NOW.isoformat())
PREV_TZ = ("2026-05-14T00:00:00+00:00", "2026-06-12T12:00:00+00:00")


@pytest.fixture()
def frozen_now(monkeypatch):
    monkeypatch.setattr(board_series, "_now_utc", lambda: FROZEN_NOW)


# ── 1. SQL 常量静态审查 ─────────────────────────────────────────────────


def test_sql_constants_parameterized_with_limit_pushdown():
    limited = tuple(sql for sql in ALL_SQL_CONSTANTS if "GROUP BY" in sql) + (
        bsql.SKU_TITLE_DAY_SQL, bsql.CREATIVE_SEGMENT_ROWS_SQL, bsql.SKU_ALIASES_SQL,
    )
    for sql in limited:
        assert "LIMIT ?" in sql
    for sql in ALL_SQL_CONSTANTS:
        if sql is bsql.DEALERS_TOTAL_SQL:
            continue  # 全表探针计数,无参数如实
        assert "?" in sql  # 全参数化,零拼接


def test_sql_compat_redlines_no_percent_no_like_no_comment():
    assert "%" not in ALL_SQL
    assert " LIKE " not in f" {ALL_SQL.upper()} ".replace("\n", " ")
    assert "--" not in ALL_SQL  # SQL 字符串零注释(compat ASCII 问号陷阱)


def test_sql_never_selects_private_columns():
    for forbidden in (
        "author_handle", "author_id", "raw_data_json", "author_avatar_url",
        "payload_json", "contact_value", "email", "viltrox_fit_score",
    ):
        assert forbidden not in ALL_SQL


def test_limit_cap_constants():
    assert bsql.SERIES_MAX_DAYS == 370
    assert bsql.SERIES_ROWS_LIMIT == 400
    assert bsql.DAYS_MAX == 365
    assert bsql.SKU_TITLE_SCAN_LIMIT == 6000
    assert bsql.CREATIVE_SCAN_LIMIT == 800


# ── 2. 信封 + 非法入参(SQL 零执行)──────────────────────────────────────


def test_unknown_board_raises_before_any_sql(frozen_now):
    conn = _FakeConn()
    with pytest.raises(ValueError):
        board_series.build_board_series("nonsense", conn=conn)
    assert conn.calls == []


def test_param_boards_require_params(frozen_now):
    conn = _FakeConn()
    with pytest.raises(ValueError):
        board_series.build_board_series("kol-profile", conn=conn)
    with pytest.raises(ValueError):
        board_series.build_board_series("sku360", conn=conn)
    assert conn.calls == []


def test_envelope_contract_and_days_clamp(frozen_now):
    body = board_series.build_board_series("projects", days=9999, conn=_FakeConn())
    assert set(body.keys()) == {
        "status", "board", "days", "window", "series", "metrics", "basis",
        "method", "generated_at",
    }
    assert body["days"] == bsql.DAYS_MAX  # days 封顶
    assert body["board"] == "projects"
    assert body["method"] == bsql.BOARD_SERIES_METHOD
    assert body["window"]["until"] == "2026-07-12"


# ── 3. projects:日轴 0 填齐 + delta 口径 + 窗口参数下推 ───────────────────


def test_projects_zero_fill_delta_and_window_pushdown(frozen_now):
    def new_count(params):
        # 本窗 6,上窗 4 → +50.0%
        return [{"n": 6}] if params[0] == CUR_TZ[0] else [{"n": 4}]

    def posted_count(params):
        # 本窗 5,上窗 0 → delta_pct=null(诚实省略药丸)
        return [{"n": 5}] if params[0] == CUR_TZ[0] else [{"n": 0}]

    conn = _FakeConn(routes={
        bsql.PROJECTS_NEW_DAY_SQL: [
            {"day": "2026-06-15", "n": 2},
            {"day": "2026-07-12", "n": 4},
        ],
        bsql.PROJECTS_NEW_COUNT_SQL: new_count,
        bsql.CONTENT_POSTED_COUNT_SQL: posted_count,
        bsql.ATTR_REVENUE_DAY_SQL: [{"day": "2026-07-01", "n": 12999}],
        bsql.ATTR_REVENUE_SUM_SQL: lambda params: (
            [{"n": 12999}] if params[0] == CUR_TZ[0] else [{"n": 0}]
        ),
    })
    body = board_series.build_board_series("projects", days=30, conn=conn)

    # 日轴:30 天连续 0 填齐,右沿=今天(进行中日),绝不产未来日
    series = body["series"]["projects_new"]
    assert len(series) == 30
    assert series[0] == {"date": "2026-06-13", "count": 0}
    assert series[2] == {"date": "2026-06-15", "count": 2}
    assert series[-1] == {"date": "2026-07-12", "count": 4}
    assert [p["date"] for p in series] == sorted({p["date"] for p in series})

    # delta 口径:上窗有数 → 百分比;上窗 0 → null
    m = body["metrics"]
    assert m["projects_new"] == {
        "status": "ready", "current": 6, "previous": 4, "delta_pct": 50.0,
        "table": "vkpi_projects", "unit": "rows",
    }
    assert m["content_posted"]["delta_pct"] is None
    assert m["content_posted"]["previous"] == 0

    # 归因金额:美分原样(unit=cents,point_key=value),换算美元是前端唯一换算点的事
    assert m["attribution_revenue_cents"]["unit"] == "cents"
    rev = body["series"]["attribution_revenue_cents"]
    assert {"date": "2026-07-01", "value": 12999} in rev
    assert m["attribution_revenue_cents"]["current"] == 12999
    assert m["attribution_revenue_cents"]["delta_pct"] is None  # 上窗 0

    # 窗口参数下推:序列查询右沿 = now;环比两侧同取已流逝等长时段
    day_calls = [c[1] for c in conn.calls if c[0] == bsql.PROJECTS_NEW_DAY_SQL]
    assert day_calls == [(CUR_TZ[0], CUR_TZ[1], bsql.SERIES_ROWS_LIMIT)]
    count_calls = [c[1] for c in conn.calls if c[0] == bsql.PROJECTS_NEW_COUNT_SQL]
    assert count_calls == [CUR_TZ, PREV_TZ]

    # basis 带真实表名
    assert "vkpi_project_stage_events" in body["basis"]["stage_advances"]
    assert "vkpi_sales_attributions" in body["basis"]["attribution_revenue_cents"]


# ── 4. events:三指标 + start_date 日窗参数 + 费用求和 ────────────────────


def test_events_metrics_and_date_window(frozen_now):
    conn = _FakeConn(routes={
        bsql.EVENTS_STARTED_DAY_SQL: [{"day": "2026-07-08", "n": 3}],
        bsql.EVENTS_STARTED_COUNT_SQL: lambda params: (
            [{"n": 3}] if params[0] == "2026-06-13" else [{"n": 1}]
        ),
        bsql.EVENT_EXPENSES_DAY_SQL: [{"day": "2026-06-20", "n": 200}],
        bsql.EVENT_EXPENSES_SUM_SQL: [{"n": 200}],
    })
    body = board_series.build_board_series("events", days=30, conn=conn)
    assert set(body["series"].keys()) == {"events_new", "events_started", "event_expense_amount"}

    # start_date 日列:窗口按日字符串下推(闭区间),不是时间戳
    started_calls = [c[1] for c in conn.calls if c[0] == bsql.EVENTS_STARTED_DAY_SQL]
    assert started_calls == [("2026-06-13", "2026-07-12", bsql.SERIES_ROWS_LIMIT)]
    assert body["metrics"]["events_started"]["delta_pct"] == 200.0  # 3 vs 1

    # 费用求和序列(point_key=value,整数原样)
    exp = body["series"]["event_expense_amount"]
    assert {"date": "2026-06-20", "value": 200} in exp
    assert body["metrics"]["event_expense_amount"]["unit"] == "amount"
    assert "vkpi_event_expenses" in body["basis"]["event_expense_amount"]


# ── 5. kol-profile:kol_id 参数下推(naive 窗)+ 双序列 ───────────────────


def test_kol_profile_pushes_kol_id_and_naive_window(frozen_now):
    conn = _FakeConn(routes={
        bsql.KOL_EVIDENCE_NEW_DAY_SQL: [{"day": "2026-07-10", "n": 7}],
        bsql.KOL_EVIDENCE_NEW_COUNT_SQL: [{"n": 7}],
    })
    body = board_series.build_board_series("kol-profile", days=30, kol_id=42, conn=conn)
    assert body["params"] == {"kol_id": 42}
    assert set(body["series"].keys()) == {"evidence_new", "evidence_published"}

    # kol_id 前置参数 + naive 窗(evidence.created_at 无时区列,库内约定 UTC)
    day_calls = [c[1] for c in conn.calls if c[0] == bsql.KOL_EVIDENCE_NEW_DAY_SQL]
    assert day_calls == [(42, "2026-06-13T00:00:00", "2026-07-12T12:00:00", bsql.SERIES_ROWS_LIMIT)]

    # 发布序列走 timestamptz 窗
    pub_calls = [c[1] for c in conn.calls if c[0] == bsql.KOL_EVIDENCE_PUB_COUNT_SQL]
    assert pub_calls == [(42, *CUR_TZ), (42, *PREV_TZ)]
    assert body["series"]["evidence_new"][-3] == {"date": "2026-07-10", "count": 7}
    assert body["metrics"]["evidence_new"]["table"] == "vkpi_kol_video_evidence"


# ── 6. autonomy:建议/执行双序列 + executed 状态参数化 ────────────────────


def test_autonomy_suggested_and_executed(frozen_now):
    conn = _FakeConn(routes={
        bsql.INBOX_SUGGESTED_DAY_SQL: [{"day": "2026-07-11", "n": 5}],
        bsql.INBOX_SUGGESTED_COUNT_SQL: lambda params: (
            [{"n": 5}] if params[0] == CUR_TZ[0] else [{"n": 10}]
        ),
        bsql.INBOX_EXECUTED_DAY_SQL: [{"day": "2026-07-11", "n": 2}],
        bsql.INBOX_EXECUTED_COUNT_SQL: [{"n": 2}],
    })
    body = board_series.build_board_series("autonomy", days=30, conn=conn)
    assert set(body["series"].keys()) == {"inbox_suggested", "inbox_executed"}
    assert body["metrics"]["inbox_suggested"]["delta_pct"] == -50.0  # 5 vs 10

    # executed 状态经参数下推(零拼接);basis 如实注明 updated_at 近似口径
    exec_calls = [c[1] for c in conn.calls if c[0] == bsql.INBOX_EXECUTED_DAY_SQL]
    assert exec_calls == [(bsql.INBOX_EXECUTED_STATUS, CUR_TZ[0], CUR_TZ[1], bsql.SERIES_ROWS_LIMIT)]
    assert "updated_at" in body["basis"]["inbox_executed"]
    assert body["series"]["inbox_executed"][-2] == {"date": "2026-07-11", "count": 2}


# ── 7. launchpad:候选序列 + 审批表未建诚实 empty ─────────────────────────


def test_launchpad_candidates_ready_and_approvals_probe_missing(frozen_now):
    conn = _FakeConn(routes={
        bsql.CANDIDATES_DAY_SQL: [{"day": "2026-07-12", "n": 4}],
        bsql.CANDIDATES_COUNT_SQL: [{"n": 4}],
        # TABLE_PROBE_SQL 未配 → 探针空 → 审批表未建
    })
    body = board_series.build_board_series("launchpad", days=30, conn=conn)
    assert body["series"]["content_candidates"][-1] == {"date": "2026-07-12", "count": 4}
    assert body["metrics"]["content_candidates"]["status"] == "ready"

    approvals = body["metrics"]["publish_approvals"]
    assert approvals["status"] == "empty"
    assert "迁移 173" in approvals["reason"]
    assert body["series"]["publish_approvals"] == []
    # 表未建时审批窗口 SQL 一条不发
    assert all(c[0] != bsql.APPROVALS_DAY_SQL for c in conn.calls)


def test_launchpad_approvals_ready_when_table_exists(frozen_now):
    conn = _FakeConn(routes={
        bsql.TABLE_PROBE_SQL: [{"table_name": bsql.APPROVALS_TABLE}],
        bsql.APPROVALS_DAY_SQL: [{"day": "2026-07-01", "n": 1}],
        bsql.APPROVALS_COUNT_SQL: [{"n": 1}],
    })
    body = board_series.build_board_series("launchpad", days=30, conn=conn)
    assert body["metrics"]["publish_approvals"]["status"] == "ready"
    assert {"date": "2026-07-01", "count": 1} in body["series"]["publish_approvals"]


# ── 8. sku360:解析 + 词表匹配计数(token 边界)+ 解析不到 404 ─────────────


def test_sku360_resolves_and_counts_title_matches(frozen_now):
    def titles(params):
        if params[0] == CUR_TZ[0]:
            return [
                {"day": "2026-07-10", "title": "VILTROX AF 85mm f1.8 review"},
                {"day": "2026-07-10", "title": "Best budget lens AF 85mm F1.8"},
                {"day": "2026-07-11", "title": "totally unrelated vlog"},
                # token 边界:'af 85mm f18x' 不是词边界命中,绝不误计
                {"day": "2026-07-11", "title": "af 85mm f18x fake"},
            ]
        return [{"day": "2026-05-20", "title": "viltrox af 85mm f1.8 first look"}]

    conn = _FakeConn(routes={
        bsql.SKU_PRODUCT_LOOKUP_SQL: [{"sku": "AF 85/1.8 STM"}],
        bsql.SKU_ALIASES_SQL: [
            {"alias_norm": "af 85mm f18", "confidence": 0.9},
            {"alias_norm": "low", "confidence": 0.1},  # 置信度/长度闸挡掉
        ],
        bsql.SKU_TITLE_DAY_SQL: titles,
    })
    body = board_series.build_board_series("sku360", days=30, sku="af 85/1.8 stm", conn=conn)
    assert body["params"]["resolved_sku"] == "AF 85/1.8 STM"
    assert body["params"]["alias_terms"] == 1  # 低置信别名被过滤

    series = {p["date"]: p["count"] for p in body["series"]["sku_mentions"]}
    assert series["2026-07-10"] == 2
    assert series["2026-07-11"] == 0  # 无关标题 + 非词边界都不计
    m = body["metrics"]["sku_mentions"]
    assert m["current"] == 2 and m["previous"] == 1 and m["delta_pct"] == 100.0
    assert m["scanned"] == 4
    # 标题文本绝不出参(只出计数)
    assert "review" not in repr(body)


def test_sku360_unknown_sku_raises_lookup(frozen_now):
    conn = _FakeConn()  # 产品/别名查询全空
    with pytest.raises(LookupError):
        board_series.build_board_series("sku360", days=30, sku="no-such-sku", conn=conn)


# ── 9. creative:段级计数同口径 + 深析新增序列 ───────────────────────────


FINAL_V1_RESULT = {
    "layer1_visual_content": {
        "scene_timeline": [
            {"what": "opening product shot", "timestamp": "0:00", "why_it_matters": "hook"},
            {"what": "bokeh comparison", "timestamp": "0:31"},
        ],
        "product_presence": "lens on camera most of the video",
        "brand_exposure": "logo visible",
    },
    "layer2_viewer_emotion": {"first_three_seconds_feeling": "curiosity"},
}


def test_creative_segment_counting_matches_decompose_caliber(frozen_now):
    # 同口径核对:opening 1 + scene 2 + product_exposure 1 = 4 段
    from app.domains.content.creative_segments import _decompose_video

    expected = len(_decompose_video({"evidence_id": 1, "result": FINAL_V1_RESULT}))
    assert board_series_param._segment_count(FINAL_V1_RESULT) == expected == 4

    conn = _FakeConn(routes={
        bsql.CREATIVE_READY_DAY_SQL: [{"day": "2026-07-09", "n": 1}],
        bsql.CREATIVE_READY_COUNT_SQL: [{"n": 1}],
        bsql.CREATIVE_SEGMENT_ROWS_SQL: lambda params: (
            [{"day": "2026-07-09", "result": FINAL_V1_RESULT}] if params[1] == CUR_TZ[0] else []
        ),
    })
    body = board_series.build_board_series("creative", days=30, conn=conn)
    seg = {p["date"]: p["count"] for p in body["series"]["segments_new"]}
    assert seg["2026-07-09"] == 4
    assert body["metrics"]["segments_new"]["current"] == 4
    assert body["metrics"]["segments_new"]["previous"] == 0
    assert body["metrics"]["segments_new"]["delta_pct"] is None  # 上窗 0 → null
    assert body["series"]["deep_videos_new"][-4] == {"date": "2026-07-09", "count": 1}
    assert "video_analysis_final_v1" in body["basis"]["deep_videos_new"]


# ── 10. dealers:全表 0 行诚实 empty / 有数据照常 ─────────────────────────


def test_dealers_zero_rows_is_honest_empty(frozen_now):
    conn = _FakeConn(routes={bsql.DEALERS_TOTAL_SQL: [{"n": 0}]})
    body = board_series.build_board_series("dealers", days=30, conn=conn)
    assert body["status"] == "empty"
    assert "0 行" in body["reason"]
    assert body["series"]["dealers_new"] == []  # 绝不 0 填平线冒充有数据流
    assert body["metrics"]["dealers_new"]["status"] == "empty"
    # 空表时窗口序列 SQL 一条不发
    assert all(c[0] != bsql.DEALERS_NEW_DAY_SQL for c in conn.calls)


def test_dealers_with_rows_builds_series(frozen_now):
    conn = _FakeConn(routes={
        bsql.DEALERS_TOTAL_SQL: [{"n": 3}],
        bsql.DEALERS_NEW_DAY_SQL: [{"day": "2026-07-02", "n": 3}],
        bsql.DEALERS_NEW_COUNT_SQL: [{"n": 3}],
    })
    body = board_series.build_board_series("dealers", days=30, conn=conn)
    assert body["status"] == "ready"
    assert {"date": "2026-07-02", "count": 3} in body["series"]["dealers_new"]
    assert body["metrics"]["dealers_new"]["table"] == "vkpi_dealers"


# ── 11. 单指标失败降级 + 整链只读 ─────────────────────────────────────────


def test_single_metric_failure_degrades_without_killing_board(frozen_now):
    def boom(params):
        raise RuntimeError("db exploded")

    conn = _FakeConn(routes={
        bsql.STAGE_EVENTS_DAY_SQL: boom,
        bsql.PROJECTS_NEW_DAY_SQL: [{"day": "2026-07-12", "n": 1}],
        bsql.PROJECTS_NEW_COUNT_SQL: [{"n": 1}],
    })
    body = board_series.build_board_series("projects", days=30, conn=conn)
    assert body["status"] == "ready"
    assert body["metrics"]["stage_advances"]["status"] == "error"
    assert "db exploded" in body["metrics"]["stage_advances"]["reason"]
    assert body["series"]["stage_advances"] == []
    # 同板其余指标照常
    assert body["metrics"]["projects_new"]["status"] == "ready"
    assert body["series"]["projects_new"][-1] == {"date": "2026-07-12", "count": 1}


def test_whole_chain_is_readonly(frozen_now):
    boards = ("projects", "events", "autonomy", "launchpad", "creative", "dealers")
    conn = _FakeConn(routes={bsql.DEALERS_TOTAL_SQL: [{"n": 1}]})
    for key in boards:
        board_series.build_board_series(key, days=7, conn=conn)
    board_series.build_board_series("kol-profile", days=7, kol_id=1, conn=conn)
    import re

    assert conn.calls, "必须真的查询"
    for sql, _params in conn.calls:
        head = sql.strip().upper()
        assert head.startswith("SELECT")
        # 词边界判定(UPDATED_AT 列名不是 UPDATE 动词)
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", head)
