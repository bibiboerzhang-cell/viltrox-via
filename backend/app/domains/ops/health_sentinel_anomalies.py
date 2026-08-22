"""C1 数据健康哨兵 — 统计异常 5 检(从 health_sentinel.py 拆出,千行卫兵)。

与主模块同一契约:每项只读既有真实表,返回 {key,label,status,detail,…};阈值全部 env 化;
样本不够时诚实返回 warn + ``insufficient_data=True``(「数据不足」≠健康,也≠故障)。
零写入、零运维动作、零触 viltrox_fit_score / rule_v0。

  13 llm_degrade_rate      LLM 近 N 天降级率(优先调 L1 的 llm_degrade_rate(days) 纯函数)
  14 ledger_day_diff       成本账本 24h 日差:vkpi_ai_cost_ledger(非 apify)vs vkpi_llm_calls(success)
  15 queue_backlog         apify_jobs queued 超 N 且最老 >N 小时(堆积 + 停滞双条件)
  16 apify_spend_spike     Apify 日支出突增:>均值+kσ 或 >provider:apify 月封顶×fraction
  17 snapshot_failure_rate vkpi_content_metric_snapshots 24h failed/(success+failed) 超阈
"""
from __future__ import annotations

import importlib
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

# ── 阈值 env(缺省保守)──
ENV_LLM_DEGRADE_DAYS = "VKPI_SENTINEL_LLM_DEGRADE_DAYS"
ENV_LLM_DEGRADE_FAIL_RATIO = "VKPI_SENTINEL_LLM_DEGRADE_FAIL_RATIO"
ENV_LLM_DEGRADE_MIN_CALLS = "VKPI_SENTINEL_LLM_DEGRADE_MIN_CALLS"
ENV_LEDGER_DIFF_RATIO = "VKPI_SENTINEL_LEDGER_DIFF_RATIO"
ENV_LEDGER_DIFF_MIN_USD = "VKPI_SENTINEL_LEDGER_DIFF_MIN_USD"
ENV_QUEUE_BACKLOG_MIN = "VKPI_SENTINEL_QUEUE_BACKLOG_MIN"
ENV_QUEUE_BACKLOG_OLDEST_HOURS = "VKPI_SENTINEL_QUEUE_BACKLOG_OLDEST_HOURS"
ENV_APIFY_SPIKE_SIGMA = "VKPI_SENTINEL_APIFY_SPIKE_SIGMA"
ENV_APIFY_SPIKE_BASELINE_DAYS = "VKPI_SENTINEL_APIFY_SPIKE_BASELINE_DAYS"
ENV_APIFY_SPIKE_MIN_BASELINE_DAYS = "VKPI_SENTINEL_APIFY_SPIKE_MIN_BASELINE_DAYS"
ENV_APIFY_SPIKE_CAP_FRACTION = "VKPI_SENTINEL_APIFY_SPIKE_CAP_FRACTION"
ENV_SNAPSHOT_FAIL_RATIO = "VKPI_SENTINEL_SNAPSHOT_FAIL_RATIO"
ENV_SNAPSHOT_MIN_SAMPLE = "VKPI_SENTINEL_SNAPSHOT_MIN_SAMPLE"

_APIFY_BUDGET_SCOPE = "provider:apify"
_SNAPSHOT_TABLE = "vkpi_content_metric_snapshots"
_LEDGER_SCAN_LIMIT = 20000

