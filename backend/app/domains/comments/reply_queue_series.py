"""ReplyQueue KPI 时序(kpi_series · voice_report_ext kpi_series 同模式)。

用途:回复队列板块页 KPI 四卡(待起草/待回复/已回复/价格购买意向)的
sparkline 按日序列 + 环比 delta 真数据。挂 GET /api/admin/vkpi/reply-queue/kpi-series
(与队列列表同前缀、同 require_tab("vkpi","read") 权限),纯增量端点,
对既有 /reply-queue 列表契约零改动。

契约(voice_report_ext kpi_series 模式):
  {
    status: ready|empty,  granularity: "day",  days: N,
    window: {since, until},                       # until 钳 now,绝不产未来日
    series: {enqueued|pending|drafted|replied|price: [{date, count}...]},  # 日轴 0 填齐
    prev:   {measure: {current, previous, delta_pct}},  # 上一等长窗;上窗 0 → delta_pct=null
    basis:  {...}                                 # 每序列真实口径句
  }

五个度量(口径逐一如实,绝不冒充历史快照):
  enqueued  窗口内入队行/日(created_at UTC 日;全状态)。
  pending   现存 status=pending 行按入队日 —— 状态会流转,序列口径=「现状回看」,
            不是当日积压快照(vkpi_reply_queue 无历史快照表,诚实口径)。
  drafted   现存 status=drafted 行按入队日(同上现状回看)。
  replied   现存 status=replied 行按 updated_at 日 —— 终态后行不再更新,
            updated_at≈标记已回时刻(近似回复时刻,如实注明)。
  price     intent_tag=price 行按入队日。

环比:current = 本窗日序列求和;previous = 上一等长窗(prev_since = since - 窗长)
同口径 COUNT;previous 为 0 → delta_pct=null(前端诚实省略环比药丸,绝不编百分比)。

诚实空态:队列表未建 → status=empty + reason,series 全空数组;窗口右沿=now,
日轴只到今天(未来日 0 填装有 = 编数据)。所有 LIMIT 双层封顶(SQL LIMIT ? +
Python 切片);days router ge/le + 本层 max/min 双封顶。

compat 约定:SQL 占位符全 ?;SQL 字符串零字面 percent、零 SQL 内注释(compat
适配器把 ? 当占位符,注释里的 ? 会炸参数计数);时间戳读回可能是 str;
conn 可注入(测试),缺省懒 import get_conn。

红线:纯读聚合,零写库、零 LLM、零外调;零触 viltrox_fit_score / rule_v0
(本模块不产生任何评分,输出仅供 KPI 卡 sparkline 展示)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

# ── 护栏常量(测试直接断言;SQL LIMIT ? + Python 层二次封顶双保险)──
SERIES_MAX_DAYS = 180        # 窗口天数封顶(router le=180 + 本层 min 双保险)
DEFAULT_DAYS = 30            # 缺省窗口(近 30 天,UTC 日轴)
STATUS_DAY_ROWS_LIMIT = 800  # 日 × 状态组合行封顶(180 天 × 4 状态 + 余量)
DAY_ROWS_LIMIT = 200         # 单序列日行封顶(180 天 + 余量)
PREV_STATUS_ROWS_LIMIT = 10  # 上窗按状态分组行封顶(合法状态仅 4)

MEASURES = ("enqueued", "pending", "drafted", "replied", "price")

# ── SQL 常量(全参数化;零字面 percent;窗口/LIMIT 全下推)────────────────
STATUS_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day,
           LOWER(COALESCE(status, '')) AS status,
           COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1, 2
    ORDER BY 1
    LIMIT ?
"""

REPLIED_DAY_SQL = """
    SELECT CAST((updated_at AT TIME ZONE 'UTC') AS DATE) AS day,
           COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE status = 'replied'
      AND updated_at >= CAST(? AS TIMESTAMPTZ)
      AND updated_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

PRICE_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day,
           COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE intent_tag = 'price'
      AND created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

STATUS_PREV_SQL = """
    SELECT LOWER(COALESCE(status, '')) AS status,
           COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    LIMIT ?
