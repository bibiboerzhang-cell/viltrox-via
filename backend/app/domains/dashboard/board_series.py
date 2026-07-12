"""板块 KPI 按日时序端点族(board_series · 挂账迸发①)——8 板 KPI 卡真 sparkline 数据源。

用途:给此前「无按日时序端点 → KpiCard spempty 诚实虚线」的板块 KPI 卡补按日
序列 + 环比真数据。统一端点 GET /api/admin/vkpi/board-series?board=<key>&days=30
(kol-profile 带 &kol_id=,sku360 带 &sku=),金样板 = market/voice_report_ext 的
kpi_series 模式(日轴 0 填齐右沿钳 now / 环比同等流逝窗 / 单组降级)+
kol/my_kol_board_ext 的 days 天日粒度窗信封。

八板逐板序列(全部流量型按日计数/求和,0 填齐;每指标 basis 注明真实表名):
  projects     projects_new(vkpi_projects.created_at)/ stage_advances
               (vkpi_project_stage_events.created_at,阶段推进事件)/ content_posted
               (vkpi_project_content_posts.published_at,发布时间缺失行如实不进序列)/
               attribution_revenue_cents(vkpi_sales_attributions.occurred_at 按日
               SUM(revenue_cents),美分原样,换算美元是前端唯一换算点的事)
  events       events_new(vkpi_events.created_at)/ events_started(start_date 日列,
               开幕日计数)/ event_expense_amount(vkpi_event_expenses 按日 SUM(amount),
               整数原样)
  kol-profile  evidence_new(该 KOL evidence 采集入库/日,created_at 为 naive UTC 列)/
               evidence_published(COALESCE(published_at_norm, publish_date) 发布/日,
               双缺失行如实不进序列);必带 kol_id
  autonomy     inbox_suggested(vkpi_action_inbox.created_at 新建议/日)/ inbox_executed
               (status=executed 行按 updated_at 计数;执行时刻无独立列,
               updated_at=最后状态变更时刻,如实近似并写进 basis)
  launchpad    content_candidates(vkpi_project_content_posts.created_at,入库即候选)/
               publish_approvals(vkpi_publish_approvals.created_at;迁移 173 未 apply
               → 探针判缺表,该组诚实 empty)
  sku360       sku_mentions(vkpi_kol_video_evidence 标题 × vkpi_product_aliases
               alias_norm 词表按发布日计数,与 sku_performance 同源词表同 token 边界
               匹配器;匹配全在 Python 零 LIKE);必带 sku,解析不到 404
  creative     deep_videos_new(vkpi_analysis_cache final_v1 ready 按 created_at/日)/
               segments_new(同窗 result payload 按 creative_segments._decompose_video
               同口径计段:开头段+分镜段+产品露出段,解不开 payload 记 0 段如实)
  dealers      dealers_new(vkpi_dealers.created_at);全表 0 行 → 板级诚实 empty,
               绝不摆 0 填平线冒充有数据流

窗口口径(my_kol_board_ext._windows 同款):days 天 UTC 日粒度窗
[today-(days-1), today](右沿=今天,绝不产未来日);环比上窗=紧前等长 days 天;
流量型环比两侧同取已流逝等长时段(current=[since 0 点, now) vs
prev=[prev_since 0 点, prev_since+elapsed)),部分窗绝不比整窗。

诚实空态:窗口内真 0 = 序列如实 0(表在数据链在);表未建/全表空 = status empty
带 reason;delta_pct 只在上窗 > 0 时给(上窗 0 → null,前端诚实省略药丸)。
单指标失败降级 {status:"error"} 不拖累同板其余指标。所有扫描 SQL LIMIT ? +
Python 切片双封顶。时间全 UTC。

显示层宪法:返回体零个人字段零明文联系方式(SELECT 列白名单 + 契约测试静态审查);
sku360 只出计数,标题文本不出参。

拆分伴随文件(600 行红线):SQL 常量在 board_series_sql.py;sku360 / creative
两块参数板构建器在 board_series_param.py(本模块调度处函数内懒 import,零循环)。
红线:纯读聚合,零写库、零 LLM、零外调;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.domains.dashboard.board_series_sql import (
    APPROVALS_COUNT_SQL,
    APPROVALS_DAY_SQL,
    APPROVALS_TABLE,
    ATTR_REVENUE_DAY_SQL,
    ATTR_REVENUE_SUM_SQL,
    BOARD_SERIES_METHOD,
    CANDIDATES_COUNT_SQL,
    CANDIDATES_DAY_SQL,
    CONTENT_POSTED_COUNT_SQL,
    CONTENT_POSTED_DAY_SQL,
    DAYS_MAX,
    DEALERS_NEW_COUNT_SQL,
    DEALERS_NEW_DAY_SQL,
    DEALERS_TOTAL_SQL,
    EVENT_EXPENSES_DAY_SQL,
    EVENT_EXPENSES_SUM_SQL,
    EVENTS_NEW_COUNT_SQL,
    EVENTS_NEW_DAY_SQL,
    EVENTS_STARTED_COUNT_SQL,
    EVENTS_STARTED_DAY_SQL,
    INBOX_EXECUTED_COUNT_SQL,
    INBOX_EXECUTED_DAY_SQL,
    INBOX_EXECUTED_STATUS,
    INBOX_SUGGESTED_COUNT_SQL,
    INBOX_SUGGESTED_DAY_SQL,
    KOL_EVIDENCE_NEW_COUNT_SQL,
    KOL_EVIDENCE_NEW_DAY_SQL,
    KOL_EVIDENCE_PUB_COUNT_SQL,
    KOL_EVIDENCE_PUB_DAY_SQL,
    PROJECTS_NEW_COUNT_SQL,
    PROJECTS_NEW_DAY_SQL,
    SERIES_MAX_DAYS,
    SERIES_ROWS_LIMIT,
    STAGE_EVENTS_COUNT_SQL,
    STAGE_EVENTS_DAY_SQL,
    TABLE_PROBE_SQL,
)

logger = get_logger(__name__)

BOARD_KEYS = (
    "projects", "events", "kol-profile", "autonomy",
    "launchpad", "sku360", "creative", "dealers",
)


# ── 小工具(voice_report_ext / my_kol_board_ext 同款容错约定)────────────


def _now_utc() -> datetime:
    """当前 UTC 时刻(独立缝:契约测试 monkeypatch 冻结 now)。"""
    return datetime.now(timezone.utc)


def _int0(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _day_str(value: Any) -> str:
    """DB 读回的日期(date / datetime / str)→ 'YYYY-MM-DD';解不了返回 ''(诚实丢弃)。"""
    if isinstance(value, datetime):        # datetime 是 date 子类,必须先判
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _delta_pct(current: int | None, previous: int | None) -> float | None:
    """环比百分比;上窗为 0 或缺数据 → null(前端诚实省略药丸)。"""
    if current is None or previous is None or previous <= 0:
        return None
    return round((current - previous) * 100.0 / previous, 1)


def _windows(days: int) -> dict[str, Any]:
    """days 天日粒度窗 + 紧前等长上窗(my_kol_board_ext._windows 同口径)。

    流量型环比用时间戳窗:current=[since 0 点, now) 实际流逝段,prev=上窗起点 0 点 +
    同等流逝长度——两侧等长,部分窗绝不比整窗。timestamptz 列用 tz-aware ISO 参数;
    naive 列(evidence.created_at 库内约定 UTC)用 naive ISO;date 列用日字符串。
    """
    now = _now_utc()
    today = now.date()
    since_d = today - timedelta(days=days - 1)
    prev_until_d = since_d - timedelta(days=1)
    prev_since_d = since_d - timedelta(days=days)
    since_ts = datetime(since_d.year, since_d.month, since_d.day, tzinfo=timezone.utc)
    elapsed = now - since_ts
    prev_since_ts = since_ts - timedelta(days=days)
    prev_until_ts = prev_since_ts + elapsed
    return {
        "since_d": since_d,
        "until_d": today,
        "prev_since_d": prev_since_d,
        "prev_until_d": prev_until_d,
        "cur_tz": (since_ts.isoformat(), now.isoformat()),
        "prev_tz": (prev_since_ts.isoformat(), prev_until_ts.isoformat()),
        "cur_naive": (
            since_ts.replace(tzinfo=None).isoformat(),
            now.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
        ),
        "prev_naive": (
            prev_since_ts.replace(tzinfo=None).isoformat(),
            prev_until_ts.replace(tzinfo=None).isoformat(),
        ),
        "cur_date": (since_d.isoformat(), today.isoformat()),
        "prev_date": (prev_since_d.isoformat(), prev_until_d.isoformat()),
    }


def _day_axis(since_d: date, until_d: date) -> list[str]:
    """[since_d, until_d] 连续 UTC 日轴(封顶 SERIES_MAX_DAYS;右沿=今天,零未来日)。"""
    out: list[str] = []
    cursor = since_d
    while cursor <= until_d and len(out) < SERIES_MAX_DAYS:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def _day_map(conn: Any, sql: str, params: tuple) -> dict[str, int]:
    """按日聚合 SQL → {'YYYY-MM-DD': n};行数 SQL/Python 双封顶。"""
    rows = conn.execute(sql, params).fetchall()
    out: dict[str, int] = {}
    for r in list(rows)[:SERIES_ROWS_LIMIT]:
        rec = dict(r)
        day = _day_str(rec.get("day"))
        if day:
            out[day] = _int0(rec.get("n"))
    return out


def _scalar(conn: Any, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return _int0(dict(row or {}).get("n"))


def _flow_metric(
    conn: Any,
    *,
    day_sql: str,
    count_sql: str,
    axis: list[str],
    cur: tuple[str, str],
    prev: tuple[str, str],
    table: str,
    unit: str = "rows",
    point_key: str = "count",
    extra: tuple = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """流量型指标通用件:0 填日序列 + 本窗/上窗计数 + delta(上窗 0 → null)。"""
    day_map = _day_map(conn, day_sql, (*extra, cur[0], cur[1], SERIES_ROWS_LIMIT))
    current = _scalar(conn, count_sql, (*extra, cur[0], cur[1]))
    previous = _scalar(conn, count_sql, (*extra, prev[0], prev[1]))
    series = [{"date": d, point_key: day_map.get(d, 0)} for d in axis]
    metric = {
        "status": "ready",
        "current": current,
        "previous": previous,
        "delta_pct": _delta_pct(current, previous),
        "table": table,
        "unit": unit,
    }
    return series, metric


def _build_metric(
    out: dict[str, Any],
    name: str,
    basis: str,
    build: Callable[[], tuple[list[dict[str, Any]], dict[str, Any]]],
) -> None:
    """单指标失败诚实降级 {status:'error'},不拖累同板其余指标(金样板同款)。"""
    out["basis"][name] = basis
    try:
        series, metric = build()
        out["series"][name] = series
        out["metrics"][name] = metric
    except Exception as exc:  # noqa: BLE001 — 单指标降级,同板其余照常
        logger.warning("board_series metric %s failed: %s", name, exc)
        out["series"][name] = []
        out["metrics"][name] = {"status": "error", "reason": str(exc)[:200]}


def _table_missing(conn: Any, table: str) -> bool:
    probe = conn.execute(TABLE_PROBE_SQL, (table,)).fetchone()
    return not probe


# ── 逐板构建器(每板单一职责;真实表名写进 basis)─────────────────────────


def _projects_board(conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any]) -> None:
    cur, prev = win["cur_tz"], win["prev_tz"]
    _build_metric(
        out, "projects_new",
        "vkpi_projects 按 created_at 的 UTC 日计数(新建项目/日);流量型 0 填齐,"
        "环比两侧同取已流逝等长时段,上窗 0 → delta_pct=null",
        lambda: _flow_metric(conn, day_sql=PROJECTS_NEW_DAY_SQL, count_sql=PROJECTS_NEW_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_projects"),
    )
    _build_metric(
        out, "stage_advances",
        "vkpi_project_stage_events 按 created_at 的 UTC 日计数(阶段推进事件/日,"
        "含全部 event_type 的真实推进留痕);流量型 0 填齐",
        lambda: _flow_metric(conn, day_sql=STAGE_EVENTS_DAY_SQL, count_sql=STAGE_EVENTS_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_project_stage_events"),
    )
    _build_metric(
        out, "content_posted",
        "vkpi_project_content_posts 按 published_at 的 UTC 日计数(发布内容帖/日);"
        "published_at 缺失的候选行如实不进序列(未发布不算发布)",
        lambda: _flow_metric(conn, day_sql=CONTENT_POSTED_DAY_SQL, count_sql=CONTENT_POSTED_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_project_content_posts"),
    )
    _build_metric(
        out, "attribution_revenue_cents",
        "vkpi_sales_attributions 按 occurred_at 的 UTC 日 SUM(revenue_cents),美分原样"
        "(美分→美元换算是前端唯一换算点的事,历史 100 倍缺陷防线);"
        "环比=金额和对比,上窗 0 → delta_pct=null",
        lambda: _flow_metric(conn, day_sql=ATTR_REVENUE_DAY_SQL, count_sql=ATTR_REVENUE_SUM_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_sales_attributions",
                             unit="cents", point_key="value"),
    )


def _events_board(conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any]) -> None:
    cur, prev = win["cur_tz"], win["prev_tz"]
    _build_metric(
        out, "events_new",
        "vkpi_events 按 created_at 的 UTC 日计数(新建活动/日);流量型 0 填齐",
        lambda: _flow_metric(conn, day_sql=EVENTS_NEW_DAY_SQL, count_sql=EVENTS_NEW_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_events"),
    )
    _build_metric(
        out, "events_started",
        "vkpi_events 按 start_date(日列)计数(开幕活动/日);窗口=日粒度闭区间,"
        "start_date 为纯日期列无时刻精度,如实按日对齐",
        lambda: _flow_metric(conn, day_sql=EVENTS_STARTED_DAY_SQL, count_sql=EVENTS_STARTED_COUNT_SQL,
                             axis=axis, cur=win["cur_date"], prev=win["prev_date"], table="vkpi_events"),
    )
    _build_metric(
        out, "event_expense_amount",
        "vkpi_event_expenses 按 created_at 的 UTC 日 SUM(amount)(费用登记/日,整数原样);"
        "窗口内零登记 = 序列如实 0",
        lambda: _flow_metric(conn, day_sql=EVENT_EXPENSES_DAY_SQL, count_sql=EVENT_EXPENSES_SUM_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_event_expenses",
                             unit="amount", point_key="value"),
    )


def _kol_profile_board(
    conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any], kol_id: int,
) -> None:
    _build_metric(
        out, "evidence_new",
        "vkpi_kol_video_evidence(kol_pool_id 过滤,is_active IS NOT FALSE)按 created_at "
        "采集入库的 UTC 日计数(naive 列库内约定 UTC);流量型 0 填齐",
        lambda: _flow_metric(conn, day_sql=KOL_EVIDENCE_NEW_DAY_SQL, count_sql=KOL_EVIDENCE_NEW_COUNT_SQL,
                             axis=axis, cur=win["cur_naive"], prev=win["prev_naive"],
                             table="vkpi_kol_video_evidence", extra=(kol_id,)),
    )
    _build_metric(
        out, "evidence_published",
        "vkpi_kol_video_evidence(kol_pool_id 过滤,is_active IS NOT FALSE)按 "
        "COALESCE(published_at_norm, publish_date) 的 UTC 日计数(发布/日);"
        "两列皆缺的行如实不进序列(发布时间不明不编)",
        lambda: _flow_metric(conn, day_sql=KOL_EVIDENCE_PUB_DAY_SQL, count_sql=KOL_EVIDENCE_PUB_COUNT_SQL,
                             axis=axis, cur=win["cur_tz"], prev=win["prev_tz"],
                             table="vkpi_kol_video_evidence", extra=(kol_id,)),
    )


def _autonomy_board(conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any]) -> None:
    cur, prev = win["cur_tz"], win["prev_tz"]
    _build_metric(
        out, "inbox_suggested",
        "vkpi_action_inbox 按 created_at 的 UTC 日计数(新建议/日,全状态入账:"
        "建议产生量与当前 status 无关);流量型 0 填齐",
        lambda: _flow_metric(conn, day_sql=INBOX_SUGGESTED_DAY_SQL, count_sql=INBOX_SUGGESTED_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_action_inbox"),
    )
    _build_metric(
        out, "inbox_executed",
        "vkpi_action_inbox(status=executed)按 updated_at 的 UTC 日计数;执行时刻无"
        "独立列,updated_at=最后状态变更时刻,如实近似(executed 为终态,变更即执行)",
        lambda: _flow_metric(conn, day_sql=INBOX_EXECUTED_DAY_SQL, count_sql=INBOX_EXECUTED_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_action_inbox",
                             extra=(INBOX_EXECUTED_STATUS,)),
    )


def _launchpad_board(conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any]) -> None:
    cur, prev = win["cur_tz"], win["prev_tz"]
    _build_metric(
        out, "content_candidates",
        "vkpi_project_content_posts 按 created_at 的 UTC 日计数(新候选帖/日:"
        "观察窗口扫到内容入库即候选,后续复核只改 status 不改入库时刻);流量型 0 填齐",
        lambda: _flow_metric(conn, day_sql=CANDIDATES_DAY_SQL, count_sql=CANDIDATES_COUNT_SQL,
                             axis=axis, cur=cur, prev=prev, table="vkpi_project_content_posts"),
    )

    def _approvals() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if _table_missing(conn, APPROVALS_TABLE):
            return [], {
                "status": "empty",
                "reason": "vkpi_publish_approvals 表未建(迁移 173 未 apply)——诚实空,不编序列。",
                "table": APPROVALS_TABLE,
            }
        return _flow_metric(conn, day_sql=APPROVALS_DAY_SQL, count_sql=APPROVALS_COUNT_SQL,
                            axis=axis, cur=cur, prev=prev, table=APPROVALS_TABLE)

    _build_metric(
        out, "publish_approvals",
        "vkpi_publish_approvals 按 created_at 的 UTC 日计数(新审批行/日,迁移 173);"
        "表未建 → 探针判缺诚实 empty;窗口内零审批 = 序列如实 0",
        _approvals,
    )


def _dealers_board(conn: Any, out: dict[str, Any], axis: list[str], win: dict[str, Any]) -> None:
    total = _scalar(conn, DEALERS_TOTAL_SQL, ())
    if total <= 0:
        out["status"] = "empty"
        out["reason"] = "vkpi_dealers 全表 0 行(数据在线上库)——诚实空,不摆 0 填平线冒充有数据流。"
        out["series"]["dealers_new"] = []
        out["metrics"]["dealers_new"] = {"status": "empty", "table": "vkpi_dealers"}
        out["basis"]["dealers_new"] = "vkpi_dealers 按 created_at 的 UTC 日计数(新入库经销商/日);全表 0 行 → 诚实 empty"
        return
    _build_metric(
        out, "dealers_new",
        "vkpi_dealers 按 created_at 的 UTC 日计数(新入库经销商/日);流量型 0 填齐",
        lambda: _flow_metric(conn, day_sql=DEALERS_NEW_DAY_SQL, count_sql=DEALERS_NEW_COUNT_SQL,
                             axis=axis, cur=win["cur_tz"], prev=win["prev_tz"], table="vkpi_dealers"),
    )


# ── 主入口 ──────────────────────────────────────────────────────────────


def build_board_series(
    board: str,
    *,
    days: int = 30,
    kol_id: int | None = None,
    sku: str | None = None,
    conn: Any = None,
) -> dict[str, Any]:
    """单板 KPI 按日序列信封:series/metrics/basis 三键逐指标对齐。

    board ∈ BOARD_KEYS,非法抛 ValueError(路由转 400);kol-profile 必带 kol_id、
    sku360 必带 sku,缺参 ValueError;sku 解析不到抛 LookupError(路由转 404)。
    纯读;conn 可注入(测试),缺省懒取 get_conn。
    """
    from app.db.connection import get_conn

    key = str(board or "").strip().lower()
    if key not in BOARD_KEYS:
        raise ValueError(f"unknown board: {board}")
    days = max(1, min(_int0(days, 30) or 30, DAYS_MAX))
    if key == "kol-profile" and _int0(kol_id) <= 0:
        raise ValueError("kol-profile 板必须带 kol_id 参数")
    if key == "sku360" and not str(sku or "").strip():
        raise ValueError("sku360 板必须带 sku 参数")

    db = conn if conn is not None else get_conn()
    win = _windows(days)
    axis = _day_axis(win["since_d"], win["until_d"])
    out: dict[str, Any] = {
        "status": "ready",
        "board": key,
        "days": days,
        "window": {
            "since": win["since_d"].isoformat(),
            "until": win["until_d"].isoformat(),
            "prev_since": win["prev_since_d"].isoformat(),
            "prev_until": win["prev_until_d"].isoformat(),
        },
        "series": {},
        "metrics": {},
        "basis": {},
    }
    if key == "projects":
        _projects_board(db, out, axis, win)
    elif key == "events":
        _events_board(db, out, axis, win)
    elif key == "kol-profile":
        out["params"] = {"kol_id": _int0(kol_id)}
        _kol_profile_board(db, out, axis, win, _int0(kol_id))
    elif key == "autonomy":
        _autonomy_board(db, out, axis, win)
    elif key == "launchpad":
        _launchpad_board(db, out, axis, win)
    elif key == "sku360":
        # 参数板构建器住拆分伴随文件(600 行红线);函数内懒 import 零循环依赖
        from app.domains.dashboard.board_series_param import _sku360_board

        _sku360_board(db, out, axis, win, str(sku or "").strip())
    elif key == "creative":
        from app.domains.dashboard.board_series_param import _creative_board

        _creative_board(db, out, axis, win)
    else:  # dealers(BOARD_KEYS 闸后唯一余项)
        _dealers_board(db, out, axis, win)

    out["method"] = BOARD_SERIES_METHOD
    out["generated_at"] = datetime.now(timezone.utc).isoformat()
    return out
