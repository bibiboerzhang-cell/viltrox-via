"""市场之声报表增强字段(voice_report_ext · V0h)契约测试 —— 零 DB 依赖(mock conn)。

覆盖点(施工单验收口径):
  1. SQL 常量静态审查:参数化 ? / LIMIT 下推 / 零字面 percent、零 LIKE /
     显示层宪法(author_handle、author_id、raw_data_json、author_avatar_url 绝不入 SELECT);
  2. kpi_series 日轴 0 填齐:稀疏 GROUP BY 行 → 连续日轴,缺数日 =0;
  3. kpi_prev delta null 口径:上窗 0 → delta_pct=null;上窗有数 → 正确百分比;
  4. sentiment_summary share 计算:总量/分期 share,空期 share=null,粒度=day(≤35 天窗);
  5. alerts 只读不写:整链全 SELECT,零 INSERT/UPDATE/DELETE;评估六类契约键齐;
  6. 私字段不外泄:行里混入 author_handle / payload_json 也绝不进响应;
  7. LIMIT 双层封顶:SQL 参数=模块常量,Python 层切片二次封顶;
  8. month 非法抛 ValueError(路由转 400),SQL 零执行。
红线:纯读契约,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.market import voice_alerts, voice_report_ext  # noqa: E402
from app.domains.market.market_voice import _window  # noqa: E402


# ── mock conn(仿 test_voice_feed_contract._FakeConn,按 SQL 常量路由结果)──


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
    voice_report_ext.COMMENTS_DAY_SQL,
    voice_report_ext.QUEUE_DAY_SQL,
    voice_report_ext.COMMENTS_COUNT_SQL,
    voice_report_ext.QUEUE_COUNT_SQL,
    voice_report_ext.SENTIMENT_DAY_SQL,
    voice_report_ext.PLATFORM_DIST_SQL,
    voice_report_ext.LINE_VOICE_SELECT_SQL,
    voice_report_ext.RECENT_ALERTS_SQL,
    # V0i 增量三组(topics / language_dist / competitor_voice)也进红线静态审查
    voice_report_ext.TOPIC_TEXT_SQL,
    voice_report_ext.LANGUAGE_DIST_SQL,
    voice_report_ext.COMPETITOR_VOICE_SQL,
)
ALL_SQL = "\n".join(ALL_SQL_CONSTANTS)

# 2026-06 自然月窗口(UTC),与 voice-report 同口径
SINCE_0606, UNTIL_0606, _LABEL_0606 = _window("2026-06")


# ── 1. SQL 常量静态审查 ─────────────────────────────────────────────────


def test_sql_constants_parameterized_with_limit_pushdown():
    limited = (
        voice_report_ext.COMMENTS_DAY_SQL,
        voice_report_ext.QUEUE_DAY_SQL,
        voice_report_ext.SENTIMENT_DAY_SQL,
        voice_report_ext.PLATFORM_DIST_SQL,
        voice_report_ext.LINE_VOICE_SELECT_SQL,
        voice_report_ext.RECENT_ALERTS_SQL,
        voice_report_ext.TOPIC_TEXT_SQL,
        voice_report_ext.LANGUAGE_DIST_SQL,
        voice_report_ext.COMPETITOR_VOICE_SQL,
    )
    for sql in limited:
        assert "LIMIT ?" in sql
    for sql in ALL_SQL_CONSTANTS:
        assert "?" in sql  # 全参数化,零拼接


def test_sql_compat_redlines_no_percent_no_like():
    """compat 红线:SQL 字符串零字面 percent;前缀匹配走 strpos 参数化,不用 LIKE。"""
    assert "%" not in ALL_SQL
    assert " LIKE " not in f" {ALL_SQL.upper()} ".replace("\n", " ")
    assert "strpos(dedupe_key, ?)" in voice_report_ext.RECENT_ALERTS_SQL


def test_sql_never_selects_private_columns():
    """显示层宪法:个人/内部字段一列不进任何 SELECT。"""
    for forbidden in ("author_handle", "author_id", "raw_data_json", "author_avatar_url", "payload_json"):
        assert forbidden not in ALL_SQL


def test_limit_cap_constants():
    assert voice_report_ext.SERIES_MAX_DAYS == 370
    assert voice_report_ext.PLATFORM_MAX_ITEMS == 20
    assert voice_report_ext.RECENT_ALERTS_LIMIT == 10
    assert voice_report_ext.LINE_SCAN_LIMIT == 5000


# ── 2. kpi_series 日轴 0 填齐 ────────────────────────────────────────────


def test_kpi_series_zero_fills_continuous_day_axis():
    conn = _FakeConn(routes={
        voice_report_ext.COMMENTS_DAY_SQL: [
            {"day": "2026-06-03", "n": 5},
            {"day": "2026-06-10", "n": 2},
        ],
        # queue 稀疏单日
        voice_report_ext.QUEUE_DAY_SQL: [{"day": "2026-06-30", "n": 7}],
    })
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    series = body["kpi_series"]["series"]

    comments = series["comments"]
    assert len(comments) == 30  # 2026-06 整月连续日轴
    assert comments[0] == {"date": "2026-06-01", "count": 0}
    assert comments[2] == {"date": "2026-06-03", "count": 5}
    assert comments[9] == {"date": "2026-06-10", "count": 2}
    assert comments[-1] == {"date": "2026-06-30", "count": 0}
    assert [c["date"] for c in comments] == sorted({c["date"] for c in comments})
    assert sum(c["count"] for c in comments) == 7

    queue = series["queue"]
    assert len(queue) == 30
    assert queue[-1] == {"date": "2026-06-30", "count": 7}
    assert sum(q["count"] for q in queue) == 7


def test_kpi_series_window_params_pushed_down():
    conn = _FakeConn()
    voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    day_calls = [c for c in conn.calls if c[0] == voice_report_ext.COMMENTS_DAY_SQL]
    assert day_calls, "comments 日计数 SQL 必须执行"
    params = day_calls[0][1]
    assert params == (SINCE_0606, UNTIL_0606, voice_report_ext.SERIES_ROWS_LIMIT)


# ── 3. kpi_prev delta 口径 ──────────────────────────────────────────────


def test_kpi_prev_delta_pct_and_null_when_prev_zero():
    def comments_count(params):
        # 本窗(since=2026-06-01)150,上窗(2026-05-01)100 → +50.0%
        return [{"n": 150}] if params[0] == SINCE_0606 else [{"n": 100}]

    def queue_count(params):
        # 本窗 5,上窗 0 → delta_pct=null(诚实省略药丸)
        return [{"n": 5}] if params[0] == SINCE_0606 else [{"n": 0}]

    conn = _FakeConn(routes={
        voice_report_ext.COMMENTS_COUNT_SQL: comments_count,
        voice_report_ext.QUEUE_COUNT_SQL: queue_count,
    })
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    prev = body["kpi_prev"]
    assert prev["status"] == "ready"
    assert prev["metrics"]["comments"] == {
        "current": 150, "previous": 100, "delta_pct": 50.0, "table": "vkpi_comments",
    }
    assert prev["metrics"]["queue"]["delta_pct"] is None
    assert prev["metrics"]["queue"]["previous"] == 0
    # 上窗 = 等长(2026-06 有 30 天 → 上窗 2026-05-02 起,严格等长不取整月)
    assert prev["window_prev"]["until"] == SINCE_0606


# ── 4. sentiment_summary share 计算 ─────────────────────────────────────


def test_sentiment_summary_shares_and_trend_null_on_empty_days():
    conn = _FakeConn(routes={
        voice_report_ext.SENTIMENT_DAY_SQL: [
            {"day": "2026-06-01", "sentiment": "positive", "n": 8},
            {"day": "2026-06-01", "sentiment": "negative", "n": 2},
            {"day": "2026-06-02", "sentiment": "neutral", "n": 5},
        ],
    })
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    summary = body["sentiment_summary"]
    assert summary["status"] == "ready"
    assert summary["done_total"] == 15
    assert summary["pos_total"] == 8 and summary["neg_total"] == 2 and summary["neu_total"] == 5
    assert summary["pos_share"] == round(8 / 15, 4)
    assert summary["neg_share"] == round(2 / 15, 4)
    assert summary["granularity"] == "day"  # 30 天窗 ≤35 → 日粒度

    trend = summary["trend"]
    assert len(trend) == 30
    assert trend[0] == {"period_start": "2026-06-01", "total": 10, "pos_share": 0.8, "neg_share": 0.2}
    # 06-02 全中性:share 是 0.0(分母>0),不是 null
    assert trend[1] == {"period_start": "2026-06-02", "total": 5, "pos_share": 0.0, "neg_share": 0.0}
    # 空日:total=0,share=null(诚实空,不编 0%)
    assert trend[2] == {"period_start": "2026-06-03", "total": 0, "pos_share": None, "neg_share": None}


def test_sentiment_summary_empty_window_is_honest():
    conn = _FakeConn()
    summary = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["sentiment_summary"]
    assert summary["status"] == "empty"
    assert summary["done_total"] == 0
    assert summary["pos_share"] is None and summary["neg_share"] is None
    assert "reason" in summary
    assert len(summary["trend"]) == 30 and all(t["total"] == 0 for t in summary["trend"])


# ── 5. alerts_state 只读不写 ────────────────────────────────────────────


def test_alerts_state_readonly_and_category_contract():
    conn = _FakeConn(routes={
        voice_report_ext.RECENT_ALERTS_SQL: [
            {
                "id": 9, "dedupe_key": "voice_alert:price:2026-07-10",
                "title": "声量告警 · 「价格」", "priority": "high",
                "status": "suggested", "created_at": "2026-07-10T09:00:00+00:00",
                # 行里混入内部列也不许泄漏
                "payload_json": {"secret_payload": "never_show"},
            },
        ],
    })
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)

    # 整条链全 SELECT:纯读,零 INSERT/UPDATE/DELETE(alerts evaluate 也只 SELECT)
    assert conn.calls, "必须真的查询"
    for sql, _params in conn.calls:
        head = sql.strip().upper()
        assert head.startswith("SELECT")
        for verb in ("INSERT", "UPDATE", "DELETE"):
            assert verb not in head

    state = body["alerts_state"]
    assert state["status"] == "ok"
    assert state["window_hours"] == voice_alerts.WINDOW_HOURS_DEFAULT
    assert state["threshold"] == voice_alerts.THRESHOLD_DEFAULT
    assert len(state["categories"]) == 6  # COMPLAINT_CATEGORIES 六类全量(含 0 分)
    for cat in state["categories"]:
        assert set(cat.keys()) == {
            "category", "label", "negative_count", "score",
            "threshold", "triggered", "window_hours",
        }
        assert cat["triggered"] is False  # mock 空评论 → 无触发

    pushed = state["recent_pushed"]
    assert len(pushed) == 1
    assert set(pushed[0].keys()) == {"id", "dedupe_key", "title", "priority", "status", "created_at"}
    assert pushed[0]["dedupe_key"] == "voice_alert:price:2026-07-10"
    assert pushed[0]["created_at"] == "2026-07-10T09:00:00Z"  # ISO UTC Z 后缀


# ── 6. 私字段不外泄 ─────────────────────────────────────────────────────


def test_response_never_leaks_private_fields():
    conn = _FakeConn(routes={
        voice_report_ext.LINE_VOICE_SELECT_SQL: [
            {
                "comment_text": "Great lens, autofocus is fast.",
                "sentiment": "positive",
                # 内部列混进行里也不许泄漏(显示层宪法回归)
                "author_handle": "should_never_leak",
                "author_id": "u-999",
                "raw_data_json": "{}",
            },
        ],
        voice_report_ext.RECENT_ALERTS_SQL: [
            {
                "id": 1, "dedupe_key": "voice_alert:price:2026-07-10", "title": "t",
                "priority": "high", "status": "suggested", "created_at": "2026-07-10T09:00:00Z",
                "payload_json": {"quotes": [{"secret": "payload_never_show"}]},
            },
        ],
    })
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    flat = repr(body)
    for leaked in (
        "should_never_leak", "u-999", "author_handle", "author_id",
        "raw_data_json", "payload_never_show", "payload_json",
    ):
        assert leaked not in flat


# ── 7. LIMIT 双层封顶 ───────────────────────────────────────────────────


def test_limits_pushed_down_and_python_side_capped():
    over_platforms = [{"platform": f"p{i}", "n": i} for i in range(40)]     # 超量喂 40
    over_alerts = [
        {"id": i, "dedupe_key": f"voice_alert:x:{i}", "title": "t",
         "priority": "low", "status": "suggested", "created_at": "2026-07-10T00:00:00Z"}
        for i in range(25)
    ]
    conn = _FakeConn(routes={
        voice_report_ext.PLATFORM_DIST_SQL: over_platforms,
        voice_report_ext.RECENT_ALERTS_SQL: over_alerts,
    })
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)

    # Python 层二次封顶(哪怕 SQL 层被 mock 放水)
    assert len(body["platform_dist"]["items"]) == voice_report_ext.PLATFORM_MAX_ITEMS
    assert len(body["alerts_state"]["recent_pushed"]) == voice_report_ext.RECENT_ALERTS_LIMIT

    # SQL 层 LIMIT 参数 = 模块常量
    by_sql = {sql: params for sql, params in conn.calls}
    assert by_sql[voice_report_ext.PLATFORM_DIST_SQL][-1] == voice_report_ext.PLATFORM_ROWS_LIMIT
    assert by_sql[voice_report_ext.RECENT_ALERTS_SQL] == (
        voice_report_ext.ALERT_DEDUPE_PREFIX, voice_report_ext.RECENT_ALERTS_LIMIT,
    )
    assert by_sql[voice_report_ext.LINE_VOICE_SELECT_SQL][-1] == voice_report_ext.LINE_SCAN_LIMIT
    assert by_sql[voice_report_ext.SENTIMENT_DAY_SQL][-1] == voice_report_ext.SENTIMENT_ROWS_LIMIT


# ── 8. 信封契约 + 非法 month ────────────────────────────────────────────


def test_response_envelope_contract_keys():
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=_FakeConn())
    assert set(body.keys()) == {
        "status", "month", "window",
        "kpi_series", "kpi_prev", "sentiment_summary",
        "alerts_state", "platform_dist", "line_voice",
        "topics", "language_dist", "competitor_voice",
        "method", "generated_at",
    }
    assert body["status"] == "ready"
    assert body["month"] == "2026-06"
    assert body["window"] == {"since": SINCE_0606, "until": UNTIL_0606, "label": _LABEL_0606}
    assert body["method"] == voice_report_ext.EXT_METHOD


def test_invalid_month_raises_before_any_sql():
    conn = _FakeConn()
    with pytest.raises(ValueError):
        voice_report_ext.voice_report_ext(month="garbage", conn=conn)
    assert conn.calls == []  # 非法值挡在 SQL 之前(路由转 400)


def test_line_voice_pos_share_null_without_annotation():
    conn = _FakeConn(routes={
        voice_report_ext.LINE_VOICE_SELECT_SQL: [
            {"comment_text": "This lens autofocus is bad", "sentiment": "negative"},
            {"comment_text": "lens bokeh looks great", "sentiment": ""},   # 未批注
            {"comment_text": "need a better light for studio", "sentiment": None},  # 未批注
        ],
    })
    items = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["line_voice"]["items"]
    by_key = {i["key"]: i for i in items}

    af = by_key["af_lens"]  # 词表含 lens/autofocus/bokeh
    assert af["count"] == 2
    assert af["annotated"] == 1
    assert af["pos_share"] == 0.0  # 1 条已批注且是 negative → 0.0,不是 null

    lighting = by_key["lighting"]  # 命中 light,但零批注 → pos_share=null
    assert lighting["count"] == 1
    assert lighting["annotated"] == 0
    assert lighting["pos_share"] is None
