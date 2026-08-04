"""N6 数据问数 · 白名单查询计划器(只读 / 加性新文件)。

把自然语言问题映射到【固定白名单意图集】,每个意图 = 一条预定义、参数化、只读
的安全 SQL(或聚合)。绝不拼接用户输入到 SQL;用户的自由文本只用于【意图匹配】
(关键词打分),从不进入查询字符串。区间/来源等参数全部通过 `?` 占位符以绑定参数
传入(本仓 compat 用 `?`,跨 sqlite/Postgres 翻译层统一处理)。

设计要点(遵循铁律):
  - 加性:本文件只新建,不改任何现有文件;不动 fit_score(零写)。
  - 只读:每条 SQL 必须以 SELECT 开头,且只触白名单表;运行期再次断言。
  - 防注入:用户问题永不进 SQL;意图键来自固定枚举;数值参数经 int 夹取。
  - 跨后端:WHERE 里不用各家方言的日期函数,改在 Python 侧算 cutoff 时间戳,
    以绑定参数 `?` 下推,避免 sqlite/Postgres 日期方言漂移。
  - BOOLEAN 读回可能是 int 1/0:本文件只 SELECT 原值回传,不做布尔判定。

对外主要接口:
  - list_intents() -> 可问问题清单(intent key / 标题 / 示例问法 / 参数说明)。
  - plan(question, range_days, source) -> 命中的 IntentPlan(含将执行的 SQL 解释)。
  - run(conn, question|intent, range_days, source) -> {intent, columns, rows, sql_explain}。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable


# --- 白名单:允许出现在任何意图 SQL 中的表名(运行期硬校验) ---------------
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "kols",
        "vkpi_sample_assets",
        "vkpi_shipments",
        "vkpi_content_posts",
        "vkpi_kol_claims",
        "vkpi_channel_metrics",
        "vkpi_kol_pool",
        "vkpi_kol_video_evidence",
        "vkpi_projects",
    }
)

# 区间默认与夹取范围(天)。
DEFAULT_RANGE_DAYS = 30
MIN_RANGE_DAYS = 1
MAX_RANGE_DAYS = 365

# 行数硬上限,防止意外全表回灌。
MAX_ROWS = 200


def _clamp_range_days(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RANGE_DAYS
    if n < MIN_RANGE_DAYS:
        return MIN_RANGE_DAYS
    if n > MAX_RANGE_DAYS:
        return MAX_RANGE_DAYS
    return n


def _cutoff_iso(range_days: int) -> str:
    """区间起点 = now - range_days,UTC ISO 字符串,以绑定参数下推。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=range_days)
    return cutoff.replace(microsecond=0).isoformat()


def _norm_country(source: Any) -> str | None:
    """来源/地区入参规整为短码(仅用于绑定参数,非拼接)。空 -> None。"""
    if source is None:
        return None
    s = str(source).strip().upper()
    if not s:
        return None
    # 只允许字母与少量分隔符,长度受限;非法直接丢弃(回退到无过滤)。
    if not re.fullmatch(r"[A-Z]{2,16}", s):
        return None
    return s


# --- 意图定义 ----------------------------------------------------------------
SqlBuilder = Callable[[dict[str, Any]], tuple[str, tuple[Any, ...], list[str]]]


@dataclass(frozen=True)
class Intent:
    key: str
    title: str
    description: str
    examples: tuple[str, ...]
    keywords: tuple[str, ...]
    columns: tuple[str, ...]
    build: SqlBuilder
    uses_range: bool = True
    uses_source: bool = False
    source_status: str = "ready"
    source_reason: str = ""


@dataclass(frozen=True)
class IntentPlan:
    intent: str
    title: str
    columns: list[str]
    sql: str
    params: tuple[Any, ...]
    sql_explain: str = ""
    source_status: str = "ready"
    source_reason: str = ""


# 每个 builder 返回 (sql, params, columns)。SQL 全部 SELECT、参数化、白名单表。
# 注意:列别名与 columns 元组一一对应,便于前端稳定取数。


def _b_sample_value_30d(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...], list[str]]:
    cutoff = ctx["cutoff_iso"]
    cols = ["sku", "samples", "total_cost_cents", "currency"]
    sql = (
        "SELECT COALESCE(product_sku, '') AS sku, "
        "COUNT(*) AS samples, "
        "COALESCE(SUM(sample_cost_cents), 0) AS total_cost_cents, "
        "COALESCE(MAX(currency), '') AS currency "
        "FROM vkpi_sample_assets "
        "WHERE created_at >= ? "
        "GROUP BY COALESCE(product_sku, '') "
        "ORDER BY total_cost_cents DESC "
        "LIMIT ?"
    )
    return sql, (cutoff, MAX_ROWS), cols


