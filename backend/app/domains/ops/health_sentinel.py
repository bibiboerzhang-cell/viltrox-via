"""C1 数据健康哨兵 — 10 项黄金链路日检(只读检查 → persistent_cache 落库 → fail 汇总告警)。

诚实 by design:本模块只 READ 既有真实表,绝不修数据、不重排队、不触发同步、不执行运维动作。
仅有的两处写入:
  - persistent_cache:最新一次检查结果 JSON(latest 键)+ 按日历史(day 键,30 天自动过期),
    复用既有 KV 机制(003 baseline),零新表零迁移。
  - vkpi_alerts:存在 fail 项时经既有 upsert_alert 发一条汇总告警;alert_key 带 UTC 日期,
    同天重复跑只更新同一行(当天幂等,不重复发);当天全部恢复则自动 resolve。
零触 viltrox_fit_score / rule_v0。

10 项检查(单项独立 try/except,单项炸不影响其余;表缺失=warn 并注明迁移号):
  1 daily_sync               vkpi_sync_runs 最近 24h 是否有 completed 的每日增量同步(074)
  2 apify_queue              apify_jobs 最老 queued 任务年龄(堆积)(095)
  3 kol_hot_refresh          vkpi_kol_refresh_tier hot 层 24h 刷新增量(076)
  4 official_metrics         vkpi_channel_metrics 官号当日快照有无(030)
  5 search_entries           vkpi_kol_profile_index_entries 行数(099;历史全灭事故防复发)
  6 staff_roster             staff 活跃名单非空(012)
  7 llm_budget               vkpi_provider_budget_caps hard_stop + 当日 budget_blocked 拦截(057)
  8 apify_spend              vkpi_ai_cost_ledger 当日 apify 记账消耗 + apify_jobs 24h 失败率
  9 failed_pool              apify_jobs triage/failed 终态堆积(含 evidence 冲突被阻断任务的归宿,201)
 10 recommendation_outcomes  vkpi_recommendation_outcomes 总量 + 近 7 日新增(045)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_LATEST_KEY = "vkpi:health_sentinel:latest"
_DAY_KEY_PREFIX = "vkpi:health_sentinel:day:"
_ALERT_KEY_PREFIX = "health-sentinel-"
_RULE_KEY = "ops.health_sentinel"
_HISTORY_DAYS = 30

# 每日同步的 run 记录键(daily_sync.py 固定写这个 job_name)。
_DAILY_SYNC_JOB_NAME = "daily_incremental_sync"


# ──────────────────────────────────────────────
# 小工具(纯函数,容错优先)
# ──────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutoff_iso(delta: timedelta) -> str:
    return _iso(_utcnow() - delta)


def _parse_dt(value: Any) -> datetime | None:
    """TIMESTAMPTZ 读回可能是 datetime(PG)或 ISO 文本(compat);统一转 aware UTC,解析不了返回 None。"""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_hours(value: Any) -> float | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return max(0.0, (_utcnow() - parsed).total_seconds() / 3600.0)


def _row(conn: Any, sql: str, params: tuple = ()) -> dict[str, Any]:
    got = conn.execute(sql, params).fetchone()
    return dict(got) if got else {}


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float0(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _check(key: str, label: str, status: str, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _missing(key: str, label: str, table: str, migration: str) -> dict[str, Any]:
    return _check(key, label, "warn", f"{table} 表缺失(迁移 {migration} 未跑),无法检查")


# ──────────────────────────────────────────────
# 10 项检查(全部只读;返回 {key,label,status,detail})
# ──────────────────────────────────────────────


def _check_daily_sync() -> dict[str, Any]:
    key, label = "daily_sync", "每日同步 24h 内成功"
    if not table_exists("vkpi_sync_runs"):
        return _missing(key, label, "vkpi_sync_runs", "074")
    conn = get_conn()
    latest_ok = _row(
        conn,
        """
        SELECT run_id, started_at, finished_at
        FROM vkpi_sync_runs
        WHERE job_name=? AND status='completed'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (_DAILY_SYNC_JOB_NAME,),
    )
    if not latest_ok:
        return _check(key, label, "warn", "vkpi_sync_runs 里从未出现 completed 的每日同步(可能是新环境或同步从未跑通)")
    age = _age_hours(latest_ok.get("finished_at") or latest_ok.get("started_at"))
    when = str(latest_ok.get("finished_at") or latest_ok.get("started_at") or "")
    if age is not None and age <= 24:
        return _check(key, label, "ok", f"最近成功 {age:.1f} 小时前(run {latest_ok.get('run_id')})")
    latest_any = _row(
        conn,
        "SELECT status, reason FROM vkpi_sync_runs WHERE job_name=? ORDER BY started_at DESC LIMIT 1",
        (_DAILY_SYNC_JOB_NAME,),
    )
    tail = f";最近一次 run 状态 {latest_any.get('status')}" if latest_any else ""
    hours = f"{age:.0f} 小时" if age is not None else f"时间无法解析({when})"
    return _check(key, label, "fail", f"最近成功已是 {hours} 前,超过 24h 断更红线{tail}")


