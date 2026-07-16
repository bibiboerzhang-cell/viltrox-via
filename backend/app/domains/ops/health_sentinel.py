"""C1 数据健康哨兵 — 黄金链路日检(只读检查 → persistent_cache 落库 → fail 汇总告警)。

诚实 by design:本模块只 READ 既有真实表,绝不修数据、不重排队、不触发同步、不执行运维动作。
仅有的两处写入:
  - persistent_cache:最新一次检查结果 JSON(latest 键)+ 按日历史(day 键,30 天自动过期),
    复用既有 KV 机制(003 baseline),零新表零迁移。
  - vkpi_alerts:存在 fail 项时经既有 upsert_alert 发一条汇总告警;alert_key 带 UTC 日期,
    同天重复跑只更新同一行(当天幂等,不重复发);当天全部恢复则自动 resolve。
零触 viltrox_fit_score / rule_v0。

检查项单项独立 try/except,单项炸不影响其余;表缺失=warn 并注明迁移号:
  1 daily_sync               durable official-channel 任务近 24h 是否有完整执行覆盖
  2 apify_queue              apify_jobs 最老 queued 任务年龄(堆积)(095)
  3 kol_hot_refresh          vkpi_kol_refresh_tier hot 层覆盖与 24h 刷新增量(076)
  4 official_metrics         官号 24h 快照全覆盖与 provider provenance(030)
  5 search_entries           vkpi_kol_profile_index_entries 行数(099;历史全灭事故防复发)
  6 staff_roster             staff 活跃名单非空(012)
  7 llm_budget               所有外部 provider 预算 hard_stop + 当日 budget_blocked 拦截(057)
  8 apify_spend              vkpi_ai_cost_ledger 当日 apify 记账消耗 + apify_jobs 24h 失败率
 9 failed_pool              apify_jobs triage/failed 终态堆积(含 evidence 冲突被阻断任务的归宿,201)
10 recommendation_outcomes  vkpi_recommendation_outcomes 总量 + 近 7 日新增(045)
 11 queue_inflow             自动入队任务启用时 24h 是否有新增
 12 legacy_queue_orphans     第二套 job_execution_ledger 是否有 >24h 活跃孤儿
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

_OFFICIAL_SYNC_JOB_TYPE = "vkpi_official_channel_sync"
_SUCCESS_JOB_STATUSES = frozenset({"done"})
_ACTIVE_JOB_STATUSES = frozenset({"queued", "retrying", "processing", "running"})
_FAILED_JOB_STATUSES = frozenset({"failed", "timeout", "cancelled"})
# Lifetime dead letters are historical debt, not proof of a current outage.  A
# recent surge is still a red signal; smaller/new debt remains visible as warn.
_FAILED_POOL_RECENT_FAIL_THRESHOLD = 20

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


def _rows(conn: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(item) for item in conn.execute(sql, params).fetchall()]


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
    """Check the durable official-channel path, not legacy KOL-only receipts.

    `vkpi_sync_runs` currently contains old synchronous ``kol_pool_light``
    stages.  Treating one of those as a successful official sync made this
    check capable of going green while every company-channel snapshot was
    stale.  The actual scheduler now dispatches one Redis-ledger task per
    official channel, so those terminal task rows are the execution receipt.
    Data freshness/provenance remains an independent `official_metrics` gate.
    """

    key, label = "daily_sync", "每日官号同步执行覆盖"
    if not table_exists("job_execution_ledger"):
        return _missing(key, label, "job_execution_ledger", "009/056")
    conn = get_conn()
    channels_total = 0
    if table_exists("vkpi_employee_channels"):
        channels_total = _int0(
            _row(conn, "SELECT COUNT(*) AS n FROM vkpi_employee_channels WHERE deleted_at IS NULL").get("n")
        )
    if channels_total == 0:
        return _check(key, label, "warn", "未绑定官号，无 durable 同步覆盖可验证")

    cutoff = _cutoff_iso(timedelta(hours=24))
    recent = _rows(
        conn,
        """
        SELECT task_id, status, payload_json, created_at, updated_at, finished_at
        FROM job_execution_ledger
        WHERE job_type=? AND created_at>=?
        """,
        (_OFFICIAL_SYNC_JOB_TYPE, cutoff),
    )
    completed_channels: set[int] = set()
    active = failed = malformed = 0
    for item in recent:
        status = str(item.get("status") or "").strip().lower()
        raw_payload = item.get("payload_json")
        try:
            payload = raw_payload if isinstance(raw_payload, dict) else json.loads(str(raw_payload or "{}"))
        except (TypeError, ValueError):
            payload = {}
        channel_id = _int0(payload.get("channel_id") if isinstance(payload, dict) else 0)
        if status in _SUCCESS_JOB_STATUSES and channel_id:
            completed_channels.add(channel_id)
        elif status in _ACTIVE_JOB_STATUSES:
            active += 1
        elif status in _FAILED_JOB_STATUSES:
            failed += 1
        elif status in _SUCCESS_JOB_STATUSES:
            malformed += 1

    covered = len(completed_channels)
    if covered >= channels_total:
        return _check(
            key,
            label,
            "ok",
            f"24h durable 完成覆盖 {covered}/{channels_total} 个官号；进行中 {active}，失败/取消 {failed}",
        )
    if active > 0 and failed == 0:
        return _check(
            key,
            label,
            "warn",
            f"24h durable 完成覆盖 {covered}/{channels_total}，仍有 {active} 条执行中；未提前宣告成功",
        )

    latest_success = _row(
        conn,
        """
        SELECT MAX(COALESCE(finished_at,updated_at)) AS at
        FROM job_execution_ledger
        WHERE job_type=? AND status='done'
        """,
        (_OFFICIAL_SYNC_JOB_TYPE,),
    )
    success_age = _age_hours(latest_success.get("at"))
    age_text = f"{success_age:.0f}h 前" if success_age is not None else "从未有成功回执"
    legacy_note = ""
    if table_exists("vkpi_sync_runs"):
        latest_legacy = _row(
            conn,
            "SELECT stage,status FROM vkpi_sync_runs WHERE job_name=? ORDER BY started_at DESC LIMIT 1",
            (_DAILY_SYNC_JOB_NAME,),
        )
        if latest_legacy:
            legacy_note = (
                f"；vkpi_sync_runs 最新仅 {latest_legacy.get('stage')}/{latest_legacy.get('status')}"
                "，不作官号同步证据"
            )
    malformed_note = f"，无 channel_id 成功回执 {malformed}" if malformed else ""
    return _check(
        key,
        label,
        "fail",
        f"24h durable 完成覆盖 {covered}/{channels_total}，进行中 {active}，失败/取消 {failed}{malformed_note}；最近真成功 {age_text}{legacy_note}",
    )


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
            COUNT(*) AS tier_rows,
            SUM(CASE WHEN tier='hot' OR manual_hot_flag THEN 1 ELSE 0 END) AS hot_total,
            SUM(CASE WHEN tier='warm' AND NOT manual_hot_flag THEN 1 ELSE 0 END) AS warm_total,
            SUM(CASE WHEN (tier='hot' OR manual_hot_flag) AND last_refresh_at >= ? THEN 1 ELSE 0 END) AS refreshed
        FROM vkpi_kol_refresh_tier
        """,
        (cutoff,),
    )
    tier_rows = _int0(data.get("tier_rows"))
    hot_total = _int0(data.get("hot_total"))
    warm_total = _int0(data.get("warm_total"))
    refreshed = _int0(data.get("refreshed"))
    pool_total = 0
    if table_exists("vkpi_kol_pool"):
        pool_total = _int0(_row(conn, "SELECT COUNT(*) AS n FROM vkpi_kol_pool").get("n"))
    coverage = f"tier 已物化 {tier_rows}/{pool_total}" if pool_total else f"tier 已物化 {tier_rows}"
    if hot_total == 0:
        return _check(
            key,
            label,
            "warn",
            f"真实 hot 证据候选 0 个（warm {warm_total}，{coverage}）；这是证据/实体关联缺口，未自动伪造 hot 标签",
        )
    if refreshed == 0:
        return _check(key, label, "fail", f"hot 层 {hot_total} 个 KOL,24h 内刷新 0 个(每日 KOL 快照链断流)")
    if pool_total and tier_rows < pool_total:
        return _check(
            key,
            label,
            "warn",
            f"hot 层 {hot_total} 个，24h 刷新 {refreshed} 个；{coverage}，分层覆盖尚未全量",
        )
    return _check(key, label, "ok", f"hot 层 {hot_total} 个 KOL,24h 内刷新 {refreshed} 个")


