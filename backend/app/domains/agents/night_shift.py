"""B 夜班经理晨报(night_shift v1)—— 「昨晚我做了 X 件」纯读聚合。

morning_brief(window_hours=16) 聚合「昨天 16:00 → 今晨 8:00」(本地时区锚点,
按 UTC 存储换算;窗口小时数参数化,锚点=最近一次已过去的本地 8:00)系统完成的一切:

  sections(每段带真实计数与例证,零数据段诚实 0/empty,绝不杜撰):
    scraping       抓取任务:apify_jobs 窗口内按状态/类型分组计数 + done 平均耗时秒
    deep_analysis  深析完成:vkpi_analysis_cache status=ready 按 derive_method 分组
    comments       评论采集:vkpi_comments 窗口内新入库条数/覆盖视频数 + 采集 run 数
    kol_pool       新入池 KOL:vkpi_kol_pool created_at 窗口内 + 例证(粉丝数 TOP3)
    alerts         告警:窗口内新增按 severity 分组 + 当前 open 快照 + 最新例证
    budget         预算消耗:vkpi_ai_cost_ledger 按 provider 汇总 + vkpi_cost_ledger 业务成本
    anomalies      异常:窗口内失败任务 TOP3(带 last_error 原因)+ danger 告警数
    agents         (可选)兄弟件 D 预测台账摘要,import 失败安静缺席

  headline:「昨晚完成 X 件:抓取 a/深析 b/评论 c/新KOL d」;全零时诚实说静默。

数据源全只读:apify_jobs / vkpi_analysis_cache / vkpi_comments /
vkpi_comments_collection_runs / vkpi_kol_pool / vkpi_alerts /
vkpi_ai_cost_ledger / vkpi_cost_ledger。

compat 约定:SQL 占位符用 ?;SQL 全文零 percent 字符(不用 LIKE);BOOLEAN 读回
可能是 int 1/0(本模块未依赖布尔列);数值可能回 Decimal,统一 _num 转 float。
红线:纯读聚合零写库、零 LLM、零外部动作(只判定与展示,绝不真执行);
绝不写 viltrox_fit_score、不碰 rule_v0 —— 晨报维度对评分的影响永久为 NO,
本模块任何输出都不得回流评分链路。
本轮不建 cron:卡片加载即算(纯 SQL,3s 内);将来如挂每日 job,
入口函数即本模块 morning_brief(可序列化 dict,直接落 message/report)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
DEFAULT_WINDOW_HOURS = 16
MAX_WINDOW_HOURS = 168  # 一周封顶,防止误传大数扫全表


# ── 小工具 ──────────────────────────────────────────────────────────


def _num(value: Any, default: float = 0.0) -> float:
    """Decimal / str / None 容错转 float(compat 层数值双态)。"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _clamp_hours(window_hours: Any) -> int:
    hours = _int(window_hours, DEFAULT_WINDOW_HOURS)
    return max(1, min(hours, MAX_WINDOW_HOURS))


def _compute_window(window_hours: int) -> dict[str, Any]:
    """锚点=最近一次已过去的本地 8:00(晨报口径);窗口向前推 window_hours 小时。

    数据 UTC 存储,这里以本地时区定锚再换算 UTC 传参,避免 SQL 里做时区运算。
    """
    local_now = datetime.now().astimezone()
    anchor = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
    if local_now < anchor:
        anchor -= timedelta(days=1)
    end_local = anchor
    start_local = end_local - timedelta(hours=window_hours)
    end_utc = end_local.astimezone(timezone.utc)
    start_utc = start_local.astimezone(timezone.utc)
    return {
        "hours": window_hours,
        "anchor": "最近一次本地 08:00",
        "timezone": str(local_now.tzinfo),
        "start_local": start_local.isoformat(),
        "end_local": end_local.isoformat(),
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "_start": start_utc,  # 内部传参用,输出前剥离
        "_end": end_utc,
    }


