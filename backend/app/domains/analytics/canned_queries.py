"""A4 问数页 · 预设问题库(canned queries,只读 / 加性新文件)。

12 个常用业务问题,每个 = 一条预定义、参数化、只读的确定性 SQL 聚合。
零 LLM:问题→SQL 的映射是硬编码白名单,摘要句由 Python 按聚合结果拼装。

可追溯性(出数带来源):每个结果回传
  - source_tables: 该问题实际读取的表清单
  - row_count:    返回行数
  - sql_explain:  key + 表 + 区间参数的一行解释
  - summary:      一句话中文摘要(纯 Python 计算,零 LLM)

安全铁律(与 query_planner 同款,自包含):
  - 只读:SQL 必须 SELECT 开头,禁 DML/DDL 关键词,单语句。
  - 白名单表:运行期正则抽取 FROM/JOIN 表名,逐一比对 ALLOWED_TABLES。
  - 参数化:区间 cutoff 在 Python 侧算好,经 `?` 占位符绑定下推(compat 层
    统一翻译);SQL 里零字面 `%`、零用户文本。
  - 红线:纯读,零触 viltrox_fit_score 写路径、不碰 rule_v0。
  - BOOLEAN 读回可能是 int 1/0:本文件不做布尔判定,原值回传。

对外接口:
  - list_questions() -> 12 问清单(key/标题/说明/列/是否吃区间/来源表)。
  - run(conn, key, range_days) -> 结构化结果(见上),未知 key 抛 LookupError。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable


# --- 白名单:允许出现在任何预设问题 SQL 中的表名(运行期硬校验) -------------
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "vkpi_cost_ledger",
        "vkpi_ai_cost_ledger",
        "vkpi_sample_assets",
        "vkpi_kol_video_evidence",
        "vkpi_kol_pool",
        "vkpi_kol_llm_deep_analysis_results",
        "vkpi_budget_settings",
        "vkpi_provider_budget_caps",
        "vkpi_channel_metrics_filled",
        "vkpi_employee_channels",
        "vkpi_reply_queue",
        "vkpi_recommendation_outcomes",
        "vkpi_kol_rates",
        "vkpi_projects",
    }
)

DEFAULT_RANGE_DAYS = 30
MIN_RANGE_DAYS = 1
MAX_RANGE_DAYS = 365
MAX_ROWS = 200


def _clamp_range_days(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RANGE_DAYS
    return max(MIN_RANGE_DAYS, min(MAX_RANGE_DAYS, n))


def _cutoff_iso(range_days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=range_days)
    return cutoff.replace(microsecond=0).isoformat()


def _f(value: Any) -> float:
    """聚合值容错转 float(Decimal/str/None 一律安全)。"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# --- 安全断言(与 query_planner 同款,自包含避免耦合) ------------------------
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|pragma|merge|replace)\b",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def assert_safe_sql(sql: str) -> None:
    s = " ".join(str(sql).split())
    low = s.lower().lstrip("( ")
    if not low.startswith("select"):
        raise ValueError("canned query must be a SELECT")
    if ";" in s.rstrip(";"):
        raise ValueError("canned query must be a single statement")
    if _FORBIDDEN.search(s):
        raise ValueError("canned query contains a forbidden keyword")
    for tbl in _TABLE_REF.findall(s):
        if tbl.lower() not in ALLOWED_TABLES:
            raise ValueError(f"table not in whitelist: {tbl}")


# --- 问题定义 -----------------------------------------------------------------
# builder(ctx) -> (sql, params);summarize(rows, ctx) -> 一句话摘要(零 LLM)。
SqlBuilder = Callable[[dict[str, Any]], tuple[str, tuple[Any, ...]]]
Summarizer = Callable[[list[dict[str, Any]], dict[str, Any]], str]


@dataclass(frozen=True)
class CannedQuestion:
    key: str
    title: str
    description: str
    columns: tuple[str, ...]
    source_tables: tuple[str, ...]
    build: SqlBuilder
    summarize: Summarizer
    uses_range: bool = False