def _check_official_metrics() -> dict[str, Any]:
    key, label = "official_metrics", "官号 24h 指标快照覆盖"
    if not table_exists("vkpi_channel_metrics"):
        return _missing(key, label, "vkpi_channel_metrics", "030")
    conn = get_conn()
    if not table_exists("vkpi_employee_channels"):
        return _missing(key, label, "vkpi_employee_channels", "030")
    cutoff = _cutoff_iso(timedelta(hours=24))
    rows = _rows(
        conn,
        """
        SELECT
            c.id AS channel_id,
            MAX(m.captured_at) AS latest_captured,
            MAX(CASE WHEN m.captured_at>=? THEN 1 ELSE 0 END) AS fresh,
            MAX(CASE WHEN m.captured_at>=?
                      AND CAST(m.raw_payload_json AS TEXT) LIKE ?
                     THEN 1 ELSE 0 END) AS fresh_with_provenance
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.channel_id=c.id
        WHERE c.deleted_at IS NULL
        GROUP BY c.id
        """,
        (cutoff, cutoff, '%"provider"%'),
    )
    channels_total = len(rows)
    if channels_total == 0:
        return _check(key, label, "warn", "未绑定任何官号(vkpi_employee_channels 为空),无快照可查")
    fresh = sum(1 for row in rows if _int0(row.get("fresh")))
    provenance = sum(1 for row in rows if _int0(row.get("fresh_with_provenance")))
    latest = max(
        (parsed for parsed in (_parse_dt(row.get("latest_captured")) for row in rows) if parsed is not None),
        default=None,
    )
    latest_age = _age_hours(latest)
    age_text = f"最新 {latest_age:.1f}h 前" if latest_age is not None else "从未有快照"
    if fresh == 0:
        return _check(key, label, "fail", f"24h 新鲜快照覆盖 0/{channels_total}，{age_text}；官号数据链断流")
    if fresh < channels_total:
        return _check(
            key,
            label,
            "fail",
            f"24h 新鲜快照覆盖 {fresh}/{channels_total}，其中 provider provenance {provenance}/{fresh}；未达全官号覆盖",
        )
    if provenance < channels_total:
        return _check(
            key,
            label,
            "fail",
            f"24h 快照覆盖 {fresh}/{channels_total}，但 provider provenance 仅 {provenance}/{channels_total}；不把无来源快照宣告为健康",
        )
    return _check(key, label, "ok", f"24h 快照及 provider provenance 均覆盖 {channels_total}/{channels_total} 个官号")


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
    # Keep the stable API key for frontend/backward compatibility.  The table
    # also contains provider:apify, so the old "LLM" label was materially
    # misleading when the only stopped scope was the crawler provider.
    key, label = "llm_budget", "外部 Provider 预算闸状态"
    if not table_exists("vkpi_provider_budget_caps"):
        return _missing(key, label, "vkpi_provider_budget_caps", "057")
    from app.domains.costs import budget_guard

    status = budget_guard.get_budget_status()
    budgets = status.get("budgets") or []
    hard_rows = [b for b in budgets if b.get("hard_stopped")]
    hard_stopped = [str(b.get("scope") or "") for b in hard_rows]
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
        detail_rows: list[str] = []
        for budget in sorted(hard_rows, key=lambda item: str(item.get("scope") or "")):
            detail_rows.append(
                f"{budget.get('scope')}(${_float0(budget.get('current_spend')):.2f}/${_float0(budget.get('cap_usd')):.2f})"
            )
        return _check(
            key,
            label,
            "warn",
            f"hard-stop 已 fail-closed:{', '.join(detail_rows)}；今日记录预算拒绝 {blocked_today} 次（未授权不自动调高额度）",
        )
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
    cutoff_24h = _cutoff_iso(timedelta(hours=24))
    cutoff_7d = _cutoff_iso(timedelta(days=7))
    data = _row(
        conn,
        """
        SELECT
            SUM(CASE WHEN status='triage' THEN 1 ELSE 0 END) AS triage,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status='triage' AND updated_at>=? THEN 1 ELSE 0 END) AS triage_24h,
            SUM(CASE WHEN status='failed' AND updated_at>=? THEN 1 ELSE 0 END) AS failed_24h,
            SUM(CASE WHEN status='triage' AND updated_at>=? THEN 1 ELSE 0 END) AS triage_7d,
            SUM(CASE WHEN status='failed' AND updated_at>=? THEN 1 ELSE 0 END) AS failed_7d
        FROM apify_jobs
        """,
        (cutoff_24h, cutoff_24h, cutoff_7d, cutoff_7d),
    )
    triage = _int0(data.get("triage"))
    failed = _int0(data.get("failed"))
    triage_24h = _int0(data.get("triage_24h"))
    failed_24h = _int0(data.get("failed_24h"))
    triage_7d = _int0(data.get("triage_7d"))
    failed_7d = _int0(data.get("failed_7d"))
    recent = triage_24h + failed_24h
    detail = (
        f"未裁决历史债 triage {triage} / failed {failed}；"
        f"近 24h 新增 {triage_24h}/{failed_24h}，近 7d 新增 {triage_7d}/{failed_7d}"
    )
    if recent >= _FAILED_POOL_RECENT_FAIL_THRESHOLD:
        return _check(key, label, "fail", detail + f"；24h 新增达红线 {_FAILED_POOL_RECENT_FAIL_THRESHOLD}")
    if triage > 0 or failed > 0:
        return _check(key, label, "warn", detail + "；历史债不冒充当前 provider 故障，也未被自动删除/重放")
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