"""

REPLIED_PREV_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE status = 'replied'
      AND updated_at >= CAST(? AS TIMESTAMPTZ)
      AND updated_at < CAST(? AS TIMESTAMPTZ)
"""

PRICE_PREV_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE intent_tag = 'price'
      AND created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

TABLE_PROBE_SQL = """
    SELECT table_name FROM information_schema.tables WHERE table_name = ? LIMIT 1
"""

BASIS = {
    "enqueued": "vkpi_reply_queue 入队行按 created_at 的 UTC 日计数(全状态;0 填齐,右沿=now)",
    "pending": "现存 status=pending 行按入队日 created_at;状态会流转,口径=现状回看非当日积压快照",
    "drafted": "现存 status=drafted 行按入队日 created_at(同上现状回看)",
    "replied": "现存 status=replied 行按 updated_at 日(终态后不再更新,≈标记已回时刻)",
    "price": "intent_tag=price 行按入队日 created_at",
    "prev": "上一等长窗口同口径计数;上窗 0 → delta_pct=null(前端诚实省略环比药丸)",
}


# ── 小工具(自持:不跨域引 market 私有件;与 voice_report_ext 同语义)──────


def _now_utc() -> datetime:
    """当前 UTC 时刻(独立缝:契约测试 monkeypatch 冻结 now)。"""
    return datetime.now(timezone.utc)


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _day_str(value: Any) -> str:
    """DB 读回的日期(date / datetime / str)→ 'YYYY-MM-DD';解不了返回 ''(诚实丢弃)。"""
    if isinstance(value, datetime):  # datetime 是 date 子类,必须先判
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _delta_pct(current: int, previous: int) -> float | None:
    """环比百分比;上窗为 0 或缺数据 → null(前端诚实省略药丸)。"""
    if previous <= 0:
        return None
    return round((current - previous) * 100.0 / previous, 1)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row.get(key) if hasattr(row, "get") else row[key]
    except Exception:
        try:
            return dict(row).get(key, default)
        except Exception:
            return default


def _day_counts(rows: Any, *, limit: int) -> dict[str, int]:
    """按日计数行 → {'YYYY-MM-DD': n}(Python 层二次封顶)。"""
    counts: dict[str, int] = {}
    for r in list(rows)[:limit]:
        day = _day_str(_row_get(r, "day"))
        if day:
            counts[day] = counts.get(day, 0) + _int0(_row_get(r, "n"))
    return counts


def _empty_payload(days: int, reason: str) -> dict[str, Any]:
    return {
        "status": "empty",
        "reason": reason,
        "granularity": "day",
        "days": days,
        "series": {m: [] for m in MEASURES},
        "prev": {},
        "basis": BASIS,
    }


# ── 主入口 ────────────────────────────────────────────────────────────────


