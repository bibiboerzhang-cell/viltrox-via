"""市场之声报表增强字段(voice_report_ext · V0h+V0i+V1.1)——10 组窗口敏感报表读数。

用途:给前端「市场之声」报表页补 sparkline / 环比药丸 / 情绪趋势双线 / 告警彩条 /
平台条形 / 产品线声量榜 / 热点话题榜 / 语言分布 / 竞品声量 所需的窗口敏感真数据
字段。挂独立端点 GET /api/admin/vkpi/market/voice-report-ext?month=YYYY-MM(与
voice-report 同前缀、同 require_tab 权限、同窗口口径:month 自然月 UTC,缺省近
30 天),对现有 voice-report 响应零改动 —— 全部字段增量新增,绝不破坏既有契约。

十组字段(每组独立 status,单组失败诚实降级 {status:"error"} 不拖累其余):
  kpi_series         窗口内按 UTC 日计数序列(comments=vkpi_comments,queue=vkpi_reply_queue);
                     日轴连续 0 填齐,前端 sparkline 直接画。
  kpi_prev           上一等长窗口同口径计数 + delta_pct;上窗为 0 → delta_pct=null
                     (前端诚实省略环比药丸,绝不编百分比)。
  sentiment_summary  vkpi_sentiment_results 经 vkpi_comments.sentiment_id 回链
                     (与 voice-feed sentiment 过滤同一条数据链),窗口 ≤35 天按日、
                     否则按 ISO 周(period_start=周一)聚合 pos/neg share 双线。
  alerts_state       纯读:voice_alerts.evaluate_voice_alerts 当前评估(默认 8h 窗/阈值 3,
                     只评估绝不推送)+ vkpi_action_inbox 里 dedupe_key 前缀
                     'voice_alert:' 的最近已推送行(只读)。
  platform_dist      窗口内 vkpi_comments 按 platform 分组计数。
  line_voice         产品线级声量榜:复用 focal_matrix.PRODUCT_LINES 词表分桶
                     (与月报 buckets.product_lines 同口径)× 情绪回链;
                     逐 SKU 关联不真实存在 → 如实停留在产品线级,绝不硬造 SKU 映射。
  topics             热点话题榜:窗口内评论正文命中 market_voice 真词族
                     (COMPLAINT_CATEGORIES 六类话题词 + WISH_TERMS 愿望词)的
                     评论数 + 环比上一等长窗(上窗 0 → delta_pct=null);Top 12 封顶。
                     热度口径=话题词命中,不叠负面线索过滤(热度≠抱怨)。
  language_dist      窗口内 vkpi_comments.language_detected 语言分桶;NULL/空串归
                     'und'(前端显示「待检」);是语言分布不是地理——geo 关联
                     不真实存在,绝不冒充按国家/市场。
  competitor_voice   竞品声量:复用 kol/competitor_exposure.py「百家饭指数」真实
                     口径(vkpi_analysis_cache 已 ready 的 video_analysis_final_v1
                     深析产物 × 视频×品牌去重),按 evidence 发布时间窗口过滤;
                     Viltrox 自家行 is_self=true;本块只出分布,不算 index 不折扣。
  prd_referrals      「已转产品部」窗口计数(vkpi_market_prd_referrals,迁移 234;
                     时间=created_at UTC)+ 日序列(0 填齐,sparkline 直画)+
                     环比上一等长窗;表未建 → status=empty 诚实空,0 也如实 0。

数据源(全只读,聚合逐一注明真实表名):vkpi_comments / vkpi_reply_queue /
vkpi_sentiment_results / vkpi_action_inbox / vkpi_analysis_cache /
vkpi_kol_video_evidence / vkpi_market_prd_referrals;产品线词表来自 focal_matrix(经 market_voice._product_lines
复用,ImportError 自动降级精简词表);话题词表来自 market_voice(lexicon_v0);
竞品品牌词表经 competitor_exposure 复用 competitor_text.load_competitor_brands。

诚实空态:算不出的字段返 null / 空数组,绝不编数;share 只在分母 > 0 时给,
delta_pct 只在上窗 > 0 时给。所有 LIMIT 双层封顶(SQL LIMIT ? + Python 切片)。
时间全 UTC,返回 ISO 绝对时间戳;日粒度 = AT TIME ZONE 'UTC' 后取日,零本地时区污染。

窗口右沿钳到 now(当月进行中口径):日轴/序列只到今天绝不产未来日 0 行;环比在
窗口未走完时 current=[since, now) 实际流逝窗、previous=上窗同等流逝长度
[prev_since, prev_since+(now-since)),部分窗绝不比整窗;整窗在 now 之后(选未来
月份)→ 窗口敏感九组诚实 empty(alerts_state 告警窗独立近 8h,不受影响)。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(前缀匹配用 strpos 参数化,
不用 LIKE);时间戳/日期读回可能是 str;conn 可注入(测试),缺省懒 import get_conn。

显示层宪法:返回体绝不含 author_handle / author_id / raw_data_json / author_avatar_url
等内部或个人字段(SELECT 列白名单 + 输出字段白名单双保险,契约测试静态审查)。

红线:纯读聚合,零写库、零 LLM、零外调;零触 viltrox_fit_score、不碰 rule_v0
(本模块不产生任何评分,输出仅供报表展示,不回写任何 KOL/SKU 打分链路)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.market.market_voice import (
    COMPLAINT_CATEGORIES,
    VOICE_METHOD,
    WISH_TERMS,
    _extract_focals,
    _int0,
    _matches_any,
    _parse_ts,
    _product_lines,
    _window,
)

logger = get_logger(__name__)

EXT_METHOD = "lexicon_v0_ext"

# ── 护栏常量(测试直接断言;全部 SQL LIMIT ? + Python 层二次封顶双保险)──
SERIES_ROWS_LIMIT = 400          # 日计数 GROUP BY 行封顶(日轴最长 366 天 + 余量)
SERIES_MAX_DAYS = 370            # 日轴长度 Python 层封顶
SENTIMENT_ROWS_LIMIT = 1600      # 日 × 情绪标签组合行封顶(370 天 × 4 标签 + 余量)
PLATFORM_ROWS_LIMIT = 20         # 平台分组 SQL 层封顶
PLATFORM_MAX_ITEMS = 20          # 平台条目 Python 层封顶
LINE_SCAN_LIMIT = 5000           # line_voice 扫描评论上限(与 market_voice SCAN 同款)
RECENT_ALERTS_LIMIT = 10         # 已推送告警最近条数(SQL + Python 双封顶)
DAY_GRANULARITY_MAX_DAYS = 35    # 窗口 ≤35 天 → 日粒度;更长 → ISO 周粒度
ALERT_DEDUPE_PREFIX = "voice_alert:"   # vkpi_action_inbox.dedupe_key 前缀(voice_alerts 同口径)
SENTIMENT_LABELS = ("positive", "negative", "neutral")
TOPIC_SCAN_LIMIT = 5000          # topics 扫描评论上限(本窗/上窗各一次;SQL+Python 双封顶)
TOPIC_MAX_ITEMS = 12             # 话题榜条目 Top 12 封顶(施工单口径)
LANGUAGE_ROWS_LIMIT = 40         # 语言分桶 SQL 层封顶(真库语言数远小于此)
LANGUAGE_MAX_ITEMS = 30          # 语言条目 Python 层封顶
UND_LANGUAGE = "und"             # NULL/空串语言归一桶(前端显示「待检」)
COMPETITOR_SCAN_LIMIT = 2000     # 竞品声量扫描已深析视频上限(SQL+Python 双封顶)
COMPETITOR_MAX_BRANDS = 12       # 竞品品牌条目封顶(与百家饭 breakdown[:12] 同款)

# ── SQL 常量(全参数化;零字面 percent;窗口/LIMIT 全下推)────────────────
# 计数口径与 market_voice._load_comments 同款:正文非空 + COALESCE(created_at, fetched_at)。
COMMENTS_DAY_SQL = """
    SELECT CAST((COALESCE(created_at, fetched_at) AT TIME ZONE 'UTC') AS DATE) AS day,
           COUNT(*) AS n
    FROM vkpi_comments
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