def _check_legacy_queue_orphans() -> dict[str, Any]:
    """第 12 检:第二套 ledger 不能再被 Apify 单队列绿灯掩盖。

    job_execution_ledger 由 Redis/in-process worker 家族消费；活动状态超过 24h
    未更新已经不再是正常排队，而是失去可证明执行载体的孤儿。这里只读审计，
    绝不取消、重排或补发任务。
    """
    key, label = "legacy_queue_orphans", "第二任务队列孤儿审计"
    if not table_exists("job_execution_ledger"):
        return _missing(key, label, "job_execution_ledger", "009/056")
    data = _row(
        get_conn(),
        """
        SELECT
            SUM(CASE WHEN status IN ('queued','retrying','processing','running') THEN 1 ELSE 0 END) AS active_total,
            SUM(CASE WHEN status IN ('queued','retrying','processing','running')
                      AND updated_at < NOW() - INTERVAL '24 hours' THEN 1 ELSE 0 END) AS stale_active,
            SUM(CASE WHEN status IN ('queued','retrying','processing','running')
                      AND updated_at < NOW() - INTERVAL '24 hours'
                      AND COALESCE(stream_id,'')='' THEN 1 ELSE 0 END) AS stale_without_stream,
            MIN(CASE WHEN status IN ('queued','retrying','processing','running')
                      AND updated_at < NOW() - INTERVAL '24 hours' THEN updated_at END) AS oldest_stale
        FROM job_execution_ledger
        """,
    )
    active = _int0(data.get("active_total"))
    stale = _int0(data.get("stale_active"))
    without_stream = _int0(data.get("stale_without_stream"))
    oldest_age = _age_hours(data.get("oldest_stale"))
    if stale > 0:
        age_text = f"{oldest_age:.1f}h" if oldest_age is not None else "未知"
        return _check(
            key,
            label,
            "fail",
            f"ledger 活跃态 {active} 条，其中 >24h 孤儿 {stale} 条、无 stream_id {without_stream} 条，最老 {age_text}；需人工裁决，未自动重排",
        )
    if active > 0:
        return _check(key, label, "ok", f"ledger 活跃态 {active} 条，均在 24h 新鲜闸内；仍需独立 worker 心跳证明")
    return _check(key, label, "ok", "ledger 当前 0 条新鲜活跃任务，且无 >24h 活跃孤儿")