# 1) 近30天花费:业务成本台账 + AI 调用账,按类目汇总(单位统一 USD)。
def _b_spend(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT category, entries, amount_usd FROM ("
        "SELECT 'business:' || COALESCE(cost_type, 'other') AS category, "
        "COUNT(*) AS entries, SUM(COALESCE(amount_cents, 0)) / 100.0 AS amount_usd "
        "FROM vkpi_cost_ledger "
        "WHERE incurred_at >= ? AND status='actual' AND approved_at IS NOT NULL "
        "GROUP BY COALESCE(cost_type, 'other') "
        "UNION ALL "
        "SELECT 'ai:' || COALESCE(ai_provider, 'unknown') AS category, "
        "COUNT(*) AS entries, SUM(COALESCE(cost_usd, 0)) AS amount_usd "
        "FROM vkpi_ai_cost_ledger "
        "WHERE occurred_at >= ? "
        "GROUP BY COALESCE(ai_provider, 'unknown')"
        ") t ORDER BY amount_usd DESC LIMIT ?"
    )
    return sql, (ctx["cutoff_iso"], ctx["cutoff_iso"], MAX_ROWS)


def _s_spend(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    total = sum(_f(r.get("amount_usd")) for r in rows)
    biz = sum(_f(r.get("amount_usd")) for r in rows if str(r.get("category", "")).startswith("business:"))
    ai = sum(_f(r.get("amount_usd")) for r in rows if str(r.get("category", "")).startswith("ai:"))
    entries = sum(_i(r.get("entries")) for r in rows)
    return (
        f"近{ctx['range_days']}天总花费 ${total:,.2f}"
        f"(业务开销 ${biz:,.2f} + AI 调用 ${ai:,.2f}),共 {entries} 笔记录。"
    )


# 2) 送样价值:成本台账 product/sample 类目 + 样品资产台账,两路合看。
def _b_sample_value(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT source, items, value_usd FROM ("
        "SELECT 'cost_ledger:' || COALESCE(cost_type, 'other') AS source, "
        "COUNT(*) AS items, SUM(COALESCE(amount_cents, 0)) / 100.0 AS value_usd "
        "FROM vkpi_cost_ledger "
        "WHERE cost_type IN (?, ?) AND status='actual' AND approved_at IS NOT NULL AND incurred_at >= ? "
        "GROUP BY COALESCE(cost_type, 'other') "
        "UNION ALL "
        "SELECT 'sample_assets' AS source, COUNT(*) AS items, "
        "COALESCE(SUM(sample_cost_cents), 0) / 100.0 AS value_usd "
        "FROM vkpi_sample_assets WHERE created_at >= ?"
        ") t ORDER BY value_usd DESC"
    )
    return sql, ("product", "sample", ctx["cutoff_iso"], ctx["cutoff_iso"])


def _s_sample_value(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    total = sum(_f(r.get("value_usd")) for r in rows)
    items = sum(_i(r.get("items")) for r in rows)
    return (
        f"近{ctx['range_days']}天送出产品/样品价值合计 ${total:,.2f},"
        f"共 {items} 件(成本台账 + 样品资产两路记录)。"
    )


# 3) 最佳产出 KOL:按视频证据聚合区间内观看/互动,降序排名。
def _b_top_kols(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT e.kol_pool_id AS kol_pool_id, "
        "COALESCE(NULLIF(p.display_name, ''), NULLIF(p.handle, ''), '') AS kol, "
        "COALESCE(p.platform, '') AS platform, "
        "COUNT(*) AS videos, "
        "SUM(COALESCE(e.view_count, 0)) AS views, "
        "SUM(COALESCE(e.like_count, 0)) AS likes, "
        "SUM(COALESCE(e.comment_count, 0)) AS comments "
        "FROM vkpi_kol_video_evidence e "
        "LEFT JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id "
        "WHERE COALESCE(e.posted_at, e.created_at) >= ? "
        "GROUP BY e.kol_pool_id, COALESCE(NULLIF(p.display_name, ''), NULLIF(p.handle, ''), ''), "
        "COALESCE(p.platform, '') "
        "ORDER BY views DESC LIMIT 20"
    )
    return sql, (ctx["cutoff_iso"],)


def _s_top_kols(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    if not rows:
        return f"近{ctx['range_days']}天没有可归属的 KOL 视频产出记录。"
    top = rows[0]
    return (
        f"近{ctx['range_days']}天共 {len(rows)} 位 KOL 有视频产出(取前20),"
        f"榜首 {top.get('kol') or top.get('kol_pool_id')}({top.get('platform') or '?'})"
        f"以 {_i(top.get('videos'))} 条视频拿下 {_i(top.get('views')):,} 次观看。"
    )


# 4) 深析完成量:LLM 深度分析结果(status=ready)按类型统计。
def _b_deep_analysis(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT COALESCE(analysis_kind, '') AS analysis_kind, "
        "COUNT(*) AS results, COUNT(DISTINCT kol_pool_id) AS kols_covered "
        "FROM vkpi_kol_llm_deep_analysis_results "
        "WHERE status = ? AND created_at >= ? "
        "GROUP BY COALESCE(analysis_kind, '') ORDER BY results DESC"
    )
    return sql, ("ready", ctx["cutoff_iso"])


def _s_deep_analysis(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    total = sum(_i(r.get("results")) for r in rows)
    kols = sum(_i(r.get("kols_covered")) for r in rows)
    if total == 0:
        return f"近{ctx['range_days']}天没有新完成的深度分析(status=ready)。"
    kinds = "、".join(f"{r.get('analysis_kind') or '?'} {_i(r.get('results'))}条" for r in rows)
    return f"近{ctx['range_days']}天完成深析 {total} 条(覆盖约 {kols} 个 KOL):{kinds}。"


# 5) 新入池:KOL 池区间内新增,按平台分布。
def _b_new_in_pool(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT COALESCE(platform, '') AS platform, COUNT(*) AS new_kols "
        "FROM vkpi_kol_pool WHERE created_at >= ? "
        "GROUP BY COALESCE(platform, '') ORDER BY new_kols DESC"
    )
    return sql, (ctx["cutoff_iso"],)


def _s_new_in_pool(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    total = sum(_i(r.get("new_kols")) for r in rows)
    if total == 0:
        return f"近{ctx['range_days']}天没有新 KOL 入池。"
    top = rows[0]
    return (
        f"近{ctx['range_days']}天新入池 {total} 个 KOL,"
        f"最多来自 {top.get('platform') or '?'}({_i(top.get('new_kols'))} 个)。"
    )


# 6) 各平台分布:KOL 池全量按平台分布(不吃区间)。
def _b_platform_dist(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT COALESCE(platform, '') AS platform, COUNT(*) AS kols, "
        "COALESCE(SUM(followers), 0) AS total_followers "
        "FROM vkpi_kol_pool GROUP BY COALESCE(platform, '') ORDER BY kols DESC"
    )
    return sql, ()


def _s_platform_dist(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    total = sum(_i(r.get("kols")) for r in rows)
    if total == 0:
        return "KOL 池当前为空。"
    top = rows[0]
    pct = 100.0 * _i(top.get("kols")) / total if total else 0.0
    return (
        f"KOL 池共 {total} 个账号,分布于 {len(rows)} 个平台,"
        f"{top.get('platform') or '?'} 占比最高({pct:.1f}%)。"
    )


# 7) 预算余量:月度预算 + provider 硬顶两套口径,按余量升序(最紧在前)。
def _b_budget_remaining(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT scope, limit_usd, spent_usd, remaining_usd FROM ("
        "SELECT 'monthly:' || budget_key AS scope, "
        "COALESCE(monthly_limit_usd, 0) AS limit_usd, "
        "COALESCE(current_month_spent, 0) AS spent_usd, "
        "COALESCE(monthly_limit_usd, 0) - COALESCE(current_month_spent, 0) AS remaining_usd "
        "FROM vkpi_budget_settings "
        "UNION ALL "
        "SELECT 'cap:' || scope AS scope, "
        "COALESCE(cap_usd, 0) AS limit_usd, "
        "COALESCE(current_spend, 0) AS spent_usd, "
        "COALESCE(cap_usd, 0) - COALESCE(current_spend, 0) AS remaining_usd "
        "FROM vkpi_provider_budget_caps"
        ") t ORDER BY remaining_usd ASC LIMIT ?"
    )
    return sql, (MAX_ROWS,)


def _s_budget_remaining(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    if not rows:
        return "尚未配置任何预算口径。"
    overspent = sum(1 for r in rows if _f(r.get("remaining_usd")) < 0)
    tight = rows[0]
    return (
        f"共 {len(rows)} 个预算口径,{overspent} 个已超限;"
        f"余量最紧的是 {tight.get('scope') or '?'}(剩 ${_f(tight.get('remaining_usd')):,.2f})。"
    )


# 8) 官号表现:18 官号最新快照(补齐表)+ 账号档案,按粉丝降序。
def _b_official_perf(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT f.channel_id AS channel_id, "
        "COALESCE(c.platform, '') AS platform, "
        "COALESCE(c.account_handle, '') AS handle, "
        "f.snapshot_date AS snapshot_date, "
        "COALESCE(f.followers, 0) AS followers, "
        "COALESCE(f.total_views, 0) AS total_views, "
        "f.engagement_rate AS engagement_rate "
        "FROM vkpi_channel_metrics_filled f "
        "LEFT JOIN vkpi_employee_channels c ON c.id = f.channel_id "
        "WHERE f.snapshot_date = ("
        "SELECT MAX(f2.snapshot_date) FROM vkpi_channel_metrics_filled f2 "
        "WHERE f2.channel_id = f.channel_id"
        ") ORDER BY followers DESC LIMIT 30"
    )
    return sql, ()


def _s_official_perf(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    if not rows:
        return "暂无官号快照数据。"
    total_followers = sum(_i(r.get("followers")) for r in rows)
    top = rows[0]
    return (
        f"{len(rows)} 个官号最新快照:总粉丝 {total_followers:,},"
        f"粉丝最多为 {top.get('handle') or top.get('channel_id')}"
        f"({top.get('platform') or '?'},{_i(top.get('followers')):,})。"
    )


# 9) 回复队列待办:按状态 x 平台统计,附最老一条时间。
def _b_reply_queue(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT COALESCE(status, '') AS status, COALESCE(platform, '') AS platform, "
        "COUNT(*) AS items, MIN(created_at) AS oldest_at "
        "FROM vkpi_reply_queue "
        "GROUP BY COALESCE(status, ''), COALESCE(platform, '') ORDER BY items DESC"
    )
    return sql, ()


def _s_reply_queue(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    total = sum(_i(r.get("items")) for r in rows)
    pending = sum(_i(r.get("items")) for r in rows if str(r.get("status", "")) == "pending")
    if total == 0:
        return "回复队列当前为空。"
    return f"回复队列共 {total} 条,其中待处理(pending){pending} 条。"


# 10) 预测积压:推荐结果台账里尚未对答案(未终局)的预测量。
def _b_forecast_backlog(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN outcome_finalized_at IS NULL THEN 1 ELSE 0 END) AS open_predictions, "
        "SUM(CASE WHEN outcome_finalized_at IS NOT NULL THEN 1 ELSE 0 END) AS finalized, "
        "MIN(recommended_at) AS oldest_recommended_at "
        "FROM vkpi_recommendation_outcomes"
    )
    return sql, ()


def _s_forecast_backlog(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    if not rows:
        return "预测台账为空。"
    r = rows[0]
    return (
        f"预测台账共 {_i(r.get('total'))} 条,"
        f"其中 {_i(r.get('open_predictions'))} 条尚未对答案(积压),"
        f"已终局 {_i(r.get('finalized'))} 条。"
    )


# 11) 报价覆盖率:KOL 池里有报价记录的账号占比。
def _b_quote_coverage(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT "
        "(SELECT COUNT(*) FROM vkpi_kol_pool) AS kols_total, "
        "(SELECT COUNT(DISTINCT kol_pool_id) FROM vkpi_kol_rates) AS kols_quoted, "
        "ROUND(100.0 * (SELECT COUNT(DISTINCT kol_pool_id) FROM vkpi_kol_rates) "
        "/ NULLIF((SELECT COUNT(*) FROM vkpi_kol_pool), 0), 1) AS coverage_pct"
    )
    return sql, ()


def _s_quote_coverage(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    if not rows:
        return "无法计算报价覆盖率。"
    r = rows[0]
    return (
        f"KOL 池 {_i(r.get('kols_total'))} 个账号中 {_i(r.get('kols_quoted'))} 个有报价记录,"
        f"覆盖率 {_f(r.get('coverage_pct')):.1f}%。"
    )


# 12) 活跃项目:未关闭、未删除、未取消的项目按阶段分布。
def _b_active_projects(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    sql = (
        "SELECT COALESCE(stage, '') AS stage, COUNT(*) AS projects, "
        "MAX(last_activity_at) AS last_activity_at "
        "FROM vkpi_projects "
        "WHERE closed_at IS NULL AND COALESCE(stage_status, '') <> 'deleted' "
        "AND COALESCE(stage, '') <> 'cancelled' "
        "GROUP BY COALESCE(stage, '') ORDER BY projects DESC"
    )
    return sql, ()


def _s_active_projects(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    total = sum(_i(r.get("projects")) for r in rows)
    if total == 0:
        return "当前没有活跃项目。"
    top = rows[0]
    return (
        f"当前活跃项目 {total} 个,"
        f"最多处于 {top.get('stage') or '?'} 阶段({_i(top.get('projects'))} 个)。"
    )


QUESTIONS: tuple[CannedQuestion, ...] = (
    CannedQuestion(
        key="spend_30d",
        title="近30天花费",
        description="业务成本台账 + AI 调用账按类目汇总(USD),void 记录剔除。",
        columns=("category", "entries", "amount_usd"),
        source_tables=("vkpi_cost_ledger", "vkpi_ai_cost_ledger"),
        build=_b_spend,
        summarize=_s_spend,
        uses_range=True,
    ),
    CannedQuestion(
        key="sample_value",
        title="送样价值",
        description="区间内送出产品/样品的件数与价值(成本台账 product/sample + 样品资产)。",
        columns=("source", "items", "value_usd"),
        source_tables=("vkpi_cost_ledger", "vkpi_sample_assets"),
        build=_b_sample_value,
        summarize=_s_sample_value,
        uses_range=True,
    ),
    CannedQuestion(
        key="top_output_kols",
        title="最佳产出 KOL",
        description="按视频证据聚合区间内每位 KOL 的视频数与观看/互动,降序取前20。",
        columns=("kol_pool_id", "kol", "platform", "videos", "views", "likes", "comments"),
        source_tables=("vkpi_kol_video_evidence", "vkpi_kol_pool"),
        build=_b_top_kols,
        summarize=_s_top_kols,
        uses_range=True,
    ),
    CannedQuestion(
        key="deep_analysis_done",
        title="深析完成量",
        description="区间内完成(status=ready)的 LLM 深度分析结果,按类型统计。",
        columns=("analysis_kind", "results", "kols_covered"),
        source_tables=("vkpi_kol_llm_deep_analysis_results",),
        build=_b_deep_analysis,
        summarize=_s_deep_analysis,
        uses_range=True,
    ),
    CannedQuestion(
        key="new_in_pool",
        title="新入池",
        description="区间内新入池 KOL 数,按平台分布。",
        columns=("platform", "new_kols"),
        source_tables=("vkpi_kol_pool",),
        build=_b_new_in_pool,
        summarize=_s_new_in_pool,
        uses_range=True,
    ),
    CannedQuestion(
        key="platform_distribution",
        title="各平台分布",
        description="KOL 池全量按平台分布(账号数 + 粉丝总量)。",
        columns=("platform", "kols", "total_followers"),
        source_tables=("vkpi_kol_pool",),
        build=_b_platform_dist,
        summarize=_s_platform_dist,
        uses_range=False,
    ),
    CannedQuestion(
        key="budget_remaining",
        title="预算余量",
        description="月度预算 + provider 硬顶两套口径的余量,最紧的排最前。",
        columns=("scope", "limit_usd", "spent_usd", "remaining_usd"),
        source_tables=("vkpi_budget_settings", "vkpi_provider_budget_caps"),
        build=_b_budget_remaining,
        summarize=_s_budget_remaining,
        uses_range=False,
    ),
    CannedQuestion(
        key="official_performance",
        title="官号表现",
        description="公司官号最新快照(粉丝/总观看/互动率),按粉丝降序。",
        columns=(
            "channel_id",
            "platform",
            "handle",
            "snapshot_date",
            "followers",
            "total_views",
            "engagement_rate",
        ),
        source_tables=("vkpi_channel_metrics_filled", "vkpi_employee_channels"),
        build=_b_official_perf,
        summarize=_s_official_perf,
        uses_range=False,
    ),
    CannedQuestion(
        key="reply_queue_todo",
        title="回复队列待办",
        description="评论回复队列按状态 x 平台统计,附最老一条时间。",
        columns=("status", "platform", "items", "oldest_at"),
        source_tables=("vkpi_reply_queue",),
        build=_b_reply_queue,
        summarize=_s_reply_queue,
        uses_range=False,
    ),
    CannedQuestion(
        key="forecast_backlog",
        title="预测积压",
        description="推荐预测台账中尚未对答案(未终局)的数量。",
        columns=("total", "open_predictions", "finalized", "oldest_recommended_at"),
        source_tables=("vkpi_recommendation_outcomes",),
        build=_b_forecast_backlog,
        summarize=_s_forecast_backlog,
        uses_range=False,
    ),
    CannedQuestion(
        key="quote_coverage",
        title="报价覆盖率",
        description="KOL 池中有报价记录的账号占比。",
        columns=("kols_total", "kols_quoted", "coverage_pct"),
        source_tables=("vkpi_kol_pool", "vkpi_kol_rates"),
        build=_b_quote_coverage,
        summarize=_s_quote_coverage,
        uses_range=False,
    ),
    CannedQuestion(
        key="active_projects",
        title="活跃项目",
        description="未关闭/未删除/未取消的项目按阶段分布。",
        columns=("stage", "projects", "last_activity_at"),
        source_tables=("vkpi_projects",),
        build=_b_active_projects,
        summarize=_s_active_projects,
        uses_range=False,
    ),
)

_QUESTION_BY_KEY: dict[str, CannedQuestion] = {q.key: q for q in QUESTIONS}


def list_questions() -> list[dict[str, Any]]:
    """12 问清单(供前端 chips / 帮助)。"""
    return [
        {
            "key": q.key,
            "title": q.title,
            "description": q.description,
            "columns": list(q.columns),
            "source_tables": list(q.source_tables),
            "uses_range": q.uses_range,
        }
        for q in QUESTIONS
    ]


def _rows_to_dicts(cursor_rows: Iterable[Any], columns: list[str]) -> list[dict[str, Any]]:
    """compat 游标行(dict / Row / tuple)统一为 {col: val};与 query_planner 同款容错。"""
    out: list[dict[str, Any]] = []
    for row in cursor_rows or []:
        if isinstance(row, dict):
            out.append({c: row.get(c) for c in columns})
            continue
        try:
            out.append({c: row[c] for c in columns})
            continue
        except (KeyError, IndexError, TypeError):
            pass
        try:
            out.append({c: row[i] for i, c in enumerate(columns)})
        except (IndexError, TypeError):
            out.append({c: None for c in columns})
    return out


def run(conn: Any, key: str, range_days: Any = DEFAULT_RANGE_DAYS) -> dict[str, Any]:
    """执行一个预设问题:确定性 SQL 聚合 → 结构化结果(带来源可追溯)。

    未知 key 抛 LookupError(API 层转 404);安全断言失败抛 ValueError(转 400)。
    """
    question = _QUESTION_BY_KEY.get(str(key or "").strip())
    if question is None:
        raise LookupError(f"unknown canned query key: {key}")

    rng = _clamp_range_days(range_days)
    ctx: dict[str, Any] = {"range_days": rng, "cutoff_iso": _cutoff_iso(rng)}
    sql, params = question.build(ctx)
    assert_safe_sql(sql)

    cursor = conn.execute(sql, params)
    raw = cursor.fetchall()
    columns = list(question.columns)
    rows = _rows_to_dicts(raw, columns)

    explain = f"key={question.key}; tables={sorted(question.source_tables)}"
    if question.uses_range:
        explain += f"; range_days={rng}"

    return {
        "key": question.key,
        "title": question.title,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "source_tables": list(question.source_tables),
        "summary": question.summarize(rows, ctx),
        "sql_explain": explain,
        "range_days": rng if question.uses_range else None,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