def _b_kol_content_roi(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...], list[str]]:
    cutoff = ctx["cutoff_iso"]
    cols = [
        "kol_id",
        "channel_name",
        "posts",
        "total_views",
        "total_likes",
        "total_comments",
    ]
    sql = (
        "SELECT p.kol_id AS kol_id, "
        "COALESCE(k.channel_name, '') AS channel_name, "
        "COUNT(*) AS posts, "
        "COALESCE(SUM(p.views), 0) AS total_views, "
        "COALESCE(SUM(p.likes), 0) AS total_likes, "
        "COALESCE(SUM(p.comments), 0) AS total_comments "
        "FROM vkpi_content_posts p "
        "LEFT JOIN kols k ON k.id = p.kol_id "
        "WHERE p.published_at >= ? "
        "GROUP BY p.kol_id, COALESCE(k.channel_name, '') "
        "ORDER BY total_views DESC "
        "LIMIT ?"
    )
    return sql, (cutoff, MAX_ROWS), cols


def _b_kol_pool_overview(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...], list[str]]:
    # total_kols 保留为旧前端读取的主字段，但口径升级为“去重后的主档案数”。
    # 其余字段为加性返回：调用方可以逐步展示原始行、重复行、视频证据覆盖和水位，
    # 老客户端继续读取前三列不会失配。视频覆盖直接由证据表 EXISTS 得出，避免
    # 依赖 Postgres 派生列而让正式 SQLite schema 在问数时崩溃。
    cols = [
        "total_kols",
        "platforms",
        "total_followers",
        "raw_records",
        "duplicate_records",
        "video_evidence_kols",
        "video_coverage_pct",
        "data_updated_at",
    ]
    sql = (
        "SELECT "
        "COALESCE(SUM(CASE WHEN duplicate_of_id IS NULL THEN 1 ELSE 0 END), 0) AS total_kols, "
        "COUNT(DISTINCT CASE WHEN duplicate_of_id IS NULL "
        "THEN NULLIF(TRIM(platform), '') END) AS platforms, "
        "COALESCE(SUM(CASE WHEN duplicate_of_id IS NULL THEN followers ELSE 0 END), 0) "
        "AS total_followers, "
        "COUNT(*) AS raw_records, "
        "COALESCE(SUM(CASE WHEN duplicate_of_id IS NOT NULL THEN 1 ELSE 0 END), 0) "
        "AS duplicate_records, "
        "COALESCE(SUM(CASE WHEN duplicate_of_id IS NULL "
        "AND EXISTS (SELECT 1 FROM vkpi_kol_video_evidence e "
        "WHERE e.kol_pool_id = vkpi_kol_pool.id AND e.is_active IS NOT FALSE) "
        "THEN 1 ELSE 0 END), 0) AS video_evidence_kols, "
        "COALESCE(ROUND(100.0 * SUM(CASE WHEN duplicate_of_id IS NULL "
        "AND EXISTS (SELECT 1 FROM vkpi_kol_video_evidence e "
        "WHERE e.kol_pool_id = vkpi_kol_pool.id AND e.is_active IS NOT FALSE) "
        "THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN duplicate_of_id IS NULL "
        "THEN 1 ELSE 0 END), 0), 1), 0) AS video_coverage_pct, "
        "MAX(CASE WHEN duplicate_of_id IS NULL THEN updated_at END) AS data_updated_at "
        "FROM vkpi_kol_pool"
    )
    return sql, (), cols


def _b_country_growth(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...], list[str]]:
    # vkpi_channel_metrics.channel_id 指向 vkpi_employee_channels.id，而不是 kols.id。
    # 当前官号档案没有经过核验的 country 维度，故不能诚实地回答“国家增长”。
    # 保留旧意图和列契约，但从正确的指标事实表 fail-closed 返回空集；调用方通过
    # source_status/source_reason 可区分“暂不支持”与真实零增长。
    cutoff = ctx["cutoff_iso"]
    cols = ["country", "kols", "followers_delta", "views_delta"]
    sql = (
        "SELECT '' AS country, 0 AS kols, 0 AS followers_delta, 0 AS views_delta "
        "FROM vkpi_channel_metrics m "
        "WHERE m.captured_at >= ? AND 1 = 0 "
        "LIMIT ?"
    )
    return sql, (cutoff, MAX_ROWS), cols


def _b_received_no_content(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...], list[str]]:
    cols = [
        "sample_asset_id",
        "project_id",
        "kol_id",
        "product_sku",
        "received_at",
    ]
    # 已签收(received_at 非空)但该 project 下尚无任何内容贴 = 待催发内容。
    sql = (
        "SELECT s.id AS sample_asset_id, "
        "s.project_id AS project_id, "
        "s.kol_id AS kol_id, "
        "COALESCE(s.product_sku, '') AS product_sku, "
        "s.received_at AS received_at "
        "FROM vkpi_sample_assets s "
        "WHERE s.received_at IS NOT NULL "
        "AND NOT EXISTS ("
        "SELECT 1 FROM vkpi_content_posts c WHERE c.project_id = s.project_id"
        ") "
        "ORDER BY s.received_at DESC "
        "LIMIT ?"
    )
    return sql, (MAX_ROWS,), cols