_CHECKS: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("daily_sync", _check_daily_sync),
    ("apify_queue", _check_apify_queue),
    ("legacy_queue_orphans", _check_legacy_queue_orphans),
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
    "daily_sync": "每日官号同步执行覆盖",
    "apify_queue": "Apify 队列堆积",
    "legacy_queue_orphans": "第二任务队列孤儿审计",
    "queue_inflow": "队列 24h 入队量",
    "kol_hot_refresh": "KOL hot 层 24h 刷新增量",
    "official_metrics": "官号 24h 指标快照覆盖",
    "search_entries": "文本搜索索引行数",
    "staff_roster": "员工名单非空",
    "llm_budget": "外部 Provider 预算闸状态",
    "apify_spend": "Apify 当日记账消耗/失败率",
    "failed_pool": "失败池/triage 堆积",
    "recommendation_outcomes": "推荐 outcomes 新数据",
}


# ──────────────────────────────────────────────
# 运行 / 落库 / 告警 / 读取
# ──────────────────────────────────────────────


def run_all_checks() -> list[dict[str, Any]]:
    """跑全部检查;单项异常兜成该项 fail(检查本身跑不动也是一种红),绝不拖垮其余。"""
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
    """跑一轮全部检查 → 落库 → fail 汇总告警(当天幂等)。返回完整结果供路由/调度直接用。"""
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
