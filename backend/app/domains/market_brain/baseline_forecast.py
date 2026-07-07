"""经验分位数日基线(baseline_forecast)——「明天每个官号大概涨多少播放」的最笨真基线。

口径(决定性,零 LLM、零采集):vkpi_channel_post_metrics(迁移073,官号逐帖日快照)
按 (channel_id, snapshot_date) 聚合出每渠道「日播放增量」序列(SUM(views_delta)),
对最近 LOOKBACK_DAYS 天的序列算线性插值经验分位数 p10/p50/p90,经
prediction_ledger.record_prediction_run 落账:
  task_type='channel_views_daily',horizon_days=1,
  run_id=blchan_<channel_id>_<UTC日>(渠道+日期幂等,同日重跑 UPSERT 不重复落账)。

诚实态:某渠道序列样本 < MIN_SAMPLES(8)→ confidence='data_missing' 不落账
(绝不拿两三天的数硬编区间);表未建 / 零行 → status='empty' 绝不抛。
消费方:job_vkpi_prediction_weekly_rollup 周评估对这些 run 与实际增量对账,
给「聪明模型必须打赢的底线」提供每日真跑分。

compat 约定:SQL 占位符 ?;SQL 字符串零字面 percent(不用 LIKE);日期读回宽容
(_date_key 双态容错),分组聚合按 SQL GROUP BY、序列统计全在 Python 算。
红线:纯读 vkpi_channel_post_metrics,唯一写入走 prediction_ledger(表
vkpi_prediction_runs);零 LLM;绝不写 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SOURCE_TABLE = "vkpi_channel_post_metrics"

MODEL_NAME = "empirical_quantile_baseline"
MODEL_VERSION = "v1"
TASK_TYPE = "channel_views_daily"
HORIZON_DAYS = 1

# 序列样本下限:少于 8 个「日增量」点区间毫无意义,诚实不落账(data_missing)。
MIN_SAMPLES = 8
# 样本 ≥ 此值才敢标 medium(仍是最笨基线,永不虚标 high)。
MEDIUM_SAMPLES = 30
# 回看窗口(天):太久远的日增量对「明天」参考价值衰减,也压住扫描量。
LOOKBACK_DAYS = 90
# 单次最多落账的渠道数(防御性上限;官号量级远小于此)。
MAX_CHANNELS = 200


# ── 小工具(compat 宽容层,与 prediction_ledger 同款口径)──────────────


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_key(value: Any) -> str | None:
    """snapshot_date 读回双态(date / datetime / ISO str)→ 'YYYY-MM-DD';解析不了诚实 None。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] or None


def _quantile(sorted_values: list[float], q: float) -> float | None:
    """线性插值分位数(与 kol.performance_forecast 同款口径,可复算)。"""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    h = (len(sorted_values) - 1) * q
    lo, hi = math.floor(h), math.ceil(h)
    if lo == hi:
        return float(sorted_values[lo])
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (h - lo)


def _confidence(n: int) -> str:
    """样本量→置信度档位;n < MIN_SAMPLES 由调用方拦掉不落账,这里只分 low/medium。"""
    return "medium" if n >= MEDIUM_SAMPLES else "low"


# ── 数据装载:每渠道日增量序列(纯读)─────────────────────────────────


def _load_daily_series(conn: Any, lookback_days: int) -> dict[int, list[float]]:
    """按 (channel_id, snapshot_date) 聚合日播放增量;判键拉回 Python 容错。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
    rows = conn.execute(
        f"""
        SELECT channel_id, snapshot_date, SUM(views_delta) AS day_views
        FROM {SOURCE_TABLE}
        WHERE snapshot_date >= ?
        GROUP BY channel_id, snapshot_date
        ORDER BY channel_id ASC, snapshot_date ASC
        """,
        (cutoff,),
    ).fetchall()
    series: dict[int, list[float]] = {}
    for raw in rows:
        row = dict(raw)
        channel_id = _int_or_none(row.get("channel_id"))
        day = _date_key(row.get("snapshot_date"))
        day_views = _float_or_none(row.get("day_views"))
        if channel_id is None or day is None or day_views is None:
            continue
        series.setdefault(channel_id, []).append(day_views)
    return series


# ── 主入口:每日基线落账 ─────────────────────────────────────────────


def run_daily_baseline(
    max_channels: int = MAX_CHANNELS,
    lookback_days: int = LOOKBACK_DAYS,
) -> dict[str, Any]:
    """每渠道日增量经验分位数 → prediction_runs 落账(渠道+UTC日幂等);永不抛。

    返回 {status, channels_seen, recorded, deduped, skipped_data_missing,
    failed, run_date, lookback_days};表未建 / 零行诚实 empty。
    """
    max_channels = max(1, min(int(max_channels), 1000))
    lookback_days = max(7, min(int(lookback_days), 365))
    run_date = datetime.now(timezone.utc).date().isoformat()
    result: dict[str, Any] = {
        "status": "ok",
        "channels_seen": 0,
        "recorded": 0,
        "deduped": 0,
        "skipped_data_missing": 0,
        "failed": 0,
        "run_date": run_date,
        "lookback_days": lookback_days,
    }
    try:
        from app.db.connection import get_conn, table_exists
        from app.domains.market_brain import prediction_ledger

        if not table_exists(SOURCE_TABLE):
            result["status"] = "empty"
            result["reason"] = f"{SOURCE_TABLE} 未建(迁移 073 未 apply),无日增量序列可算基线。"
            return result

        series = _load_daily_series(get_conn(), lookback_days)
        if not series:
            result["status"] = "empty"
            result["reason"] = f"{SOURCE_TABLE} 近 {lookback_days} 天零行——无快照即无基线,诚实空态。"
            return result

        for channel_id in sorted(series)[:max_channels]:
            values = sorted(series[channel_id])
            result["channels_seen"] += 1
            n = len(values)
            if n < MIN_SAMPLES:
                # 样本荒诚实不落账:两三个点的「区间」是伪精确(confidence='data_missing' 口径)。
                result["skipped_data_missing"] += 1
                continue
            p10 = _quantile(values, 0.10)
            p50 = _quantile(values, 0.50)
            p90 = _quantile(values, 0.90)
            out = prediction_ledger.record_prediction_run(
                run_id=f"blchan_{channel_id}_{run_date}",
                model_name=MODEL_NAME,
                model_version=MODEL_VERSION,
                task_type=TASK_TYPE,
                prediction={
                    "p10": p10, "p50": p50, "p90": p90,
                    "channel_id": channel_id,
                    "sample_size": n,
                    "unit": "views_delta_per_day",
                },
                channel=str(channel_id),
                horizon_days=HORIZON_DAYS,
                p10=p10,
                p50=p50,
                p90=p90,
                confidence=_confidence(n),
                input_summary={"samples": n, "lookback_days": lookback_days, "run_date": run_date},
                basis=[f"近 {lookback_days} 天 {n} 个日增量点的经验分位数(线性插值,可复算)"],
            )
            if out.get("ok"):
                result["recorded"] += 1
                if out.get("deduped"):
                    result["deduped"] += 1
            else:
                result["failed"] += 1
        return result
    except Exception as exc:  # noqa: BLE001 — 基线是增益件,永不炸调用方(cron)
        logger.warning("baseline_forecast.run_daily_baseline failed: %s", exc, exc_info=True)
        result["status"] = "error"
        result["reason"] = str(exc)[:300]
        return result


__all__ = ["run_daily_baseline", "MIN_SAMPLES", "TASK_TYPE", "MODEL_NAME", "SOURCE_TABLE"]