def _b_active_claims(ctx: dict[str, Any]) -> tuple[str, tuple[Any, ...], list[str]]:
    cols = ["staff_id", "active_claims"]
    # 活跃认领(status='active')按员工聚合 —— 团队产能视图。
    sql = (
        "SELECT staff_id AS staff_id, COUNT(*) AS active_claims "
        "FROM vkpi_kol_claims "
        "WHERE status = ? "
        "GROUP BY staff_id "
        "ORDER BY active_claims DESC "
        "LIMIT ?"
    )
    return sql, ("active", MAX_ROWS), cols


INTENTS: tuple[Intent, ...] = (
    Intent(
        key="sample_value_30d",
        title="近30天送样价值",
        description="按 SKU 汇总区间内送出样品的件数与样品成本(分)。",
        examples=("近30天送样价值", "这个月样品成本", "送了多少样 花了多少钱", "sample cost by sku"),
        keywords=("送样", "样品", "sample", "送了多少样", "样本", "寄样", "成本", "价值", "cost"),
        columns=("sku", "samples", "total_cost_cents", "currency"),
        build=_b_sample_value_30d,
        uses_range=True,
    ),
    Intent(
        key="kol_pool_overview",
        title="KOL 池规模概览",
        description="返回当前去重 KOL 数、平台/粉丝规模、原始与重复记录、视频证据覆盖及数据时间。",
        examples=(
            "KOL Pool 现在有多少人",
            "KOL池有多少账号",
            "KOL 池规模",
            "how many kols in pool",
        ),
        keywords=(
            "kol pool",
            "kol池",
            "kol 池",
            "kol账号总数",
            "kol 账号总数",
            "池子规模",
            "pool size",
            "kols in pool",
            "how many kols",
        ),
        columns=(
            "total_kols",
            "platforms",
            "total_followers",
            "raw_records",
            "duplicate_records",
            "video_evidence_kols",
            "video_coverage_pct",
            "data_updated_at",
        ),
        build=_b_kol_pool_overview,
        uses_range=False,
    ),
    Intent(
        key="kol_content_roi",
        title="KOL内容表现排名",
        description="按 KOL 聚合区间内内容贴的曝光/互动；未关联成本或收入，不代表 ROI。",
        examples=("KOL内容ROI排名", "哪个达人内容效果最好", "kol content roi", "达人贴文表现排行"),
        keywords=("roi", "内容", "排名", "效果", "表现", "贴文", "曝光", "互动", "ranking"),
        columns=("kol_id", "channel_name", "posts", "total_views", "total_likes", "total_comments"),
        build=_b_kol_content_roi,
        uses_range=True,
    ),
    Intent(
        key="country_growth",
        title="国家增长（数据源未就绪）",
        description="当前官号指标缺少经核验的国家维度；安全返回不可用状态，不生成伪增长。",
        examples=("某国增长", "美国市场涨粉", "country growth", "哪个国家增长快"),
        keywords=("增长", "涨粉", "国家", "地区", "市场", "growth", "country", "region", "涨播"),
        columns=("country", "kols", "followers_delta", "views_delta"),
        build=_b_country_growth,
        uses_range=True,
        uses_source=False,
        source_status="unavailable",
        source_reason="verified_country_dimension_missing",
    ),
    Intent(
        key="received_no_content",
        title="已签收未发内容",
        description="样品已签收(received_at 非空)但所属项目尚无任何内容贴 —— 待催发。",
        examples=("已签收未发内容", "签收了还没发视频", "received but no content", "样品到了没出内容"),
        keywords=("签收", "未发", "没发", "待发", "received", "no content", "催发", "已到", "没出内容"),
        columns=("sample_asset_id", "project_id", "kol_id", "product_sku", "received_at"),
        build=_b_received_no_content,
        uses_range=False,
    ),
    Intent(
        key="active_claims_by_staff",
        title="按员工统计活跃认领",
        description="活跃认领(status=active)按员工聚合,反映在手产能。",
        examples=("按员工统计活跃认领", "谁手上认领最多", "active claims by staff", "员工认领量"),
        keywords=("认领", "claim", "员工", "产能", "在手", "staff", "活跃", "active"),
        columns=("staff_id", "active_claims"),
        build=_b_active_claims,
        uses_range=False,
    ),
)

_INTENT_BY_KEY: dict[str, Intent] = {i.key: i for i in INTENTS}


# --- 意图匹配(只读用户文本,不入 SQL) -------------------------------------
def _tokenize(question: str) -> str:
    return str(question or "").strip().lower()