def _check_apify_queue() -> dict[str, Any]:
    key, label = "apify_queue", "Apify 队列堆积"
    if not table_exists("apify_jobs"):
        return _missing(key, label, "apify_jobs", "095")
    conn = get_conn()
    data = _row(
        conn,
        "SELECT COUNT(*) AS queued, MIN(created_at) AS oldest FROM apify_jobs WHERE status='queued'",
    )
    queued = _int0(data.get("queued"))
    if queued == 0:
        return _check(key, label, "ok", "队列无积压(0 条 queued)")
    age = _age_hours(data.get("oldest"))
    age_text = f"{age:.1f} 小时" if age is not None else "未知"
    if age is not None and age > 24:
        return _check(key, label, "fail", f"{queued} 条 queued,最老已等 {age_text}(worker 可能没在跑)")
    if age is not None and age > 6:
        return _check(key, label, "warn", f"{queued} 条 queued,最老已等 {age_text}")
    return _check(key, label, "ok", f"{queued} 条 queued,最老 {age_text},消化正常")


def _check_kol_hot_refresh() -> dict[str, Any]:
    key, label = "kol_hot_refresh", "KOL hot 层 24h 刷新增量"
    if not table_exists("vkpi_kol_refresh_tier"):
        return _missing(key, label, "vkpi_kol_refresh_tier", "076")
    conn = get_conn()
    cutoff = _cutoff_iso(timedelta(hours=24))
    data = _row(
        conn,
        """
        SELECT
            SUM(CASE WHEN tier='hot' OR manual_hot_flag THEN 1 ELSE 0 END) AS hot_total,
            SUM(CASE WHEN (tier='hot' OR manual_hot_flag) AND last_refresh_at >= ? THEN 1 ELSE 0 END) AS refreshed
        FROM vkpi_kol_refresh_tier
        """,
        (cutoff,),
    )
    hot_total = _int0(data.get("hot_total"))
    refreshed = _int0(data.get("refreshed"))
    if hot_total == 0:
        return _check(key, label, "warn", "hot 层名单为空(qualified 分层未跑或未标注),无增量可言")
    if refreshed == 0:
        return _check(key, label, "fail", f"hot 层 {hot_total} 个 KOL,24h 内刷新 0 个(每日 KOL 快照链断流)")
    return _check(key, label, "ok", f"hot 层 {hot_total} 个 KOL,24h 内刷新 {refreshed} 个")