# L1 车道承诺的纯函数落点(按顺序试 import;都没有 → 本地口径兜底)。
# TODO(L1): llm_degrade_rate(days) 落地后,本地兜底 _local_degrade_rate 退役,只认 L1。
_DEGRADE_FN_CANDIDATES = (
    ("app.platform.llm_gateway_ledger", "llm_degrade_rate"),
    ("app.platform.llm_gateway_ledger_stats", "llm_degrade_rate"),
    ("app.platform.llm_gateway_ledger_metrics", "llm_degrade_rate"),
)


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw else int(default)
    except (TypeError, ValueError):
        return int(default)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutoff_iso(delta: timedelta) -> str:
    return _iso(_utcnow() - delta)


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN 读回可能是 1/0、't'/'f'、True/False;统一判真。"""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "t", "true", "yes"}
    return bool(value)


def _check(key: str, label: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail, **extra}


def _insufficient(key: str, label: str, detail: str, **extra: Any) -> dict[str, Any]:
    return _check(key, label, "warn", "数据不足:" + detail, insufficient_data=True, **extra)


def _missing(key: str, label: str, table: str, migration: str) -> dict[str, Any]:
    return _check(key, label, "warn", f"{table} 表缺失(迁移 {migration} 未跑),无法检查")


# ──────────────────────────────────────────────
# 13 LLM 降级率
# ──────────────────────────────────────────────


def _resolve_degrade_fn() -> Callable[[int], Any] | None:
    for module_name, attr in _DEGRADE_FN_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        fn = getattr(module, attr, None)
        if callable(fn):
            return fn
    return None


def _local_degrade_rate(days: int) -> dict[str, Any]:
    """本地兜底口径:vkpi_llm_calls 近 N 天 status<>'success' 或 fallback_used 为真 = 降级。"""
    if not table_exists("vkpi_llm_calls"):
        return {"available": False, "reason": "vkpi_llm_calls_missing", "days": days}
    rows = _rows(
        get_conn(),
        """
        SELECT status, fallback_used, COUNT(*) AS n
        FROM vkpi_llm_calls
        WHERE created_at >= ?
        GROUP BY status, fallback_used
        """,
        (_cutoff_iso(timedelta(days=days)),),
    )
    total = degraded = 0
    for item in rows:
        n = _int0(item.get("n"))
        total += n
        if str(item.get("status") or "").strip().lower() != "success" or _truthy(item.get("fallback_used")):
            degraded += n
    return {"available": True, "total": total, "degraded": degraded,
            "rate": (degraded / total) if total else None, "days": days, "source": "local_fallback"}


def _coerce_degrade_payload(value: Any, days: int) -> dict[str, Any]:
    """L1 函数返回形状未定(float 或 dict)→ 统一成 {available,total,degraded,rate}。"""
    if isinstance(value, dict):
        total = _int0(value.get("total") or value.get("calls") or value.get("total_calls"))
        degraded = _int0(value.get("degraded") or value.get("degraded_calls") or value.get("fallback"))
        # L1 实际形状:{available, days, calls, success, fallback, fallback_rate, ...};兼容 rate/degrade_rate 别名。
        rate = next((value[k] for k in ("rate", "degrade_rate", "fallback_rate") if value.get(k) is not None), None)
        rate_f = _float0(rate) if rate is not None else ((degraded / total) if total else None)
        return {"available": bool(value.get("available", True)), "total": total, "degraded": degraded,
                "rate": rate_f, "days": days, "source": "l1"}
    if isinstance(value, (int, float)):
        return {"available": True, "total": None, "degraded": None, "rate": float(value), "days": days, "source": "l1"}
    return {"available": False, "reason": "unrecognized_payload", "days": days, "source": "l1"}


def check_llm_degrade_rate() -> dict[str, Any]:
    key, label = "llm_degrade_rate", "LLM 近 N 天降级率"
    days = max(1, _env_int(ENV_LLM_DEGRADE_DAYS, 7))
    fail_ratio = _env_float(ENV_LLM_DEGRADE_FAIL_RATIO, 0.15)
    min_calls = max(1, _env_int(ENV_LLM_DEGRADE_MIN_CALLS, 20))
    fn = _resolve_degrade_fn()
    if fn is not None:
        try:
            stats = _coerce_degrade_payload(fn(days), days)
        except Exception:
            logger.warning("health_sentinel: L1 llm_degrade_rate crashed, falling back to local", exc_info=True)
            stats = _local_degrade_rate(days)
    else:
        stats = _local_degrade_rate(days)
    source = stats.get("source") or "local_fallback"
    if not stats.get("available"):
        return _insufficient(key, label, f"{days}d 降级率不可得({stats.get('reason') or 'unavailable'});口径 {source}")
    rate = stats.get("rate")
    total = stats.get("total")
    if rate is None or (total is not None and _int0(total) < min_calls):
        return _insufficient(
            key, label, f"{days}d 调用 {_int0(total)} 次(<{min_calls} 样本下限),不判降级率;口径 {source}", calls=_int0(total)
        )
    rate_f = _float0(rate)
    summary = f"{days}d 降级率 {rate_f:.0%}"
    if total is not None:
        summary += f"({_int0(stats.get('degraded'))}/{_int0(total)} 次)"
    summary += f",阈值 {fail_ratio:.0%};口径 {source}"
    if rate_f > fail_ratio:
        return _check(key, label, "fail", summary + "——LLM 链大面积降级(代理/预算/key 其一出问题)", rate=rate_f)
    if rate_f > fail_ratio / 2:
        return _check(key, label, "warn", summary, rate=rate_f)
    return _check(key, label, "ok", summary, rate=rate_f)


# ──────────────────────────────────────────────
# 14 成本账本日差
# ──────────────────────────────────────────────


def check_ledger_day_diff() -> dict[str, Any]:
    key, label = "ledger_day_diff", "成本账本 24h 日差(ledger vs llm_calls)"
    if not table_exists("vkpi_ai_cost_ledger"):
        return _missing(key, label, "vkpi_ai_cost_ledger", "057")
    if not table_exists("vkpi_llm_calls"):
        return _missing(key, label, "vkpi_llm_calls", "045")
    ratio_limit = _env_float(ENV_LEDGER_DIFF_RATIO, 0.25)
    min_usd = _env_float(ENV_LEDGER_DIFF_MIN_USD, 1.0)
    conn = get_conn()
    cutoff = _cutoff_iso(timedelta(hours=24))
    ledger = _row(
        conn,
        "SELECT COUNT(*) AS n, SUM(cost_usd) AS usd FROM vkpi_ai_cost_ledger WHERE occurred_at >= ? AND ai_provider <> 'apify'",
        (cutoff,),
    )
    calls = _row(
        conn,
        "SELECT COUNT(*) AS n, SUM(cost_micro_usd) AS micro FROM vkpi_llm_calls WHERE created_at >= ? AND status = 'success'",
        (cutoff,),
    )
    ledger_usd = _float0(ledger.get("usd"))
    calls_usd = _float0(calls.get("micro")) / 1_000_000
    ledger_n, calls_n = _int0(ledger.get("n")), _int0(calls.get("n"))
    if ledger_n == 0 and calls_n == 0:
        return _insufficient(key, label, "24h 内 ledger 与 llm_calls 均无成功记账,无可比对样本")
    diff = abs(ledger_usd - calls_usd)
    base = max(ledger_usd, calls_usd)
    ratio = (diff / base) if base > 0 else 0.0
    summary = (
        f"24h ledger(非 apify){ledger_n} 笔 ${ledger_usd:.4f} vs llm_calls(success){calls_n} 笔 ${calls_usd:.4f}"
        f",差 ${diff:.4f}({ratio:.0%});阈值 {ratio_limit:.0%} 且 ≥${min_usd:.2f}"
    )
    if ratio > ratio_limit and diff >= min_usd:
        return _check(key, label, "fail", summary + "——两本账漂移,查 cost_tag 漏种/视频管线直写 ledger", diff_usd=diff, ratio=ratio)
    if ratio > ratio_limit:
        return _check(key, label, "warn", summary + "(绝对额未达红线)", diff_usd=diff, ratio=ratio)
    return _check(key, label, "ok", summary, diff_usd=diff, ratio=ratio)


# ──────────────────────────────────────────────
# 15 队列积压
# ──────────────────────────────────────────────


def check_queue_backlog() -> dict[str, Any]:
    key, label = "queue_backlog", "Apify 队列积压(数量+停滞双条件)"
    if not table_exists("apify_jobs"):
        return _missing(key, label, "apify_jobs", "095")
    backlog_min = max(1, _env_int(ENV_QUEUE_BACKLOG_MIN, 50))
    oldest_hours = max(0.0, _env_float(ENV_QUEUE_BACKLOG_OLDEST_HOURS, 1.0))
    data = _row(get_conn(), "SELECT COUNT(*) AS queued, MIN(created_at) AS oldest FROM apify_jobs WHERE status='queued'")
    queued = _int0(data.get("queued"))
    if queued == 0:
        return _check(key, label, "ok", "0 条 queued,无积压", queued=0)
    age = _age_hours(data.get("oldest"))
    age_text = f"{age:.1f}h" if age is not None else "未知"
    summary = f"{queued} 条 queued,最老 {age_text};阈值 >{backlog_min} 条且最老 >{oldest_hours:g}h"
    if queued > backlog_min and age is not None and age > oldest_hours:
        return _check(key, label, "fail", summary + "——积压且停滞(worker 吞吐不足或停摆)", queued=queued, oldest_hours=age)
    if age is not None and age > oldest_hours:
        return _check(key, label, "warn", summary + "(停滞但未超量)", queued=queued, oldest_hours=age)
    return _check(key, label, "ok", summary, queued=queued, oldest_hours=age)


# ──────────────────────────────────────────────
# 16 Apify 日支出突增
# ──────────────────────────────────────────────


def _apify_daily_spend(conn: Any, baseline_days: int) -> dict[str, float]:
    """按 UTC 日桶聚合 apify 记账(Python 侧分桶:occurred_at 在 PG 是 TIMESTAMPTZ,SUBSTR 不可移植)。"""
    cutoff = _cutoff_iso(timedelta(days=baseline_days + 2))
    rows = _rows(
        conn,
        "SELECT occurred_at, cost_usd FROM vkpi_ai_cost_ledger WHERE ai_provider='apify' AND occurred_at >= ? ORDER BY occurred_at DESC LIMIT ?",
        (cutoff, _LEDGER_SCAN_LIMIT),
    )
    buckets: dict[str, float] = {}
    for item in rows:
        parsed = _parse_dt(item.get("occurred_at"))
        if parsed is None:
            continue
        day = parsed.strftime("%Y-%m-%d")
        buckets[day] = buckets.get(day, 0.0) + _float0(item.get("cost_usd"))
    return buckets


def _apify_monthly_cap(conn: Any) -> float:
    if not table_exists("vkpi_provider_budget_caps"):
        return 0.0
    return _float0(_row(conn, "SELECT cap_usd FROM vkpi_provider_budget_caps WHERE scope=?", (_APIFY_BUDGET_SCOPE,)).get("cap_usd"))


def check_apify_spend_spike() -> dict[str, Any]:
    key, label = "apify_spend_spike", "Apify 日支出突增"
    if not table_exists("vkpi_ai_cost_ledger"):
        return _missing(key, label, "vkpi_ai_cost_ledger", "057")
    sigma_k = max(0.5, _env_float(ENV_APIFY_SPIKE_SIGMA, 3.0))
    baseline_days = max(3, _env_int(ENV_APIFY_SPIKE_BASELINE_DAYS, 14))
    min_baseline = max(2, _env_int(ENV_APIFY_SPIKE_MIN_BASELINE_DAYS, 5))
    cap_fraction = max(0.0, _env_float(ENV_APIFY_SPIKE_CAP_FRACTION, 0.10))
    conn = get_conn()
    buckets = _apify_daily_spend(conn, baseline_days)
    now = _utcnow()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    baseline = [usd for day, usd in buckets.items() if day not in (today, yesterday)]
    cap = _apify_monthly_cap(conn)
    cap_line = cap * cap_fraction if cap > 0 and cap_fraction > 0 else None
    candidates = {today: buckets.get(today, 0.0), yesterday: buckets.get(yesterday, 0.0)}
    peak_day, peak_usd = max(candidates.items(), key=lambda kv: kv[1])
    cap_text = f",月封顶×{cap_fraction:g}=${cap_line:.2f}" if cap_line is not None else ",无 apify 月封顶可参照"
    if cap_line is not None and peak_usd > cap_line:
        return _check(key, label, "fail", f"{peak_day} 记账 ${peak_usd:.2f} 超月封顶线(${cap:.2f}×{cap_fraction:g}=${cap_line:.2f})",
                      day=peak_day, usd=peak_usd, rule="cap_fraction")
    if len(baseline) < min_baseline:
        return _insufficient(
            key, label, f"基线仅 {len(baseline)} 天(<{min_baseline}),σ 规则不判;{peak_day} 记账 ${peak_usd:.2f}{cap_text}",
            day=peak_day, usd=peak_usd, baseline_days=len(baseline),
        )
    mean = statistics.fmean(baseline)
    stdev = statistics.pstdev(baseline) if len(baseline) > 1 else 0.0
    line = mean + sigma_k * stdev
    summary = f"{peak_day} 记账 ${peak_usd:.2f};基线 {len(baseline)} 天均值 ${mean:.2f} σ ${stdev:.2f},{sigma_k:g}σ 线 ${line:.2f}{cap_text}"
    if stdev > 0 and peak_usd > line:
        return _check(key, label, "fail", summary + "——日支出突增", day=peak_day, usd=peak_usd, rule="sigma")
    if stdev == 0 and mean > 0 and peak_usd > mean * 2:
        return _check(key, label, "warn", summary + "(基线零方差,按 2×均值提示)", day=peak_day, usd=peak_usd, rule="sigma_flat")
    return _check(key, label, "ok", summary, day=peak_day, usd=peak_usd)


# ──────────────────────────────────────────────
# 17 快照失败率
# ──────────────────────────────────────────────


def check_snapshot_failure_rate() -> dict[str, Any]:
    key, label = "snapshot_failure_rate", "内容指标快照 24h 失败率"
    if not table_exists(_SNAPSHOT_TABLE):
        return _missing(key, label, _SNAPSHOT_TABLE, "283")
    fail_ratio = _env_float(ENV_SNAPSHOT_FAIL_RATIO, 0.5)
    min_sample = max(1, _env_int(ENV_SNAPSHOT_MIN_SAMPLE, 5))
    data = _row(
        get_conn(),
        f"""
        SELECT
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok_n,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_n
        FROM {_SNAPSHOT_TABLE}
        WHERE fetched_at >= ? AND status IN ('success','failed')
        """,
        (_cutoff_iso(timedelta(hours=24)),),
    )
    ok_n, failed_n = _int0(data.get("ok_n")), _int0(data.get("failed_n"))
    total = ok_n + failed_n
    if total < min_sample:
        return _insufficient(key, label, f"24h 快照尝试 {total} 次(<{min_sample} 样本下限),不判失败率", total=total)
    rate = failed_n / total
    summary = f"24h 快照 {total} 次,失败 {failed_n}({rate:.0%}),阈值 {fail_ratio:.0%}"
    if rate > fail_ratio:
        return _check(key, label, "fail", summary + "——指标刷新链过半失败", rate=rate, total=total)
    if rate > fail_ratio / 2:
        return _check(key, label, "warn", summary, rate=rate, total=total)
    return _check(key, label, "ok", summary, rate=rate, total=total)


ANOMALY_CHECKS: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("llm_degrade_rate", check_llm_degrade_rate),
    ("ledger_day_diff", check_ledger_day_diff),
    ("queue_backlog", check_queue_backlog),
    ("apify_spend_spike", check_apify_spend_spike),
    ("snapshot_failure_rate", check_snapshot_failure_rate),
)

ANOMALY_LABELS = {
    "llm_degrade_rate": "LLM 近 N 天降级率",
    "ledger_day_diff": "成本账本 24h 日差(ledger vs llm_calls)",
    "queue_backlog": "Apify 队列积压(数量+停滞双条件)",
    "apify_spend_spike": "Apify 日支出突增",
    "snapshot_failure_rate": "内容指标快照 24h 失败率",
}

__all__ = [
    "ANOMALY_CHECKS", "ANOMALY_LABELS", "check_apify_spend_spike", "check_ledger_day_diff",
    "check_llm_degrade_rate", "check_queue_backlog", "check_snapshot_failure_rate",
]