def match_intent(question: str) -> Intent | None:
    """按关键词命中数 + 示例相似度给意图打分,取最高分;无命中返回 None。"""
    q = _tokenize(question)
    if not q:
        return None
    best: Intent | None = None
    best_score = 0
    for intent in INTENTS:
        score = 0
        for kw in intent.keywords:
            if kw.lower() in q:
                score += 2
        for ex in intent.examples:
            if ex.lower() and ex.lower() in q:
                score += 3
        if score > best_score:
            best_score = score
            best = intent
    return best if best_score > 0 else None


def resolve_intent(question: str | None, intent_key: str | None) -> Intent | None:
    """优先显式 intent_key(必须在白名单),否则按问题文本匹配。"""
    if intent_key:
        return _INTENT_BY_KEY.get(str(intent_key).strip())
    return match_intent(question or "")


# --- 安全断言:运行期再次确认 SQL 只读且仅触白名单表 -------------------------
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|pragma|merge|replace)\b",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def assert_safe_sql(sql: str) -> None:
    s = " ".join(str(sql).split())
    low = s.lower().lstrip("( ")
    if not low.startswith("select"):
        raise ValueError("query plan must be a SELECT")
    if ";" in s.rstrip(";"):
        # 允许结尾分号,但禁止语句中分号(防多语句注入)。
        raise ValueError("query plan must be a single statement")
    if _FORBIDDEN.search(s):
        raise ValueError("query plan contains a forbidden keyword")
    for tbl in _TABLE_REF.findall(s):
        if tbl.lower() not in ALLOWED_TABLES:
            raise ValueError(f"table not in whitelist: {tbl}")


# --- 计划与执行 --------------------------------------------------------------
def build_plan(intent: Intent, range_days: int, source: Any) -> IntentPlan:
    rng = _clamp_range_days(range_days)
    ctx: dict[str, Any] = {
        "cutoff_iso": _cutoff_iso(rng),
        "range_days": rng,
        "country": _norm_country(source),
    }
    sql, params, columns = intent.build(ctx)
    assert_safe_sql(sql)
    explain = f"intent={intent.key}; tables={sorted(set(_TABLE_REF.findall(' '.join(sql.split()))))}"
    if intent.uses_range:
        explain += f"; range_days={rng}"
    if intent.uses_source and ctx["country"]:
        explain += f"; country={ctx['country']}"
    return IntentPlan(
        intent=intent.key,
        title=intent.title,
        columns=list(columns),
        sql=sql,
        params=params,
        sql_explain=explain,
        source_status=intent.source_status,
        source_reason=intent.source_reason,
    )


def _rows_to_dicts(cursor_rows: Iterable[Any], columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in cursor_rows or []:
        if isinstance(row, dict):
            out.append({c: row.get(c) for c in columns})
            continue
        # sqlite3.Row 或 tuple:按列名/位置取。
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


def run_plan(conn: Any, plan: IntentPlan) -> dict[str, Any]:
    """执行已构建的安全计划,回传结构化结果。conn 为 compat 连接(? 占位)。"""
    assert_safe_sql(plan.sql)  # 双保险
    cursor = conn.execute(plan.sql, plan.params)
    raw = cursor.fetchall()
    rows = _rows_to_dicts(raw, plan.columns)
    return {
        "intent": plan.intent,
        "title": plan.title,
        "columns": plan.columns,
        "rows": rows,
        "sql_explain": plan.sql_explain,
        "source_status": plan.source_status,
        "source_reason": plan.source_reason,
    }


def run(
    conn: Any,
    question: str | None = None,
    *,
    intent: str | None = None,
    range_days: int = DEFAULT_RANGE_DAYS,
    source: Any = None,
) -> dict[str, Any]:
    """端到端:解析意图 -> 构建白名单计划 -> 执行 -> 结构化结果。

    未命中意图时返回 intent=None 与可问问题提示,不抛(便于 API 层 200 友好回复)。
    """
    chosen = resolve_intent(question, intent)
    if chosen is None:
        return {
            "intent": None,
            "title": "",
            "columns": [],
            "rows": [],
            "sql_explain": "",
            "source_status": "not_matched",
            "source_reason": "intent_not_matched",
            "available_intents": [i.key for i in INTENTS],
        }
    plan = build_plan(chosen, range_days, source)
    return run_plan(conn, plan)


def list_intents() -> list[dict[str, Any]]:
    """可问问题清单(供前端下拉 / 帮助)。"""
    return [
        {
            "intent": i.key,
            "title": i.title,
            "description": i.description,
            "examples": list(i.examples),
            "columns": list(i.columns),
            "uses_range": i.uses_range,
            "uses_source": i.uses_source,
            "source_status": i.source_status,
            "source_reason": i.source_reason,
        }
        for i in INTENTS
    ]