def kpi_series(days: int = DEFAULT_DAYS, *, conn: Any | None = None) -> dict[str, Any]:
    """回复队列近 N 天按日入队/回复序列 + 上一等长窗环比。纯读。

    窗口 = [今天-(N-1) 日 00:00 UTC, now):右沿钳 now,日轴以今天收尾,
    绝不产未来日;环比上窗 = [since-窗长, since) 与本窗严格等长。
    """
    if conn is None:
        from app.db.connection import get_conn

        conn = get_conn()

    safe_days = max(1, min(int(days or DEFAULT_DAYS), SERIES_MAX_DAYS))

    probe = conn.execute(TABLE_PROBE_SQL, ("vkpi_reply_queue",)).fetchone()
    if not probe:
        return _empty_payload(safe_days, "reply_queue_table_missing")

    now = _now_utc()
    since_day = now.date() - timedelta(days=safe_days - 1)
    since_dt = datetime(since_day.year, since_day.month, since_day.day, tzinfo=timezone.utc)
    since, until = _iso_z(since_dt), _iso_z(now)
    span = now - since_dt
    prev_since, prev_until = _iso_z(since_dt - span), since

    # 日轴:since_day → 今天(含),连续 0 填齐;右沿=now 由构造保证(无未来日)。
    axis: list[str] = []
    cursor = since_day
    today = now.date()
    while cursor <= today and len(axis) < SERIES_MAX_DAYS:
        axis.append(cursor.isoformat())
        cursor += timedelta(days=1)

    # 本窗三查:day×status(→ enqueued/pending/drafted)、replied(updated_at)、price。
    status_rows = list(
        conn.execute(STATUS_DAY_SQL, (since, until, STATUS_DAY_ROWS_LIMIT)).fetchall()
    )[:STATUS_DAY_ROWS_LIMIT]
    enqueued_counts: dict[str, int] = {}
    pending_counts: dict[str, int] = {}
    drafted_counts: dict[str, int] = {}
    for r in status_rows:
        day = _day_str(_row_get(r, "day"))
        if not day:
            continue
        st = str(_row_get(r, "status") or "").strip().lower()
        n = _int0(_row_get(r, "n"))
        enqueued_counts[day] = enqueued_counts.get(day, 0) + n
        if st == "pending":
            pending_counts[day] = pending_counts.get(day, 0) + n
        elif st == "drafted":
            drafted_counts[day] = drafted_counts.get(day, 0) + n
    replied_counts = _day_counts(
        conn.execute(REPLIED_DAY_SQL, (since, until, DAY_ROWS_LIMIT)).fetchall(),
        limit=DAY_ROWS_LIMIT,
    )
    price_counts = _day_counts(
        conn.execute(PRICE_DAY_SQL, (since, until, DAY_ROWS_LIMIT)).fetchall(),
        limit=DAY_ROWS_LIMIT,
    )

    per_measure = {
        "enqueued": enqueued_counts,
        "pending": pending_counts,
        "drafted": drafted_counts,
        "replied": replied_counts,
        "price": price_counts,
    }
    series = {
        m: [{"date": d, "count": counts.get(d, 0)} for d in axis]
        for m, counts in per_measure.items()
    }

    # 环比:current = 本窗日序列求和(同一份数据,零口径漂移);previous = 上窗同口径 COUNT。
    prev_status_rows = list(
        conn.execute(STATUS_PREV_SQL, (prev_since, prev_until, PREV_STATUS_ROWS_LIMIT)).fetchall()
    )[:PREV_STATUS_ROWS_LIMIT]
    prev_enqueued = 0
    prev_pending = 0
    prev_drafted = 0
    for r in prev_status_rows:
        st = str(_row_get(r, "status") or "").strip().lower()
        n = _int0(_row_get(r, "n"))
        prev_enqueued += n
        if st == "pending":
            prev_pending += n
        elif st == "drafted":
            prev_drafted += n
    prev_replied = _int0(_row_get(conn.execute(REPLIED_PREV_SQL, (prev_since, prev_until)).fetchone(), "n"))
    prev_price = _int0(_row_get(conn.execute(PRICE_PREV_SQL, (prev_since, prev_until)).fetchone(), "n"))
    previous_by_measure = {
        "enqueued": prev_enqueued,
        "pending": prev_pending,
        "drafted": prev_drafted,
        "replied": prev_replied,
        "price": prev_price,
    }
    prev = {}
    for m in MEASURES:
        current = sum(counts for counts in per_measure[m].values())
        previous = previous_by_measure[m]
        prev[m] = {"current": current, "previous": previous, "delta_pct": _delta_pct(current, previous)}

    return {
        "status": "ready" if axis else "empty",
        "granularity": "day",
        "days": safe_days,
        "window": {"since": since, "until": until},
        "window_prev": {"since": prev_since, "until": prev_until},
        "series": series,
        "prev": prev,
        "basis": BASIS,
    }