def _safe_section(key: str, label: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """单段聚合失败不炸整报,诚实回 error+reason(增益块非阻塞)。"""
    try:
        section = fn()
    except Exception as exc:  # noqa: BLE001 — 晨报任何一段失败都要能出报
        logger.warning("morning_brief section failed key=%s: %s", key, exc)
        section = {"status": "error", "reason": _text(str(exc), 300)}
    section.setdefault("status", "ready")
    section["key"] = key
    section["label"] = label
    return section


# ── 各 section 聚合(全只读,窗口参数化)────────────────────────────


def _scraping_section(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """apify_jobs:窗口内按 updated_at 落窗的任务,状态/类型分组 + done 平均耗时。"""
    status_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS c FROM apify_jobs
        WHERE updated_at >= ? AND updated_at < ?
        GROUP BY status
        """,
        (start, end),
    ).fetchall()
    by_status = {_text(dict(r).get("status"), 30) or "unknown": _int(dict(r).get("c")) for r in status_rows}

    type_rows = conn.execute(
        """
        SELECT job_type, status, COUNT(*) AS c FROM apify_jobs
        WHERE updated_at >= ? AND updated_at < ?
        GROUP BY job_type, status
        ORDER BY c DESC
        LIMIT 20
        """,
        (start, end),
    ).fetchall()
    by_type: list[dict[str, Any]] = [
        {
            "job_type": _text(dict(r).get("job_type"), 40) or "unknown",
            "status": _text(dict(r).get("status"), 30) or "unknown",
            "count": _int(dict(r).get("c")),
        }
        for r in type_rows
    ]

    duration_row = conn.execute(
        """
        SELECT AVG(EXTRACT(EPOCH FROM (updated_at - started_at))) AS avg_s, COUNT(*) AS c
        FROM apify_jobs
        WHERE status = ? AND started_at IS NOT NULL
          AND updated_at >= ? AND updated_at < ?
        """,
        ("done", start, end),
    ).fetchone()
    duration = dict(duration_row) if duration_row else {}
    timed_count = _int(duration.get("c"))
    avg_seconds = round(_num(duration.get("avg_s")), 1) if timed_count > 0 else None

    done = by_status.get("done", 0)
    failed = by_status.get("failed", 0) + by_status.get("error", 0) + by_status.get("dead", 0)
    total = sum(by_status.values())
    if total == 0:
        return {"status": "empty", "reason": "窗口内 apify_jobs 无任何任务活动(计数为 0,非采集缺口即真安静)。",
                "done": 0, "failed": 0, "total": 0}
    return {
        "status": "ready",
        "done": done,
        "failed": failed,
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
        "avg_duration_seconds": avg_seconds,  # 仅统计有 started_at 的 done 任务;无则诚实 None
        "timed_sample": timed_count,
    }


def _deep_analysis_section(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """vkpi_analysis_cache:窗口内转 ready 的深析,按 derive_method 分组;final_v1 单列。"""
    rows = conn.execute(
        """
        SELECT derive_method, COUNT(*) AS c FROM vkpi_analysis_cache
        WHERE status = ? AND updated_at >= ? AND updated_at < ?
        GROUP BY derive_method
        ORDER BY c DESC
        LIMIT 15
        """,
        ("ready", start, end),
    ).fetchall()
    by_method = [
        {"derive_method": _text(dict(r).get("derive_method"), 60) or "unknown", "count": _int(dict(r).get("c"))}
        for r in rows
    ]
    final_v1 = next((m["count"] for m in by_method if m["derive_method"] == FINAL_V1_DERIVE_METHOD), 0)
    total = sum(m["count"] for m in by_method)
    if total == 0:
        return {"status": "empty", "reason": "窗口内无深析完成(vkpi_analysis_cache ready 计数为 0)。",
                "final_v1": 0, "total": 0}
    return {"status": "ready", "final_v1": final_v1, "total": total, "by_method": by_method}


def _comments_section(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """vkpi_comments 窗口内新入库(fetched_at)+ 采集 run 数(collection_runs)。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS c, COUNT(DISTINCT post_id) AS posts FROM vkpi_comments
        WHERE fetched_at >= ? AND fetched_at < ?
        """,
        (start, end),
    ).fetchone()
    data = dict(row) if row else {}
    new_comments = _int(data.get("c"))
    posts_covered = _int(data.get("posts"))

    run_row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM vkpi_comments_collection_runs
        WHERE created_at >= ? AND created_at < ?
        """,
        (start, end),
    ).fetchone()
    runs = _int(dict(run_row).get("c")) if run_row else 0

    if new_comments == 0 and runs == 0:
        return {"status": "empty", "reason": "窗口内无评论采集活动(新评论与采集 run 均为 0)。",
                "new_comments": 0, "posts_covered": 0, "collection_runs": 0}
    return {
        "status": "ready",
        "new_comments": new_comments,
        "posts_covered": posts_covered,
        "collection_runs": runs,
    }


def _kol_pool_section(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """vkpi_kol_pool 窗口内新建档(created_at)+ 粉丝数 TOP3 例证。只读档案列,零触评分列。"""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM vkpi_kol_pool WHERE created_at >= ? AND created_at < ?",
        (start, end),
    ).fetchone()
    new_count = _int(dict(row).get("c")) if row else 0
    if new_count == 0:
        return {"status": "empty", "reason": "窗口内无新入池 KOL(created_at 计数为 0)。", "new_count": 0}

    example_rows = conn.execute(
        """
        SELECT handle, display_name, platform, followers FROM vkpi_kol_pool
        WHERE created_at >= ? AND created_at < ?
        ORDER BY COALESCE(followers, 0) DESC, id DESC
        LIMIT 3
        """,
        (start, end),
    ).fetchall()
    examples = [
        {
            "handle": _text(dict(r).get("handle"), 80),
            "display_name": _text(dict(r).get("display_name"), 80),
            "platform": _text(dict(r).get("platform"), 30),
            "followers": _int(dict(r).get("followers"), 0) or None,
        }
        for r in example_rows
    ]
    return {"status": "ready", "new_count": new_count, "examples": examples}


def _alerts_section(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """vkpi_alerts:窗口内新增按 severity 分组 + 当前 open 快照(快照不限窗,如实标注)。"""
    rows = conn.execute(
        """
        SELECT severity, COUNT(*) AS c FROM vkpi_alerts
        WHERE created_at >= ? AND created_at < ?
        GROUP BY severity
        """,
        (start, end),
    ).fetchall()
    by_severity = {_text(dict(r).get("severity"), 20) or "unknown": _int(dict(r).get("c")) for r in rows}
    new_total = sum(by_severity.values())

    open_row = conn.execute(
        "SELECT COUNT(*) AS c FROM vkpi_alerts WHERE status = ?",
        ("open",),
    ).fetchone()
    open_now = _int(dict(open_row).get("c")) if open_row else 0

    example_rows = conn.execute(
        """
        SELECT severity, title, created_at FROM vkpi_alerts
        WHERE created_at >= ? AND created_at < ?
        ORDER BY created_at DESC
        LIMIT 3
        """,
        (start, end),
    ).fetchall()
    examples = [
        {
            "severity": _text(dict(r).get("severity"), 20),
            "title": _text(dict(r).get("title"), 120),
            "created_at": _iso(dict(r).get("created_at")),
        }
        for r in example_rows
    ]

    if new_total == 0:
        return {"status": "empty", "reason": "窗口内无新告警。", "new_total": 0,
                "open_now": open_now, "open_now_note": "open 为当前快照,不限窗口"}
    return {
        "status": "ready",
        "new_total": new_total,
        "by_severity": by_severity,
        "open_now": open_now,
        "open_now_note": "open 为当前快照,不限窗口",
        "examples": examples,
    }


def _budget_section(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """预算消耗:vkpi_ai_cost_ledger(AI 花费)按 provider 汇总 + vkpi_cost_ledger 业务成本。

    只读记账表,绝不触发任何扣费/外部动作;没有记录=诚实 0。
    """
    ai_rows = conn.execute(
        """
        SELECT ai_provider, SUM(cost_usd) AS usd, COUNT(*) AS c FROM vkpi_ai_cost_ledger
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY ai_provider
        ORDER BY usd DESC
        """,
        (start, end),
    ).fetchall()
    by_provider = [
        {
            "provider": _text(dict(r).get("ai_provider"), 30) or "unknown",
            "cost_usd": round(_num(dict(r).get("usd")), 4),
            "calls": _int(dict(r).get("c")),
        }
        for r in ai_rows
    ]
    ai_total_usd = round(sum(p["cost_usd"] for p in by_provider), 4)
    ai_calls = sum(p["calls"] for p in by_provider)

    biz_row = conn.execute(
        """
        SELECT SUM(amount_cents) AS cents, COUNT(*) AS c FROM vkpi_cost_ledger
        WHERE incurred_at >= ? AND incurred_at < ?
        """,
        (start, end),
    ).fetchone()
    biz = dict(biz_row) if biz_row else {}
    biz_cents = _int(biz.get("cents"))
    biz_entries = _int(biz.get("c"))

    if ai_calls == 0 and biz_entries == 0:
        return {"status": "empty", "reason": "窗口内无记账消耗(AI 记账与业务成本均为 0 条)。",
                "ai_total_usd": 0.0, "ai_calls": 0, "business_cost_cents": 0}
    return {
        "status": "ready",
        "ai_total_usd": ai_total_usd,
        "ai_calls": ai_calls,
        "by_provider": by_provider,
        "business_cost_cents": biz_cents,
        "business_entries": biz_entries,
    }


def _anomalies_section(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """异常:窗口内失败任务 TOP3(带原因)+ 窗口内 danger 告警数。只判定与展示,绝不重试/执行。"""
    fail_rows = conn.execute(
        """
        SELECT id, job_type, last_error, last_error_category, updated_at FROM apify_jobs
        WHERE status IN ('failed', 'error', 'dead')
          AND updated_at >= ? AND updated_at < ?
        ORDER BY updated_at DESC
        LIMIT 3
        """,
        (start, end),
    ).fetchall()
    failed_top = [
        {
            "job_id": _int(dict(r).get("id")),
            "job_type": _text(dict(r).get("job_type"), 40),
            "category": _text(dict(r).get("last_error_category"), 60) or None,
            "reason": _text(dict(r).get("last_error"), 200) or "(无错误文本)",
            "failed_at": _iso(dict(r).get("updated_at")),
        }
        for r in fail_rows
    ]

    count_row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM apify_jobs
        WHERE status IN ('failed', 'error', 'dead')
          AND updated_at >= ? AND updated_at < ?
        """,
        (start, end),
    ).fetchone()
    failed_total = _int(dict(count_row).get("c")) if count_row else 0

    danger_row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM vkpi_alerts
        WHERE severity = ? AND created_at >= ? AND created_at < ?
        """,
        ("danger", start, end),
    ).fetchone()
    danger_alerts = _int(dict(danger_row).get("c")) if danger_row else 0

    if failed_total == 0 and danger_alerts == 0:
        return {"status": "empty", "reason": "窗口内无失败任务、无 danger 告警 — 夜里没出事。",
                "failed_total": 0, "danger_alerts": 0, "failed_top": []}
    return {
        "status": "ready",
        "failed_total": failed_total,
        "failed_top": failed_top,
        "danger_alerts": danger_alerts,
    }


def _agents_section() -> dict[str, Any]:
    """兄弟件 D 预测台账摘要(跨代理契约:import 失败安静降级,绝不阻塞晨报)。"""
    try:
        from app.domains.agents import prediction_ledger  # noqa: PLC0415 — 兄弟件可能尚未落地
    except ImportError:
        return {"status": "empty", "reason": "预测台账(prediction_ledger)尚未落地,本段安静缺席。"}
    try:
        summary = prediction_ledger.ledger_summary()
        if not isinstance(summary, dict):
            return {"status": "empty", "reason": "预测台账返回非 dict,按缺席处理。"}
        return {"status": "ready", "ledger": summary}
    except Exception as exc:  # noqa: BLE001 — 兄弟件内部错误不外溢
        return {"status": "error", "reason": _text(str(exc), 200)}


# ── headline 组装 ────────────────────────────────────────────────────


def _build_headline(sections_by_key: dict[str, dict[str, Any]]) -> tuple[str, dict[str, int]]:
    scrape_done = _int(sections_by_key.get("scraping", {}).get("done"))
    deep_done = _int(sections_by_key.get("deep_analysis", {}).get("total"))
    comments_new = _int(sections_by_key.get("comments", {}).get("new_comments"))
    kol_new = _int(sections_by_key.get("kol_pool", {}).get("new_count"))
    alerts_new = _int(sections_by_key.get("alerts", {}).get("new_total"))
    failed = _int(sections_by_key.get("anomalies", {}).get("failed_total"))

    totals = {
        "scrape_done": scrape_done,
        "deep_done": deep_done,
        "comments_new": comments_new,
        "kol_new": kol_new,
        "alerts_new": alerts_new,
        "failed_jobs": failed,
    }
    completed = scrape_done + deep_done + comments_new + kol_new
    if completed == 0:
        headline = "昨晚系统静默:窗口内无新完成任务(诚实 0,非数据缺口即真安静)。"
        if failed > 0:
            headline = f"昨晚无新完成任务,但有 {failed} 个任务失败 — 见异常段。"
        return headline, totals

    parts: list[str] = []
    if scrape_done:
        parts.append(f"抓取 {scrape_done}")
    if deep_done:
        parts.append(f"深析 {deep_done}")
    if comments_new:
        parts.append(f"评论 {comments_new}")
    if kol_new:
        parts.append(f"新KOL {kol_new}")
    headline = f"昨晚完成 {completed} 件:" + "/".join(parts)
    if failed > 0:
        headline += f";失败 {failed} 见异常段"
    return headline, totals


# ── 主入口(将来每日 job 可直接挂本函数)─────────────────────────────


def morning_brief(window_hours: int = DEFAULT_WINDOW_HOURS, *, conn: Any = None) -> dict[str, Any]:
    """夜班经理晨报:窗口内系统完成的一切,纯 SQL 聚合按需计算(本轮不建 cron)。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    hours = _clamp_hours(window_hours)
    window = _compute_window(hours)
    start: datetime = window.pop("_start")
    end: datetime = window.pop("_end")

    sections = [
        _safe_section("scraping", "抓取任务", lambda: _scraping_section(db, start, end)),
        _safe_section("deep_analysis", "深析完成", lambda: _deep_analysis_section(db, start, end)),
        _safe_section("comments", "评论采集", lambda: _comments_section(db, start, end)),
        _safe_section("kol_pool", "新入池 KOL", lambda: _kol_pool_section(db, start, end)),
        _safe_section("alerts", "告警", lambda: _alerts_section(db, start, end)),
        _safe_section("budget", "预算消耗", lambda: _budget_section(db, start, end)),
        _safe_section("anomalies", "异常", lambda: _anomalies_section(db, start, end)),
        _safe_section("agents", "代理台账", _agents_section),
    ]
    sections_by_key = {s["key"]: s for s in sections}
    headline, totals = _build_headline(sections_by_key)

    return {
        "status": "ready",
        "window": window,
        "headline": headline,
        "totals": totals,
        "sections": sections,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "纯读聚合已有记账/任务/告警数据,零 LLM、零外部动作;独立展示信号,不参与任何评分。",
    }