# 队列口径与 market_voice._load_intent_queue 同款:正文非空 + 入队时间 created_at。
QUEUE_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day,
           COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""

COMMENTS_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_comments
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
"""

QUEUE_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_reply_queue
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

# 情绪链 = vkpi_comments.sentiment_id → vkpi_sentiment_results.id(voice_feed 同链;
# 真库实测该回链与 s.comment_id 正向链行数一致:positive 627 / neutral 104 / negative 38)。
SENTIMENT_DAY_SQL = """
    SELECT CAST((COALESCE(c.created_at, c.fetched_at) AT TIME ZONE 'UTC') AS DATE) AS day,
           s.sentiment AS sentiment,
           COUNT(*) AS n
    FROM vkpi_comments c
    JOIN vkpi_sentiment_results s ON s.id = c.sentiment_id
    WHERE COALESCE(c.created_at, c.fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(c.created_at, c.fetched_at) < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1, 2
    ORDER BY 1
    LIMIT ?
"""

PLATFORM_DIST_SQL = """
    SELECT platform, COUNT(*) AS n
    FROM vkpi_comments
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
    GROUP BY platform
    ORDER BY n DESC
    LIMIT ?
"""

# 只取正文 + 情绪标签两列做产品线分桶;个人字段一列不进 SELECT(显示层宪法)。
LINE_VOICE_SELECT_SQL = """
    SELECT c.comment_text, s.sentiment AS sentiment
    FROM vkpi_comments c
    LEFT JOIN vkpi_sentiment_results s ON s.id = c.sentiment_id
    WHERE c.comment_text IS NOT NULL AND c.comment_text <> ''
      AND COALESCE(c.created_at, c.fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(c.created_at, c.fetched_at) < CAST(? AS TIMESTAMPTZ)
    ORDER BY c.id DESC
    LIMIT ?
"""

# 前缀匹配用 strpos 参数化(compat 红线:SQL 字符串零字面 percent,不用 LIKE)。
RECENT_ALERTS_SQL = """
    SELECT id, dedupe_key, title, priority, status, created_at
    FROM vkpi_action_inbox
    WHERE strpos(dedupe_key, ?) = 1
    ORDER BY id DESC
    LIMIT ?
"""

# topics 只取正文一列(显示层宪法);词表匹配全在 Python(零 LIKE 零字面 percent)。
TOPIC_TEXT_SQL = """
    SELECT comment_text
    FROM vkpi_comments
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
    ORDER BY id DESC
    LIMIT ?
"""

LANGUAGE_DIST_SQL = """
    SELECT language_detected, COUNT(*) AS n
    FROM vkpi_comments
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
    GROUP BY language_detected
    ORDER BY n DESC
    LIMIT ?
"""

# 竞品声量 = 百家饭指数(kol/competitor_exposure.py)同源同口径:final_v1 深析产物
# 的三个品牌字段(jsonb 路径抽取与 pool_detail.py 同款);只抽品牌三列,零个人字段。
# 窗口按 evidence 发布时间(published_at_norm 真库覆盖 480/480,publish_date 兜底);
# is_active IS NOT FALSE 与百家饭一致(归属纠错下线的 evidence 不计入)。
COMPETITOR_VOICE_SQL = """
    SELECT
        ac.result #>> '{raw_gemini_video,viltrox_detected}' AS viltrox_detected_text,
        ac.result #> '{raw_gemini_video,viltrox_products_all}' AS viltrox_products,
        ac.result #> '{raw_gemini_video,competitor_mentions}' AS competitor_mentions
    FROM vkpi_analysis_cache ac
    JOIN vkpi_kol_video_evidence e ON e.id = CAST(ac.target_id AS BIGINT)
    WHERE ac.target_type = 'video'
      AND ac.derive_method = 'video_analysis_final_v1'
      AND ac.status = 'ready'
      AND e.is_active IS NOT FALSE
      AND COALESCE(e.published_at_norm, e.publish_date) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(e.published_at_norm, e.publish_date) < CAST(? AS TIMESTAMPTZ)
    ORDER BY e.id DESC
    LIMIT ?
"""

# 「已转产品部」= vkpi_market_prd_referrals 转交账本(迁移 234;写路在 prd_referrals.py)。
# 探针参数化(compat 红线同款);计数/日序列时间锚 = created_at(转交时刻,UTC)。
PRD_TABLE = "vkpi_market_prd_referrals"
PRD_TABLE_PROBE_SQL = """
    SELECT table_name FROM information_schema.tables WHERE table_name = ? LIMIT 1
"""

PRD_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM vkpi_market_prd_referrals
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
"""

PRD_DAY_SQL = """
    SELECT CAST((created_at AT TIME ZONE 'UTC') AS DATE) AS day,
           COUNT(*) AS n
    FROM vkpi_market_prd_referrals
    WHERE created_at >= CAST(? AS TIMESTAMPTZ)
      AND created_at < CAST(? AS TIMESTAMPTZ)
    GROUP BY 1
    ORDER BY 1
    LIMIT ?
"""


# ── 小工具 ──────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    """当前 UTC 时刻(独立缝:「当月进行中」契约测试 monkeypatch 冻结 now)。"""
    return datetime.now(timezone.utc)


def _elapsed_until(since: str, until: str) -> str | None:
    """窗口右沿钳到 now → min(until, now) 的 ISO;整窗在 now 之后 → None(诚实空)。

    月份窗右沿可以在未来(「当月进行中」选当月 → until=下月 1 日):未来时段
    不真实存在,日轴/计数/环比只覆盖 [since, now) 已流逝部分——未来日 0 填装有、
    部分窗比整窗,都是编数据。解析不了的窗口原样透传(下游各自诚实降级)。
    """
    since_dt, until_dt = _parse_ts(since), _parse_ts(until)
    if since_dt is None or until_dt is None:
        return until
    now = _now_utc()
    if now <= since_dt:
        return None
    return until if now >= until_dt else now.astimezone(timezone.utc).isoformat()


def _day_str(value: Any) -> str:
    """DB 读回的日期(date / datetime / str)→ 'YYYY-MM-DD';解不了返回 ''(诚实丢弃)。"""
    if isinstance(value, datetime):        # datetime 是 date 子类,必须先判
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _iso_utc(value: Any) -> str | None:
    """时间戳 → ISO-8601 UTC(Z 后缀);compat 可能读回 str,原样透传修 Z。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text.replace("+00:00", "Z") if text else None


def _share(part: int, total: int) -> float | None:
    """占比;分母为 0 → null(诚实空态,绝不编 0% 或 100%)。"""
    if total <= 0:
        return None
    return round(part / total, 4)


def _delta_pct(current: int, previous: int) -> float | None:
    """环比百分比;上窗为 0 或缺数据 → null(前端诚实省略药丸)。"""
    if previous <= 0:
        return None
    return round((current - previous) * 100.0 / previous, 1)


def _day_axis(since_iso: str, until_iso: str) -> list[str]:
    """[since, min(until, now)) 覆盖到的连续 UTC 日轴(封顶 SERIES_MAX_DAYS)。

    右沿钳到 now:未来日不真实存在,日轴只到今天(未来日 0 填装有 = 编数据);
    整窗在 now 之后 → 空轴(诚实空)。
    """
    since_dt, until_dt = _parse_ts(since_iso), _parse_ts(until_iso)
    if since_dt is None or until_dt is None:
        return []
    until_dt = min(until_dt, _now_utc())
    if until_dt <= since_dt:
        return []
    cursor = since_dt.astimezone(timezone.utc).date()
    last = (until_dt.astimezone(timezone.utc) - timedelta(microseconds=1)).date()
    out: list[str] = []
    while cursor <= last and len(out) < SERIES_MAX_DAYS:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def _period_key(day_iso: str, granularity: str) -> str:
    """日期 → 聚合期起点;day=原日,week=该 ISO 周的周一。"""
    if granularity != "week":
        return day_iso
    d = date.fromisoformat(day_iso)
    return (d - timedelta(days=d.weekday())).isoformat()


def _day_counts(conn: Any, sql: str, since: str, until: str) -> dict[str, int]:
    """按日计数 SQL → {'YYYY-MM-DD': n};行数 SQL/Python 双封顶。"""
    rows = conn.execute(sql, (since, until, SERIES_ROWS_LIMIT)).fetchall()
    counts: dict[str, int] = {}
    for r in list(rows)[:SERIES_ROWS_LIMIT]:
        day = _day_str(dict(r).get("day"))
        if day:
            counts[day] = _int0(dict(r).get("n"))
    return counts


def _scalar_count(conn: Any, sql: str, since: str, until: str) -> int:
    row = conn.execute(sql, (since, until)).fetchone()
    return _int0(dict(row or {}).get("n"))


# ── 六组字段构建器(每组单一职责;真实表名写进 basis)─────────────────────


def _kpi_series(conn: Any, since: str, until: str) -> dict[str, Any]:
    """1. kpi_series:窗口内按 UTC 日计数序列,日轴连续 0 填齐(sparkline 直接画)。"""
    axis = _day_axis(since, until)
    comment_counts = _day_counts(conn, COMMENTS_DAY_SQL, since, until)
    queue_counts = _day_counts(conn, QUEUE_DAY_SQL, since, until)
    return {
        "status": "ready" if axis else "empty",
        "granularity": "day",
        "series": {
            "comments": [{"date": d, "count": comment_counts.get(d, 0)} for d in axis],
            "queue": [{"date": d, "count": queue_counts.get(d, 0)} for d in axis],
        },
        "basis": {
            "comments": "vkpi_comments(正文非空;时间=COALESCE(created_at,fetched_at) 的 UTC 日,与月报 samples 同口径)",
            "queue": "vkpi_reply_queue(正文非空;时间=created_at 入队时间的 UTC 日)",
        },
    }


def _prev_compare_window(since: str, until: str, until_eff: str) -> tuple[str, str] | None:
    """环比对照窗 (prev_since, prev_until);解析失败/零流逝 → None(诚实空)。

    上窗起点按完整窗长定位(prev_since = since - span);窗口右沿超 now
    (当月进行中)时只比同等流逝长度:current=[since, now) vs
    prev=[prev_since, prev_since+(now-since)) —— 部分窗绝不比整窗(环比失真修复);
    完整已过窗 elapsed==span,退化为原有整窗对整窗。
    """
    since_dt, until_dt, eff_dt = _parse_ts(since), _parse_ts(until), _parse_ts(until_eff)
    if since_dt is None or until_dt is None or eff_dt is None:
        return None
    if until_dt <= since_dt or eff_dt <= since_dt:
        return None
    span = until_dt - since_dt          # 完整窗长(定位上窗起点)
    elapsed = min(eff_dt, until_dt) - since_dt   # 实际流逝长(两窗同长对比)
    prev_since_dt = since_dt - span
    return prev_since_dt.isoformat(), (prev_since_dt + elapsed).isoformat()


def _kpi_prev(conn: Any, since: str, until: str, until_eff: str) -> dict[str, Any]:
    """2. kpi_prev:上一等长窗口同口径计数 + delta_pct(上窗 0 → null)。

    当月进行中(until 超 now):current 取 [since, now) 实际流逝窗,
    previous 取上窗同等流逝长度(_prev_compare_window)——绝不拿部分窗比整窗。
    """
    prev_window = _prev_compare_window(since, until, until_eff)
    if prev_window is None:
        return {"status": "empty", "reason": "窗口时间解析失败,环比无从算起,诚实空。"}
    prev_since, prev_until = prev_window
    metrics: dict[str, Any] = {}
    for name, sql, table in (
        ("comments", COMMENTS_COUNT_SQL, "vkpi_comments"),
        ("queue", QUEUE_COUNT_SQL, "vkpi_reply_queue"),
    ):
        current = _scalar_count(conn, sql, since, until_eff)
        previous = _scalar_count(conn, sql, prev_since, prev_until)
        metrics[name] = {
            "current": current,
            "previous": previous,
            "delta_pct": _delta_pct(current, previous),
            "table": table,
        }
    return {
        "status": "ready",
        "window_prev": {"since": prev_since, "until": prev_until},
        "metrics": metrics,
        "basis": (
            "上一等长窗口同口径 COUNT;上窗为 0 → delta_pct=null(前端诚实省略环比药丸);"
            "窗口进行中时两侧都只取已流逝等长时段,不拿部分窗比整窗"
        ),
    }


def _sentiment_summary(conn: Any, since: str, until: str) -> dict[str, Any]:
    """3. sentiment_summary:情绪总量 + pos/neg share + 趋势双线(日/周粒度按窗口长度定)。"""
    axis = _day_axis(since, until)
    granularity = "day" if len(axis) <= DAY_GRANULARITY_MAX_DAYS else "week"
    rows = conn.execute(SENTIMENT_DAY_SQL, (since, until, SENTIMENT_ROWS_LIMIT)).fetchall()

    totals = {"positive": 0, "negative": 0, "neutral": 0, "other": 0}
    per_period: dict[str, dict[str, int]] = {}
    for r in list(rows)[:SENTIMENT_ROWS_LIMIT]:
        rec = dict(r)
        day = _day_str(rec.get("day"))
        if not day:
            continue
        label = str(rec.get("sentiment") or "").strip().lower()
        label = label if label in SENTIMENT_LABELS else "other"
        n = _int0(rec.get("n"))
        totals[label] += n
        bucket = per_period.setdefault(
            _period_key(day, granularity),
            {"positive": 0, "negative": 0, "neutral": 0, "other": 0},
        )
        bucket[label] += n

    done_total = sum(totals.values())
    period_axis: list[str] = []
    seen: set[str] = set()
    for d in axis:
        key = _period_key(d, granularity)
        if key not in seen:
            seen.add(key)
            period_axis.append(key)
    trend = []
    for key in period_axis:
        bucket = per_period.get(key)
        total = sum(bucket.values()) if bucket else 0
        trend.append({
            "period_start": key,
            "total": total,
            "pos_share": _share(bucket["positive"], total) if bucket else None,
            "neg_share": _share(bucket["negative"], total) if bucket else None,
        })

    body: dict[str, Any] = {
        "status": "ready" if done_total else "empty",
        "done_total": done_total,
        "pos_total": totals["positive"],
        "neu_total": totals["neutral"],
        "neg_total": totals["negative"],
        "pos_share": _share(totals["positive"], done_total),
        "neg_share": _share(totals["negative"], done_total),
        "granularity": granularity,
        "trend": trend,
        "basis": (
            "vkpi_sentiment_results JOIN vkpi_comments ON s.id=c.sentiment_id"
            "(与 voice-feed sentiment 过滤同一条回链);时间=COALESCE(c.created_at,c.fetched_at) UTC;"
            f"窗口 ≤{DAY_GRANULARITY_MAX_DAYS} 天按日聚合,更长按 ISO 周(period_start=周一);"
            "share 分母=该期已批注条数,空期 share=null"
        ),
    }
    if not done_total:
        body["reason"] = "窗口内无已批注情绪结果(vkpi_comments.sentiment_id 回链零行)——诚实空,不编。"
    return body


def _alerts_state(conn: Any) -> dict[str, Any]:
    """4. alerts_state:纯读——当前告警评估(evaluate,绝不推送)+ 最近已推送 inbox 行。"""
    from app.domains.market.voice_alerts import evaluate_voice_alerts

    evaluation = evaluate_voice_alerts(conn=conn)  # 默认 8h 窗/阈值 3;纯读 vkpi_comments
    window_hours = _int0(evaluation.get("window_hours"))
    threshold = _int0(evaluation.get("threshold"))
    categories = [
        {
            "category": str(c.get("key") or ""),
            "label": str(c.get("label") or ""),
            "negative_count": _int0(c.get("count")),
            "score": _int0(c.get("score")),
            "threshold": threshold,
            "triggered": bool(c.get("triggered")),
            "window_hours": window_hours,
        }
        for c in (evaluation.get("categories") or [])
    ]

    rows = conn.execute(RECENT_ALERTS_SQL, (ALERT_DEDUPE_PREFIX, RECENT_ALERTS_LIMIT)).fetchall()
    recent_pushed = []
    for r in list(rows)[:RECENT_ALERTS_LIMIT]:
        rec = dict(r)
        recent_pushed.append({
            "id": _int0(rec.get("id")),
            "dedupe_key": str(rec.get("dedupe_key") or ""),
            "title": str(rec.get("title") or ""),
            "priority": str(rec.get("priority") or ""),
            "status": str(rec.get("status") or ""),
            "created_at": _iso_utc(rec.get("created_at")),
        })

    return {
        "status": "ok",
        "method": str(evaluation.get("method") or ""),
        "window_hours": window_hours,
        "threshold": threshold,
        "since": evaluation.get("since"),
        "until": evaluation.get("until"),
        "scanned": _int0(evaluation.get("scanned")),
        "categories": categories,
        "recent_pushed": recent_pushed,
        "basis": (
            "评估=voice_alerts.evaluate_voice_alerts(纯读 vkpi_comments,本端点绝不推送);"
            "已推送=vkpi_action_inbox(dedupe_key 前缀 voice_alert:,只读);"
            "告警窗独立于报表窗(默认近 8h)"
        ),
    }


def _platform_dist(conn: Any, since: str, until: str) -> dict[str, Any]:
    """5. platform_dist:窗口内按 platform 分组计数(条形图)。"""
    rows = conn.execute(PLATFORM_DIST_SQL, (since, until, PLATFORM_ROWS_LIMIT)).fetchall()
    items = [
        {"platform": str(dict(r).get("platform") or "unknown"), "count": _int0(dict(r).get("n"))}
        for r in list(rows)[:PLATFORM_MAX_ITEMS]
    ]
    return {
        "status": "ready" if items else "empty",
        "items": items,
        "basis": "vkpi_comments 窗口内 GROUP BY platform(正文非空;时间=COALESCE(created_at,fetched_at))",
    }


def _line_voice(conn: Any, since: str, until: str) -> dict[str, Any]:
    """6. line_voice:产品线级声量榜(词表分桶 × 情绪回链);无逐 SKU 真实关联,不下钻。"""
    rows = conn.execute(LINE_VOICE_SELECT_SQL, (since, until, LINE_SCAN_LIMIT)).fetchall()
    docs: list[dict[str, Any]] = []
    for r in list(rows)[:LINE_SCAN_LIMIT]:
        rec = dict(r)
        text = " ".join(str(rec.get("comment_text") or "").split())
        if not text:
            continue
        docs.append({
            "lower": text.lower(),
            "sentiment": str(rec.get("sentiment") or "").strip().lower(),
        })

    items: list[dict[str, Any]] = []
    for key, label, terms in _product_lines():
        hits = [d for d in docs if _matches_any(d["lower"], tuple(terms))]
        if key == "af_lens":
            # 与月报 buckets 同口径:提到具体焦段即视作镜头内容
            matched = {id(d) for d in hits}
            hits = hits + [d for d in docs if id(d) not in matched and _extract_focals(d["lower"])]
        annotated = [d for d in hits if d["sentiment"]]
        pos = sum(1 for d in annotated if d["sentiment"] == "positive")
        items.append({
            "key": key,
            "label": label,
            "count": len(hits),
            "annotated": len(annotated),
            "pos_share": _share(pos, len(annotated)),
        })
    items.sort(key=lambda x: (-_int0(x.get("count")), str(x.get("key"))))
    return {
        "status": "ready" if any(_int0(i.get("count")) for i in items) else "empty",
        "items": items,
        "basis": (
            "vkpi_comments 窗口原声 × focal_matrix.PRODUCT_LINES 词表分桶"
            "(与月报 buckets.product_lines 同口径,af_lens 含焦段命中)× "
            "vkpi_sentiment_results 情绪回链(c.sentiment_id);pos_share 分母=该桶已批注条数,"
            "零批注 → null;逐 SKU 关联不真实存在 → 停留在产品线级,不造 SKU 映射"
        ),
    }


def _topic_families() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """话题词族 = market_voice 真词表:六类抱怨话题词 + 愿望词(lexicon_v0 同源)。"""
    families = [(key, label, tuple(terms)) for key, label, terms in COMPLAINT_CATEGORIES]
    families.append(("wishlist", "愿望/求出", tuple(WISH_TERMS)))
    return tuple(families)


def _topic_hit_counts(conn: Any, since: str, until: str) -> tuple[dict[str, int], int]:
    """窗口内逐词族命中评论数;返回 (counts, 实际扫描条数);SQL/Python 双封顶。"""
    rows = conn.execute(TOPIC_TEXT_SQL, (since, until, TOPIC_SCAN_LIMIT)).fetchall()
    lowers: list[str] = []
    for r in list(rows)[:TOPIC_SCAN_LIMIT]:
        text = " ".join(str(dict(r).get("comment_text") or "").split()).lower()
        if text:
            lowers.append(text)
    counts = {
        key: sum(1 for low in lowers if _matches_any(low, terms))
        for key, _label, terms in _topic_families()
    }
    return counts, len(lowers)


def _topics(conn: Any, since: str, until: str, until_eff: str) -> dict[str, Any]:
    """7. topics:热点话题榜 [{key,label,count,delta_pct}];环比上一等长窗,Top 12 封顶。

    窗口进行中时环比两侧都只取已流逝等长时段(_prev_compare_window 同口径)。
    """
    prev_window = _prev_compare_window(since, until, until_eff)
    if prev_window is None:
        return {"status": "empty", "reason": "窗口时间解析失败,话题榜无从算起,诚实空。", "items": []}
    prev_since, prev_until = prev_window
    counts, scanned = _topic_hit_counts(conn, since, until_eff)
    prev_counts, _prev_scanned = _topic_hit_counts(conn, prev_since, prev_until)
    labels = {key: label for key, label, _terms in _topic_families()}
    items = [
        {"key": k, "label": labels[k], "count": n, "delta_pct": _delta_pct(n, prev_counts.get(k, 0))}
        for k, n in counts.items()
        if n > 0
    ]
    items.sort(key=lambda x: (-_int0(x.get("count")), str(x.get("key"))))
    body: dict[str, Any] = {
        "status": "ready" if items else "empty",
        "items": items[:TOPIC_MAX_ITEMS],
        "scanned": scanned,
        "window_prev": {"since": prev_since, "until": prev_until},
        "basis": (
            f"词表={VOICE_METHOD}(market_voice.COMPLAINT_CATEGORIES 六类话题词 + WISH_TERMS 愿望词);"
            "vkpi_comments 窗口正文(正文非空,时间=COALESCE(created_at,fetched_at))小写包含匹配,"
            "全在 Python(零 LIKE);热度口径=话题词命中,不叠负面线索过滤(热度≠抱怨);"
            "delta_pct=vs 上一等长窗同口径命中数,上窗 0 → null(诚实省略环比)"
        ),
    }
    if not items:
        body["reason"] = f"窗口内 {scanned} 条评论无一命中话题词族——诚实空榜,不编热度。"
    return body


def _language_dist(conn: Any, since: str, until: str) -> dict[str, Any]:
    """8. language_dist:语言分桶;NULL/空串归 'und'(待检);是语言分布不是地理。"""
    rows = conn.execute(LANGUAGE_DIST_SQL, (since, until, LANGUAGE_ROWS_LIMIT)).fetchall()
    counts: dict[str, int] = {}
    for r in list(rows)[:LANGUAGE_ROWS_LIMIT]:
        rec = dict(r)
        lang = str(rec.get("language_detected") or "").strip().lower() or UND_LANGUAGE
        counts[lang] = counts.get(lang, 0) + _int0(rec.get("n"))
    total = sum(counts.values())
    items = [{"language": k, "count": v} for k, v in counts.items()]
    items.sort(key=lambda x: (-_int0(x.get("count")), str(x.get("language"))))
    body: dict[str, Any] = {
        "status": "ready" if items else "empty",
        "items": items[:LANGUAGE_MAX_ITEMS],
        "total": total,
        "basis": (
            "vkpi_comments.language_detected 窗口分桶(正文非空,时间=COALESCE(created_at,fetched_at));"
            "NULL/空串归 'und'(未检出,前端显示「待检」);语言码原样 lower 透传;"
            "这是语言分布不是地理——评论无真实 geo 关联,绝不冒充按国家/市场"
        ),
    }
    if not items:
        body["reason"] = "窗口内无评论行——语言分布诚实空。"
    return body


def _competitor_voice(conn: Any, since: str, until: str) -> dict[str, Any]:
    """9. competitor_voice:竞品声量分布,复用百家饭指数(competitor_exposure)真实口径。

    视频×品牌去重:同一视频同一品牌只记 1 次;V=Viltrox 出现的视频数,
    C=Σ 每视频去重竞品品牌数;share 分母=V+C。只出分布不算 index(不做样本折扣)。
    """
    from app.domains.kol.competitor_exposure import (
        _display_brand,
        _known_brand_lookup,
        _loads as _ce_loads,
        _mention_brand_keys,
    )

    rows = conn.execute(COMPETITOR_VOICE_SQL, (since, until, COMPETITOR_SCAN_LIMIT)).fetchall()
    known = _known_brand_lookup()
    viltrox_videos = 0
    brand_counts: dict[str, int] = {}
    scanned = 0
    for r in list(rows)[:COMPETITOR_SCAN_LIMIT]:
        rec = dict(r)
        scanned += 1
        detected = str(rec.get("viltrox_detected_text") or "").strip().lower()
        products = _ce_loads(rec.get("viltrox_products"))
        has_products = isinstance(products, list) and any(str(p or "").strip() for p in products)
        mention_keys = _mention_brand_keys(rec.get("competitor_mentions"), known)
        if detected == "true" or has_products or "viltrox" in mention_keys:
            viltrox_videos += 1
        for key in mention_keys - {"viltrox"}:
            brand_counts[key] = brand_counts.get(key, 0) + 1
    competitor_exposures = sum(brand_counts.values())
    total = viltrox_videos + competitor_exposures

    ranked = sorted(brand_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:COMPETITOR_MAX_BRANDS]
    items: list[dict[str, Any]] = []
    if viltrox_videos:
        items.append({
            "brand": "Viltrox", "count": viltrox_videos,
            "share": _share(viltrox_videos, total), "is_self": True,
        })
    items.extend(
        {"brand": _display_brand(k), "count": n, "share": _share(n, total), "is_self": False}
        for k, n in ranked
    )
    body: dict[str, Any] = {
        "status": "ready" if items else "empty",
        "sample_videos": scanned,
        "viltrox_videos": viltrox_videos,
        "competitor_exposures": competitor_exposures,
        "items": items,
        "basis": (
            "vkpi_analysis_cache(derive_method=video_analysis_final_v1,status=ready)"
            "JOIN vkpi_kol_video_evidence(is_active IS NOT FALSE,"
            "窗口=COALESCE(published_at_norm,publish_date));视频×品牌去重口径与 "
            "kol/competitor_exposure.py 百家饭指数同款(品牌归一复用 competitor_text 词表);"
            "share 分母=Viltrox 视频数+竞品曝光数;本块只出分布,不算 index 不折扣;"
            "零 LLM 零新分析,纯读已深析产物"
        ),
    }
    if not items:
        body["reason"] = (
            f"窗口内 {scanned} 条已深析(final_v1)视频无任何品牌露出信号——诚实空。"
            if scanned else "窗口内无已深析(final_v1)视频——竞品声量诚实空,不编。"
        )
    return body


def _prd_referrals(conn: Any, since: str, until: str, until_eff: str) -> dict[str, Any]:
    """10. prd_referrals:「已转产品部」窗口计数 + 日序列 + 环比(表未建诚实 empty)。

    计数=窗口内转交行 COUNT(时间=created_at UTC);0 也如实 0(前端不再摆 pending 卡);
    环比=上一等长窗同口径,上窗 0 → delta_pct=null(诚实省略药丸)。窗口进行中时
    日轴只到今天、环比两侧同取已流逝等长时段(_prev_compare_window)。纯读,零写库。
    """
    probe = conn.execute(PRD_TABLE_PROBE_SQL, (PRD_TABLE,)).fetchone()
    if not probe:
        return {
            "status": "empty",
            "reason": "vkpi_market_prd_referrals 表未建(迁移 234 未 apply)——诚实空,不编计数。",
            "count": None,
            "series": [],
            "table": PRD_TABLE,
        }
    axis = _day_axis(since, until_eff)
    day_counts = _day_counts(conn, PRD_DAY_SQL, since, until_eff)
    current = _scalar_count(conn, PRD_COUNT_SQL, since, until_eff)
    previous: int | None = None
    delta: float | None = None
    prev_window = _prev_compare_window(since, until, until_eff)
    if prev_window is not None:
        previous = _scalar_count(conn, PRD_COUNT_SQL, prev_window[0], prev_window[1])
        delta = _delta_pct(current, previous)
    return {
        "status": "ready",
        "count": current,
        "previous": previous,
        "delta_pct": delta,
        "series": [{"date": d, "count": day_counts.get(d, 0)} for d in axis],
        "table": PRD_TABLE,
        "basis": (
            "vkpi_market_prd_referrals 窗口内转交行 COUNT(时间=created_at UTC,0 也如实 0);"
            "日序列 0 填齐可直画 sparkline(只到今天,不产未来日);"
            "环比=上一等长窗同口径,窗口进行中两侧同取已流逝等长,上窗 0 → delta_pct=null"
        ),
    }


# ── 主入口 ──────────────────────────────────────────────────────────────

FUTURE_WINDOW_REASON = "窗口整体在未来(尚未开始)——无已流逝时段,诚实空,不编 0 序列。"


def _future_window_groups() -> dict[str, Any]:
    """整窗在 now 之后(选了未来月份):窗口敏感九组诚实 empty,零窗口 SQL。

    未来没有数据,0 填序列/环比都是编数;每组保持各自契约形状(items/series/
    计数字段齐)。alerts_state 不在此列——它的告警窗独立于报表窗(恒当前近 8h),
    由调用方照常评估。
    """
    reason = FUTURE_WINDOW_REASON
    return {
        "kpi_series": {"status": "empty", "reason": reason, "granularity": "day",
                       "series": {"comments": [], "queue": []}},
        "kpi_prev": {"status": "empty", "reason": reason},
        "sentiment_summary": {"status": "empty", "reason": reason, "done_total": 0,
                              "pos_total": 0, "neu_total": 0, "neg_total": 0,
                              "pos_share": None, "neg_share": None,
                              "granularity": "day", "trend": []},
        "platform_dist": {"status": "empty", "reason": reason, "items": []},
        "line_voice": {"status": "empty", "reason": reason, "items": []},
        "topics": {"status": "empty", "reason": reason, "items": []},
        "language_dist": {"status": "empty", "reason": reason, "items": [], "total": 0},
        "competitor_voice": {"status": "empty", "reason": reason, "items": [],
                             "sample_videos": 0, "viltrox_videos": 0, "competitor_exposures": 0},
        "prd_referrals": {"status": "empty", "reason": reason, "count": None,
                          "series": [], "table": PRD_TABLE},
    }


def build_ext(
    month: str | None = None,
    *,
    conn: Any = None,
    window: tuple[str, str, str] | None = None,
) -> dict[str, Any]:
    """十组字段字典(仅 10 个键,可被上层 merge);单组失败降级 {status:'error'} 不拖全局。

    window=(since_iso, until_iso, label) 可直接注入(免二次算窗);缺省按 month 算
    (market_voice._window 同口径:month='YYYY-MM' 自然月 UTC / None=近 30 天,
    非法抛 ValueError → 路由转 400)。纯读;conn 可注入(测试),缺省懒取 get_conn。
    """
    from app.db.connection import get_conn

    since, until, _label = window if window else _window(month)
    db = conn or get_conn()

    # 窗口右沿钳到 now:当月进行中只算 [since, now) 已流逝部分(未来日 0 填装有
    # =编数据);整窗在未来 → 窗口敏感九组诚实 empty,零窗口 SQL。
    until_eff = _elapsed_until(since, until)
    future_window = until_eff is None
    if future_window:
        until_eff = until  # 不会被查询(窗口敏感组全走 _future_window_groups)

    builders: tuple[tuple[str, Any], ...] = (
        ("kpi_series", lambda: _kpi_series(db, since, until_eff)),
        ("kpi_prev", lambda: _kpi_prev(db, since, until, until_eff)),
        ("sentiment_summary", lambda: _sentiment_summary(db, since, until_eff)),
        ("alerts_state", lambda: _alerts_state(db)),
        ("platform_dist", lambda: _platform_dist(db, since, until_eff)),
        ("line_voice", lambda: _line_voice(db, since, until_eff)),
        ("topics", lambda: _topics(db, since, until, until_eff)),
        ("language_dist", lambda: _language_dist(db, since, until_eff)),
        ("competitor_voice", lambda: _competitor_voice(db, since, until_eff)),
        ("prd_referrals", lambda: _prd_referrals(db, since, until, until_eff)),
    )
    future_groups = _future_window_groups() if future_window else {}
    out: dict[str, Any] = {}
    for name, build in builders:
        if name in future_groups:  # alerts_state 告警窗独立(近 8h),不受未来月份影响
            out[name] = future_groups[name]
            continue
        try:
            out[name] = build()
        except Exception as exc:  # noqa: BLE001 — 单组失败诚实降级,不拖累其余各组
            logger.warning("voice_report_ext block %s failed: %s", name, exc)
            out[name] = {"status": "error", "reason": str(exc)[:200]}
    return out


def voice_report_ext(month: str | None = None, *, conn: Any = None) -> dict[str, Any]:
    """路由入口:窗口信封 + 十组字段(GET /api/admin/vkpi/market/voice-report-ext)。

    month='YYYY-MM' 自然月(UTC);None=近 30 天;格式非法抛 ValueError(路由转 400)。
    纯读、零 LLM、零写库;窗口口径与 voice-report 完全一致。
    """
    since, until, label = _window(month)
    body = build_ext(month, conn=conn, window=(since, until, label))
    return {
        "status": "ready",
        "month": month or None,
        "window": {"since": since, "until": until, "label": label},
        **body,
        "method": EXT_METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
