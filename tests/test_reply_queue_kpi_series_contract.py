"""ReplyQueue kpi_series(按日入队/回复时序)契约测试(零 DB 依赖)。

断言面(voice_report_ext kpi_series 同模式):
  1. 契约形状:status/granularity/days/window/series/prev/basis 齐全;五度量
     (enqueued/pending/drafted/replied/price)全在;
  2. 日轴口径:连续 UTC 日 0 填齐;右沿钳 now(冻结 _now_utc,末日=今天,零未来日);
     计数落在正确日期上;enqueued=按日全状态求和;
  3. 环比口径:current=本窗日序列求和;previous=上一等长窗同口径 COUNT;
     上窗 0 → delta_pct=null(诚实无药丸,绝不编百分比);上窗>0 → 真百分比;
  4. 双层封顶:days>180 钳 SERIES_MAX_DAYS;days=0 回落缺省 30;
  5. 诚实空态:队列表未建 → status=empty + reason,series 全空数组;
  6. SQL 卫生:全 ? 参数化零字面 percent、SQL 内零注释(compat 红线);
  7. 路由层:api_kpi_series 透传 days;domain 炸 → 契约形状 status=error 不 500。
红线:mock conn 全程,不触真库,不触 viltrox_fit_score / rule_v0(纯读聚合零评分)。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

from app.domains.comments import reply_queue_series as series_mod  # noqa: E402

FROZEN_NOW = datetime(2026, 7, 12, 8, 30, tzinfo=timezone.utc)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """按 SQL 内容路由六条查询(有 'AS day' = 日序列;无 = 上窗 COUNT)。"""

    def __init__(
        self,
        *,
        has_table=True,
        status_day_rows=(),
        replied_day_rows=(),
        price_day_rows=(),
        prev_status_rows=(),
        prev_replied=0,
        prev_price=0,
    ):
        self.has_table = has_table
        self.status_day_rows = list(status_day_rows)
        self.replied_day_rows = list(replied_day_rows)
        self.price_day_rows = list(price_day_rows)
        self.prev_status_rows = list(prev_status_rows)
        self.prev_replied = prev_replied
        self.prev_price = prev_price
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        flat = " ".join(sql.split()).lower()
        if "information_schema.tables" in flat:
            return _FakeResult([{"table_name": "vkpi_reply_queue"}] if self.has_table else [])
        has_day = "as day" in flat
        if "status = 'replied'" in flat:
            return _FakeResult(self.replied_day_rows if has_day else [{"n": self.prev_replied}])
        if "intent_tag = 'price'" in flat:
            return _FakeResult(self.price_day_rows if has_day else [{"n": self.prev_price}])
        if "lower(coalesce(status" in flat:
            return _FakeResult(self.status_day_rows if has_day else self.prev_status_rows)
        raise AssertionError(f"unexpected SQL: {sql}")


@pytest.fixture()
def frozen_now(monkeypatch):
    monkeypatch.setattr(series_mod, "_now_utc", lambda: FROZEN_NOW)


# ── 1. 契约形状 + 日轴 0 填齐钳 now ──


def test_contract_shape_and_zero_filled_axis(frozen_now):
    conn = _FakeConn(
        status_day_rows=[
            {"day": "2026-07-10", "status": "pending", "n": 3},
            {"day": "2026-07-10", "status": "replied", "n": 1},
            {"day": "2026-07-12", "status": "drafted", "n": 2},
        ],
        replied_day_rows=[{"day": "2026-07-11", "n": 4}],
        price_day_rows=[{"day": "2026-07-12", "n": 5}],
    )
    result = series_mod.kpi_series(days=7, conn=conn)
    assert result["status"] == "ready"
    assert result["granularity"] == "day"
    assert result["days"] == 7
    assert set(result["series"].keys()) == set(series_mod.MEASURES)
    assert set(result["prev"].keys()) == set(series_mod.MEASURES)
    assert "basis" in result and "prev" in result["basis"]

    # 日轴:7 天连续,末日=冻结的今天(右沿钳 now,零未来日)
    dates = [p["date"] for p in result["series"]["enqueued"]]
    assert dates == [
        "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09",
        "2026-07-10", "2026-07-11", "2026-07-12",
    ]
    for m in series_mod.MEASURES:
        assert [p["date"] for p in result["series"][m]] == dates

    # 0 填齐 + 计数落位:enqueued=按日全状态求和(7-10:3+1=4;7-12:2)
    by_date = {p["date"]: p["count"] for p in result["series"]["enqueued"]}
    assert by_date == {"2026-07-06": 0, "2026-07-07": 0, "2026-07-08": 0,
                       "2026-07-09": 0, "2026-07-10": 4, "2026-07-11": 0, "2026-07-12": 2}
    pending = {p["date"]: p["count"] for p in result["series"]["pending"]}
    assert pending["2026-07-10"] == 3 and pending["2026-07-12"] == 0
    replied = {p["date"]: p["count"] for p in result["series"]["replied"]}
    assert replied["2026-07-11"] == 4 and sum(replied.values()) == 4
    price = {p["date"]: p["count"] for p in result["series"]["price"]}
    assert price["2026-07-12"] == 5

    # 窗口:since=00:00 UTC 的 7 天前起点,until=now(钳);上窗严格等长
    assert result["window"] == {"since": "2026-07-06T00:00:00Z", "until": "2026-07-12T08:30:00Z"}
    assert result["window_prev"]["until"] == result["window"]["since"]


# ── 2. 环比:current=本窗序列求和;上窗 0 → delta_pct=null ──


def test_delta_pct_null_when_prev_zero_and_real_when_positive(frozen_now):
    conn = _FakeConn(
        status_day_rows=[
            {"day": "2026-07-12", "status": "pending", "n": 6},
            {"day": "2026-07-11", "status": "drafted", "n": 2},
        ],
        replied_day_rows=[{"day": "2026-07-12", "n": 3}],
        price_day_rows=[],
        prev_status_rows=[{"status": "pending", "n": 4}],  # 上窗:enqueued=4, pending=4, drafted=0
        prev_replied=2,
        prev_price=0,
    )
    result = series_mod.kpi_series(days=7, conn=conn)
    prev = result["prev"]
    # enqueued:cur=8, prev=4 → +100.0
    assert prev["enqueued"] == {"current": 8, "previous": 4, "delta_pct": 100.0}
    # pending:cur=6, prev=4 → +50.0
    assert prev["pending"]["delta_pct"] == 50.0
    # drafted:上窗 0 → null(诚实无药丸)
    assert prev["drafted"] == {"current": 2, "previous": 0, "delta_pct": None}
    # replied:cur=3, prev=2 → +50.0
    assert prev["replied"]["delta_pct"] == 50.0
    # price:两窗都 0 → delta null,cur 如实 0
    assert prev["price"] == {"current": 0, "previous": 0, "delta_pct": None}


def test_prev_window_params_are_equal_length_shifted(frozen_now):
    conn = _FakeConn()
    result = series_mod.kpi_series(days=7, conn=conn)
    # 上窗 = [since-窗长, since):参数如实出现在上窗三查里
    prev_since = result["window_prev"]["since"]
    prev_until = result["window_prev"]["until"]
    assert prev_until == "2026-07-06T00:00:00Z"
    assert prev_since == "2026-06-29T15:30:00Z"  # since - (now-since) 严格等长
    prev_calls = [c for c in conn.calls if len(c[1]) >= 2 and c[1][0] == prev_since]
    assert len(prev_calls) == 3  # status/replied/price 三条上窗查询同一窗


# ── 3. 双层封顶 ──


def test_days_clamped_both_directions(frozen_now):
    conn = _FakeConn()
    assert series_mod.kpi_series(days=99999, conn=conn)["days"] == series_mod.SERIES_MAX_DAYS
    assert len(series_mod.kpi_series(days=99999, conn=_FakeConn())["series"]["enqueued"]) == series_mod.SERIES_MAX_DAYS
    assert series_mod.kpi_series(days=0, conn=_FakeConn())["days"] == series_mod.DEFAULT_DAYS


# ── 4. 诚实空态:表未建 ──


def test_missing_table_honest_empty():
    conn = _FakeConn(has_table=False)
    result = series_mod.kpi_series(days=30, conn=conn)
    assert result["status"] == "empty"
    assert result["reason"] == "reply_queue_table_missing"
    assert result["series"] == {m: [] for m in series_mod.MEASURES}
    assert result["prev"] == {}
    # 探针后零后续查询(不摸不存在的表)
    assert len(conn.calls) == 1


# ── 5. SQL 卫生:? 参数化、零字面 percent、SQL 内零注释(compat 红线)──


def test_sql_hygiene(frozen_now):
    conn = _FakeConn()
    series_mod.kpi_series(days=7, conn=conn)
    assert len(conn.calls) == 7  # 探针 + 本窗 3 + 上窗 3
    for sql, params in conn.calls:
        assert "%" not in sql, f"SQL 出现字面 percent(compat 红线): {sql}"
        assert "--" not in sql and "/*" not in sql, f"SQL 内注释(compat ? 陷阱): {sql}"
        assert sql.count("?") == len(params)


# ── 6. 路由层:days 透传;domain 炸 → 契约形状 status=error 不 500 ──


def test_router_passthrough_and_error_degrade(monkeypatch):
    from app.api.routers import vkpi_reply_queue as router_mod

    seen: dict = {}
    monkeypatch.setattr(series_mod, "kpi_series", lambda days: seen.update(days=days) or {"status": "ready", "days": days})
    assert router_mod.api_kpi_series(days=14, staff=None)["status"] == "ready"
    assert seen["days"] == 14

    def _boom(days):
        raise RuntimeError("db down")

    monkeypatch.setattr(series_mod, "kpi_series", _boom)
    degraded = router_mod.api_kpi_series(days=30, staff=None)
    assert degraded["status"] == "error"
    assert "db down" in degraded["reason"]
    assert degraded["series"] == {m: [] for m in series_mod.MEASURES}