def _check_official_metrics() -> dict[str, Any]:
    key, label = "official_metrics", "官号当日指标快照"
    if not table_exists("vkpi_channel_metrics"):
        return _missing(key, label, "vkpi_channel_metrics", "030")
    conn = get_conn()
    channels_total = 0
    if table_exists("vkpi_employee_channels"):
        channels_total = _int0(
            _row(conn, "SELECT COUNT(*) AS n FROM vkpi_employee_channels WHERE deleted_at IS NULL").get("n")
        )
    since = (_utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    data = _row(
        conn,
        "SELECT COUNT(*) AS n FROM vkpi_channel_metrics WHERE snapshot_date >= ?",
        (since,),
    )
    fresh = _int0(data.get("n"))
    if channels_total == 0:
        return _check(key, label, "warn", "未绑定任何官号(vkpi_employee_channels 为空),无快照可查")
    if fresh == 0:
        return _check(key, label, "fail", f"绑定 {channels_total} 个官号,但 {since} 以来 0 条快照(官号同步断流)")
    return _check(key, label, "ok", f"{since} 以来 {fresh} 条官号快照(共 {channels_total} 个官号)")


def _check_search_entries() -> dict[str, Any]:
    key, label = "search_entries", "文本搜索索引行数"
    if not table_exists("vkpi_kol_profile_index_entries"):
        return _missing(key, label, "vkpi_kol_profile_index_entries", "099")
    conn = get_conn()
    total = _int0(_row(conn, "SELECT COUNT(*) AS n FROM vkpi_kol_profile_index_entries").get("n"))
    if total == 0:
        return _check(key, label, "fail", "entries 表 0 行 — 文本搜索 join 全灭(历史事故复发),需重建索引")
    return _check(key, label, "ok", f"索引 {total} 行")


def _check_staff_roster() -> dict[str, Any]:
    key, label = "staff_roster", "员工名单非空"
    if not table_exists("staff"):
        return _missing(key, label, "staff", "012")
    conn = get_conn()
    data = _row(
        conn,
        # staff.active 是 INTEGER(迁移 012),PG 下 CASE WHEN 只吃 boolean,必须显式 = 1。
        "SELECT COUNT(*) AS total, SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active_n FROM staff",
    )
    total = _int0(data.get("total"))
    active = _int0(data.get("active_n"))
    if total == 0:
        return _check(key, label, "fail", "staff 表为空 — 身份真源丢失,全站按人过滤会全灭")
    if active == 0:
        return _check(key, label, "fail", f"staff 共 {total} 行但 0 个 active — 登录/授权链会全挡")
    return _check(key, label, "ok", f"{active} 个活跃员工(共 {total} 行)")


def _check_llm_budget() -> dict[str, Any]:
    key, label = "llm_budget", "LLM 预算闸状态"
    if not table_exists("vkpi_provider_budget_caps"):
        return _missing(key, label, "vkpi_provider_budget_caps", "057")
    from app.domains.costs import budget_guard

    status = budget_guard.get_budget_status()
    budgets = status.get("budgets") or []
    hard_stopped = [str(b.get("scope") or "") for b in budgets if b.get("hard_stopped")]
    warned = [str(b.get("scope") or "") for b in budgets if b.get("warning") and not b.get("hard_stopped")]
    blocked_today = 0
    if table_exists("vkpi_ai_cost_ledger"):
        day_start = _utcnow().strftime("%Y-%m-%dT00:00:00Z")
        blocked_today = _int0(
            _row(
                get_conn(),
                "SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger WHERE occurred_at >= ? AND metadata_json LIKE ?",
                (day_start, "%budget_blocked%"),
            ).get("n")
        )
    if "monthly_total" in hard_stopped:
        return _check(key, label, "fail", f"monthly_total 已 hard-stop — 全部外呼 LLM 被挡;今日拦截 {blocked_today} 次")
    if hard_stopped:
        return _check(key, label, "warn", f"hard-stop scope:{', '.join(sorted(hard_stopped))};今日拦截 {blocked_today} 次")
    if blocked_today > 0:
        return _check(key, label, "warn", f"今日有 {blocked_today} 次调用被预算闸拦下(scope 级)")
    tail = f";接近上限:{', '.join(sorted(warned))}" if warned else ""
    return _check(key, label, "ok", f"{len(budgets)} 个 scope 均未 hard-stop,今日 0 次拦截{tail}")


def _check_apify_spend() -> dict[str, Any]:
    key, label = "apify_spend", "Apify 当日记账消耗/失败率"
    if not table_exists("apify_jobs"):
        return _missing(key, label, "apify_jobs", "095")
    conn = get_conn()
    spend_text = "记账表缺失"
    if table_exists("vkpi_ai_cost_ledger"):
        day_start = _utcnow().strftime("%Y-%m-%dT00:00:00Z")
        ledger = _row(
            conn,
            """
            SELECT COUNT(*) AS runs, SUM(cost_usd) AS spend
            FROM vkpi_ai_cost_ledger
            WHERE ai_provider='apify' AND occurred_at >= ?
            """,
            (day_start,),
        )
        spend_text = f"今日记账 {_int0(ledger.get('runs'))} 笔 / ${_float0(ledger.get('spend')):.2f}"
    cutoff = _cutoff_iso(timedelta(hours=24))
    jobs = _row(
        conn,
        """
        SELECT
            SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
        FROM apify_jobs
        WHERE status IN ('done','failed') AND updated_at >= ?
        """,
        (cutoff,),
    )
    done = _int0(jobs.get("done"))
    failed = _int0(jobs.get("failed"))
    total = done + failed
    rate = (failed / total) if total else 0.0
    rate_text = f"24h 任务 {total} 条,失败率 {rate:.0%}"
    if total >= 10 and rate >= 0.5:
        return _check(key, label, "fail", f"{spend_text};{rate_text}(过半在烧钱失败)")
    if total >= 10 and rate >= 0.3:
        return _check(key, label, "warn", f"{spend_text};{rate_text}")
    return _check(key, label, "ok", f"{spend_text};{rate_text}")


def _check_failed_pool() -> dict[str, Any]:
    key, label = "failed_pool", "失败池/triage 堆积"
    if not table_exists("apify_jobs"):
        return _missing(key, label, "apify_jobs", "095")
    conn = get_conn()
    data = _row(
        conn,
        """
        SELECT
            SUM(CASE WHEN status='triage' THEN 1 ELSE 0 END) AS triage,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
        FROM apify_jobs
        """,
    )
    triage = _int0(data.get("triage"))
    failed = _int0(data.get("failed"))
    detail = f"triage {triage} 条 / failed {failed} 条(evidence 冲突等终态失败停在这里等人裁)"
    if triage >= 100 or failed >= 100:
        return _check(key, label, "fail", detail + " — 堆积过百,需排水")
    if triage > 0 or failed > 10:
        return _check(key, label, "warn", detail)
    return _check(key, label, "ok", detail)


def _check_recommendation_outcomes() -> dict[str, Any]:
    key, label = "recommendation_outcomes", "推荐 outcomes 新数据"
    if not table_exists("vkpi_recommendation_outcomes"):
        return _missing(key, label, "vkpi_recommendation_outcomes", "045")
    conn = get_conn()
    cutoff = _cutoff_iso(timedelta(days=7))
    data = _row(
        conn,
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN recommended_at >= ? THEN 1 ELSE 0 END) AS recent,
            SUM(CASE WHEN first_action_at IS NOT NULL THEN 1 ELSE 0 END) AS acted
        FROM vkpi_recommendation_outcomes
        """,
        (cutoff,),
    )
    total = _int0(data.get("total"))
    recent = _int0(data.get("recent"))
    acted = _int0(data.get("acted"))
    if total == 0:
        return _check(key, label, "warn", "outcome 表 0 行 — 学习闭环结果段无水(推荐刷新/outcome job 未跑)")
    if recent == 0:
        return _check(key, label, "warn", f"共 {total} 行但近 7 日 0 新增(刷新链可能停摆);有动作 {acted} 行")
    return _check(key, label, "ok", f"共 {total} 行,近 7 日新增 {recent},有动作 {acted} 行")


def _check_queue_inflow() -> dict[str, Any]:
    """第 11 检:自动入队任务已启用却 24h 零新增 → warn。
    疫苗:2026-07 队列断流三天,旧口径只看积压(空=绿)——断流和健康不可区分。"""
    label = "队列 24h 入队量"
    if not table_exists("apify_jobs"):
        return _missing("queue_inflow", label, "apify_jobs", "095")
    if not table_exists("scheduler_tasks"):
        return _check("queue_inflow", label, "ok", "scheduler_tasks 表缺失,无自动入队方,零新增属合规")
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM scheduler_tasks WHERE enabled = TRUE AND task_key IN (?, ?, ?)",
        ("kol_auto_poll", "vkpi_comment_sentiment_refresh", "vkpi_content_fit_batch"),
    ).fetchone()
    n_auto = int(dict(row).get("n") or 0) if row else 0
    if n_auto == 0:
        return _check("queue_inflow", label, "ok", "无自动入队任务启用,零新增属合规空跑")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute("SELECT COUNT(*) AS n FROM apify_jobs WHERE created_at >= ?", (cutoff,)).fetchone()
    n_new = int(dict(row).get("n") or 0) if row else 0
    if n_new == 0:
        return _check("queue_inflow", label, "warn", f"{n_auto} 个自动入队任务已启用但 24h 零新增——入队链疑似断流")
    return _check("queue_inflow", label, "ok", f"24h 新增 {n_new} 条(自动任务 {n_auto} 个在岗)")


_CHECKS: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("daily_sync", _check_daily_sync),
    ("apify_queue", _check_apify_queue),
    ("queue_inflow", _check_queue_inflow),
    ("kol_hot_refresh", _check_kol_hot_refresh),
    ("official_metrics", _check_official_metrics),
    ("search_entries", _check_search_entries),
    ("staff_roster", _check_staff_roster),
    ("llm_budget", _check_llm_budget),
    ("apify_spend", _check_apify_spend),
    ("failed_pool", _check_failed_pool),
    ("recommendation_outcomes", _check_recommendation_outcomes),
)

# 检查项中文名(异常兜底行也要有人话 label)。
_LABELS = {
    "daily_sync": "每日同步 24h 内成功",
    "apify_queue": "Apify 队列堆积",
    "queue_inflow": "队列 24h 入队量",
    "kol_hot_refresh": "KOL hot 层 24h 刷新增量",
    "official_metrics": "官号当日指标快照",
    "search_entries": "文本搜索索引行数",
    "staff_roster": "员工名单非空",
    "llm_budget": "LLM 预算闸状态",
    "apify_spend": "Apify 当日记账消耗/失败率",
    "failed_pool": "失败池/triage 堆积",
    "recommendation_outcomes": "推荐 outcomes 新数据",
}


# ──────────────────────────────────────────────
# 运行 / 落库 / 告警 / 读取
# ──────────────────────────────────────────────


def run_all_checks() -> list[dict[str, Any]]:
    """跑全部 10 项;单项异常兜成该项 fail(检查本身跑不动也是一种红),绝不拖垮其余。"""
    results: list[dict[str, Any]] = []
    for key, fn in _CHECKS:
        checked_at = _iso(_utcnow())
        try:
            item = fn()
        except Exception as exc:
            logger.warning("health_sentinel check %s crashed", key, exc_info=True)
            item = _check(key, _LABELS.get(key, key), "fail", f"检查执行异常:{type(exc).__name__}: {str(exc)[:200]}")
        item["checked_at"] = checked_at
        results.append(item)
    return results


def _persist_result(result: dict[str, Any]) -> None:
    """结果落 persistent_cache:latest 键 + 当日键(30 天过期即 30 天历史)。失败只记日志不抛。"""
    if not table_exists("persistent_cache"):
        logger.warning("health_sentinel: persistent_cache table missing, result not persisted")
        return
    try:
        conn = get_conn()
        now = _utcnow()
        payload = json.dumps(result, ensure_ascii=False, default=str)
        expires = _iso(now + timedelta(days=_HISTORY_DAYS + 1))
        day_key = _DAY_KEY_PREFIX + now.strftime("%Y-%m-%d")
        for cache_key in (_LATEST_KEY, day_key):
            conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (cache_key,))
            conn.execute(
                "INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at) VALUES (?,?,?,?)",
                (cache_key, payload, expires, _iso(now)),
            )
        conn.commit()
    except Exception:
        logger.warning("health_sentinel: persist failed", exc_info=True)


def _notify_failures(result: dict[str, Any]) -> dict[str, Any]:
    """fail 项 → 既有 vkpi_alerts 一条汇总(alert_key 带 UTC 日期,同天 upsert 同一行=幂等);
    当天全恢复 → resolve 当日 alert。任何告警异常只记日志,不影响检查结果返回。"""
    day = _utcnow().strftime("%Y-%m-%d")
    alert_key = _ALERT_KEY_PREFIX + day
    fails = [c for c in result.get("checks") or [] if c.get("status") == "fail"]
    try:
        if not fails:
            if not table_exists("vkpi_alerts"):
                return {"notified": False, "cleared": False, "alert_key": alert_key}
            from app.domains.alerts.common import resolve_open_alert

            conn = get_conn()
            cleared = resolve_open_alert(conn, alert_key)
            if cleared:
                conn.commit()
            return {"notified": False, "cleared": bool(cleared), "alert_key": alert_key}

        from app.domains.alerts.service import upsert_alert

        lines = [f"[{c.get('key')}] {c.get('label')}:{c.get('detail')}" for c in fails]
        upsert_alert(
            alert_key=alert_key,
            severity="danger",
            target_type="ops_health",
            target_id=None,
            staff_id=None,
            title=f"数据健康哨兵:{len(fails)} 项黄金链路检查失败({day})",
            body="\n".join(lines)[:2000],
            rule_key=_RULE_KEY,
            metadata_json=json.dumps(
                {"failed_keys": [c.get("key") for c in fails], "summary": result.get("summary"), "date": day},
                ensure_ascii=False,
            ),
        )
        return {"notified": True, "cleared": False, "alert_key": alert_key, "failed": len(fails)}
    except Exception:
        logger.warning("health_sentinel: notify failed", exc_info=True)
        return {"notified": False, "cleared": False, "alert_key": alert_key, "error": "notify_failed"}


def run_health_sentinel(trigger: str = "manual", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """跑一轮 10 项检查 → 落库 → fail 汇总告警(当天幂等)。返回完整结果供路由/调度直接用。"""
    del staff  # 检查内容与身份无关;权限由路由层把关。
    started = _utcnow()
    checks = run_all_checks()
    summary = {
        "ok": sum(1 for c in checks if c.get("status") == "ok"),
        "warn": sum(1 for c in checks if c.get("status") == "warn"),
        "fail": sum(1 for c in checks if c.get("status") == "fail"),
        "total": len(checks),
    }
    result: dict[str, Any] = {
        "ran_at": _iso(started),
        "trigger": str(trigger or "manual"),
        "summary": summary,
        "checks": checks,
    }
    _persist_result(result)
    result["notification"] = _notify_failures(result)
    logger.info(
        "health_sentinel run trigger=%s ok=%s warn=%s fail=%s",
        result["trigger"], summary["ok"], summary["warn"], summary["fail"],
    )
    return result


def get_latest() -> dict[str, Any]:
    """读最新一次结果(persistent_cache latest 键)。从未跑过/表缺失 → available=False,诚实不编造。"""
    if not table_exists("persistent_cache"):
        return {"available": False, "reason": "persistent_cache_missing"}
    try:
        row = get_conn().execute(
            "SELECT value_json FROM persistent_cache WHERE cache_key=?", (_LATEST_KEY,)
        ).fetchone()
    except Exception:
        logger.warning("health_sentinel: latest read failed", exc_info=True)
        return {"available": False, "reason": "read_failed"}
    if not row:
        return {"available": False, "reason": "never_ran"}
    try:
        payload = json.loads(dict(row).get("value_json") or "{}")
    except (TypeError, ValueError):
        return {"available": False, "reason": "corrupt_payload"}
    if not isinstance(payload, dict) or not payload.get("checks"):
        return {"available": False, "reason": "empty_payload"}
    return {"available": True, **payload}


def list_history(days: int = _HISTORY_DAYS) -> dict[str, Any]:
    """近 N 天按日历史(day 键;过期行由既有 cache 清理任务回收)。只读。"""
    if not table_exists("persistent_cache"):
        return {"history": []}
    limit = max(1, min(_HISTORY_DAYS, int(days or _HISTORY_DAYS)))
    try:
        rows = get_conn().execute(
            "SELECT cache_key, value_json FROM persistent_cache WHERE cache_key LIKE ? ORDER BY cache_key DESC LIMIT ?",
            (_DAY_KEY_PREFIX + "%", limit),
        ).fetchall()
    except Exception:
        logger.warning("health_sentinel: history read failed", exc_info=True)
        return {"history": []}
    history: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        try:
            payload = json.loads(item.get("value_json") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("checks"):
            history.append({"date": str(item.get("cache_key") or "")[len(_DAY_KEY_PREFIX):], "summary": payload.get("summary"), "ran_at": payload.get("ran_at")})
    return {"history": history}


if __name__ == "__main__":
    # systemd timer 入口(镜像 vkpi-sync-daily 模式):
    # cd /opt/viltrox-2.0 && PYTHONPATH=backend .venv/bin/python -m app.domains.ops.health_sentinel
    # 线上 jobs.py 已分叉,APScheduler 注册只在本地/收敛后生效,线上日跑靠这个入口。
    import json as _json

    from app.db.connection import db_connection_sync_scope as _db_scope

    with _db_scope():
        _result = run_health_sentinel(trigger="cron")
    print(_json.dumps(_result, ensure_ascii=False, default=str, indent=2))
