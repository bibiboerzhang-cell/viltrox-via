"""
services/scheduler/jobs_tasks_gtm.py — GTM 闭环 + 推论点火定时任务簇
====================================================================
从 jobs_tasks.py 行为不变搬来(治千行卫兵):GTM 裁决链头/三窗回填 + 预测对账/
周评估/经验分位数日基线,共 5 个每日任务。jobs_tasks.py 通过
`from .jobs_tasks_gtm import (...)` re-export 兜住所有调用点(jobs.py 零改动)。

红线对齐(与 jobs_tasks.py 原注释同款):全部 config-gate 默认 OFF;只生成待人
裁决任务绝不写 decision;零 LLM;零触 viltrox_fit_score。函数体逐字不变。
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger

logger = get_logger(__name__)


def _scheduler_task_enabled(task_key: str, *, default: bool = False) -> bool:
    # lazy 代理:真实现在 jobs_tasks.py 656 行(定义晚于本模块被其底部 re-export 导入)。
    from .jobs_tasks import _scheduler_task_enabled as _impl
    return _impl(task_key, default=default)


def _record_scheduler_run(task_key: str, *, ok: bool, error: str = '') -> None:
    from .jobs_tasks import _record_scheduler_run as _impl
    return _impl(task_key, ok=ok, error=error)


async def job_vkpi_gtm_spawn_verdicts():
    """GTM 裁决闭环·链头:每日扫到期未裁决 bet → 生成 gtm_verdict 置顶裁决任务(幂等)。

    走 verdict_flow.spawn_due_verdicts(dry_run=False, limit=100):只生成待人裁决任务,
    绝不写 decision(自动裁决路径不存在);同 dedupe_key 已存在即跳过,复跑零新增。
    config-gate:scheduler_tasks.vkpi_gtm_spawn_verdicts(默认 OFF,迁移 218 种子)。零触 viltrox_fit_score。
    """
    if not _scheduler_task_enabled("vkpi_gtm_spawn_verdicts"):
        return
    try:
        from app.domains.market_brain import verdict_flow

        result = await asyncio.to_thread(verdict_flow.spawn_due_verdicts, False, limit=100)
        logger.info(
            "scheduler.vkpi_gtm_spawn_verdicts",
            extra={
                "due_count": result.get("due_count"),
                # 'created' 是 logging 保留字段(见 fulfillment_due_scan 同类修复),改 created_count。
                "created_count": result.get("created"),
                "skipped_existing": result.get("skipped_existing"),
            },
        )
        _record_scheduler_run("vkpi_gtm_spawn_verdicts", ok=True)
    except Exception as exc:
        logger.exception("scheduler.vkpi_gtm_spawn_verdicts_failed")
        _record_scheduler_run("vkpi_gtm_spawn_verdicts", ok=False, error=str(exc)[:240])


async def job_vkpi_gtm_windows_refresh():
    """GTM 裁决闭环·证据段:每日对未裁决 gtm_outcomes 行按账龄回填 7/14/28 三窗证据。

    走 gtm_windows.refresh_gtm_windows(dry_run=False):幂等覆盖未裁决行三窗(filled_at 刷新);
    已裁决行冻结绝不再刷,只写 window_* 三列绝不触 decision。
    config-gate:scheduler_tasks.vkpi_gtm_windows_refresh(默认 OFF,迁移 218 种子)。零触 viltrox_fit_score。
    """
    if not _scheduler_task_enabled("vkpi_gtm_windows_refresh"):
        return
    try:
        from app.domains.market_brain import gtm_windows

        result = await asyncio.to_thread(gtm_windows.refresh_gtm_windows, dry_run=False)
        logger.info(
            "scheduler.vkpi_gtm_windows_refresh",
            extra={
                "refresh_status": str(result.get("status")),
                "scanned": result.get("scanned"),
                "updated_rows": result.get("updated_rows"),
            },
        )
        _record_scheduler_run("vkpi_gtm_windows_refresh", ok=True)
    except Exception as exc:
        logger.exception("scheduler.vkpi_gtm_windows_refresh_failed")
        _record_scheduler_run("vkpi_gtm_windows_refresh", ok=False, error=str(exc)[:240])


async def job_vkpi_forecast_outcomes_refresh():
    """B3 学习闭环·预测对账:每日回查满窗(30 天)仍 pending 的 vkpi_forecast_log 行,
    填实际播放中位数并判 outcome(hit_in_band / below / above)。

    走 learning.forecast_feedback.refresh_forecast_outcomes(limit=500,每日多跑几轮自然清空);
    窗口内无新发证据保持 pending(诚实等料,绝不硬判)。
    config-gate:scheduler_tasks.vkpi_forecast_outcomes_refresh(默认 OFF,迁移 222 种子)。
    唯一写入是流水表 actual_views/actual_at/outcome 三列;零触 viltrox_fit_score。
    """
    if not _scheduler_task_enabled("vkpi_forecast_outcomes_refresh"):
        return
    try:
        from app.domains.learning import forecast_feedback

        result = await asyncio.to_thread(forecast_feedback.refresh_forecast_outcomes, 30, 500)
        logger.info(
            "scheduler.vkpi_forecast_outcomes_refresh",
            extra={
                "refresh_status": str(result.get("status")),
                "scanned": result.get("scanned"),
                "updated": result.get("updated"),
                "kept_pending_no_evidence": result.get("kept_pending_no_evidence"),
            },
        )
        _record_scheduler_run("vkpi_forecast_outcomes_refresh", ok=True)
    except Exception as exc:
        logger.exception("scheduler.vkpi_forecast_outcomes_refresh_failed")
        _record_scheduler_run("vkpi_forecast_outcomes_refresh", ok=False, error=str(exc)[:240])


def _prediction_weekly_rollup_sync(scan_limit: int = 500) -> dict:
    """同步实现:①已裁决预测流水补账进 evals(幂等)→ ②近 7 天评估出周指标 → ③落信号账本。

    ① 扫 vkpi_forecast_log 已裁决行(outcome 非 pending 且 actual_views 已回填),对
       run_id='fclog_<行id>' 逐条 prediction_ledger.record_eval((org, run_id, outcome_id NULL)
       幂等 UPSERT;prediction_runs 里没有该 run(双写晚于该行)→ run_not_found 诚实跳过)。
    ② 近 7 天 vkpi_prediction_evals 全量喂 weekly_rollup(纯函数)出
       wape / interval_coverage / direction_hit_rate。
    ③ 结果落 vkpi_signal_ledger(source_type='internal_eval',dedupe_key 带 ISO 周号,
       同周重跑 UPSERT 幂等)。表未建诚实 empty,绝不抛。
    """
    from datetime import datetime, timedelta, timezone

    from app.db.connection import get_conn, table_exists
    from app.domains.market_brain import (
        outreach_truth_bridge,
        prediction_ledger,
        signal_ledger,
    )
    from app.domains.market_brain.data_readiness import (
        outcome_evidence_sql,
        verified_prediction_binding_sql,
        verified_prediction_event_sql,
    )

    result: dict = {
        "status": "ok", "scanned": 0, "evals_recorded": 0, "evals_deduped": 0,
        "evals_skipped_no_run": 0, "rollup": None, "signal_id": None,
    }
    if not table_exists("vkpi_forecast_log") or not table_exists(prediction_ledger.RUNS_TABLE) \
            or not table_exists(prediction_ledger.EVALS_TABLE):
        result["status"] = "empty"
        result["reason"] = "预测流水/账本表未建(迁移 215/220/221 未 apply),无从周评估。"
        return result

    conn = get_conn()
    # ① 已裁决流水行 → record_eval 幂等补账(run 缺席 = 该行早于双写上线,诚实跳过)。
    rows = conn.execute(
        """
        SELECT id, actual_views
        FROM vkpi_forecast_log
        WHERE outcome IN ('hit_in_band', 'below', 'above') AND actual_views IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, min(int(scan_limit), 2000)),),
    ).fetchall()
    for raw in rows:
        row = dict(raw)
        result["scanned"] += 1
        try:
            log_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        out = prediction_ledger.record_eval(f"fclog_{log_id}", row.get("actual_views"))
        if out.get("ok"):
            result["evals_recorded"] += 1
            if out.get("deduped"):
                result["evals_deduped"] += 1
        elif out.get("reason") == "run_not_found":
            result["evals_skipped_no_run"] += 1

    # ② 近 7 天评估行 → 周指标(纯函数;空样本 n=0 全 None,诚实态)。
    #    LEFT JOIN runs 按 (org, run_id) 取 source_step/product_sku/market/channel/
    #    baseline_value 一并喂进 weekly_rollup:fva 段据此按 (sku, market, channel) 分组
    #    算 model vs baseline 误差增量。LEFT JOIN 保 run 缺席(理论不该有)也不掉 eval 行,
    #    wape/interval_coverage/direction_hit_rate 口径原样不变。
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    verified_binding = verified_prediction_binding_sql("e")
    verified_event = verified_prediction_event_sql("e")
    verified_outcome = outcome_evidence_sql("o")
    eval_rows = conn.execute(
        f"""
        SELECT e.run_id, e.outcome_id, e.actual_value, e.error_abs,
               e.interval_hit, e.direction_hit,
               r.task_type, r.p50, r.source_step, r.product_sku, r.market, r.channel,
               r.baseline_value,
               CASE WHEN e.outcome_id IS NOT NULL
                     AND {verified_binding} AND {verified_event}
                     AND o.id IS NOT NULL AND o.decision <> 'open'
                     AND o.decided_at IS NOT NULL AND o.decided_by IS NOT NULL
                     AND ({verified_outcome})
                    THEN TRUE ELSE FALSE END AS verified_actual
        FROM {prediction_ledger.EVALS_TABLE} e
        LEFT JOIN {prediction_ledger.RUNS_TABLE} r
            ON r.organization_id = e.organization_id AND r.run_id = e.run_id
        LEFT JOIN vkpi_gtm_outcomes o ON o.id = e.outcome_id
        WHERE e.evaluated_at >= ?
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (cutoff, 2000),
    ).fetchall()
    coverage = outreach_truth_bridge.outreach_prediction_coverage(conn)
    rollup = prediction_ledger.weekly_rollup(
        [dict(r) for r in eval_rows], outreach_coverage=coverage,
    )
    result["rollup"] = rollup

    # ③ 周汇总信号落账(dedupe_key 带 ISO 周号,同周重跑幂等覆盖)。
    #    fva 段(model vs baseline 增量)摘要一并进 normalized,供大脑读增益不必展开 rollup。
    iso = datetime.now(timezone.utc).isocalendar()
    week_key = f"{iso[0]}W{int(iso[1]):02d}"
    fva = rollup.get("fva") or {}
    signal = signal_ledger.record_signal(
        "internal_eval",
        "prediction_weekly_rollup",
        "weekly_eval",
        (
            f"预测周评估 {week_key}:样本 {rollup.get('n')},wape={rollup.get('wape')},"
            f"brier={rollup.get('brier_score')},"
            f"interval_coverage={rollup.get('interval_coverage')},"
            f"direction_hit_rate={rollup.get('direction_hit_rate')}。"
        ),
        f"prediction_weekly_rollup_{week_key}",
        signal_value=(
            rollup.get("wape")
            if rollup.get("wape") is not None else rollup.get("brier_score")
        ),
        sample_size=rollup.get("n"),
        normalized={
            "rollup": rollup,
            "week": week_key,
            "fva": {
                "n_groups": fva.get("n_groups"),
                "mean_delta": fva.get("mean_delta"),
                "model_better_share": fva.get("model_better_share"),
            },
        },
    )
    result["signal_id"] = signal.get("id")
    if not signal.get("ok"):
        result["signal_reason"] = signal.get("reason")
    return result


async def job_vkpi_prediction_weekly_rollup():
    """W6 预测账本·周评估:已裁决预测流水补账进 evals + 出周指标(wape/带内率/方向命中)。

    每周一跑,同周重跑幂等(evals (org,run_id,outcome_id) 幂等,汇总信号按周号去重)。
    config-gate:scheduler_tasks.vkpi_prediction_weekly_rollup(默认 OFF,迁移 222 种子)。
    只读流水 + 写评估/信号账本;零 LLM;零触 viltrox_fit_score。
    """
    if not _scheduler_task_enabled("vkpi_prediction_weekly_rollup"):
        return
    try:
        result = await asyncio.to_thread(_prediction_weekly_rollup_sync)
        rollup = result.get("rollup") or {}
        logger.info(
            "scheduler.vkpi_prediction_weekly_rollup",
            extra={
                "rollup_status": str(result.get("status")),
                "scanned": result.get("scanned"),
                "evals_recorded": result.get("evals_recorded"),
                "evals_skipped_no_run": result.get("evals_skipped_no_run"),
                "wape": rollup.get("wape"),
                "interval_coverage": rollup.get("interval_coverage"),
                "direction_hit_rate": rollup.get("direction_hit_rate"),
            },
        )
        _record_scheduler_run("vkpi_prediction_weekly_rollup", ok=True)
    except Exception as exc:
        logger.exception("scheduler.vkpi_prediction_weekly_rollup_failed")
        _record_scheduler_run("vkpi_prediction_weekly_rollup", ok=False, error=str(exc)[:240])


async def job_vkpi_baseline_forecast_daily():
    """经验分位数日基线:每渠道日播放增量序列的经验 p10/p50/p90 → prediction_runs 落账。

    走 market_brain.baseline_forecast.run_daily_baseline(task_type='channel_views_daily',
    horizon_days=1,run_id 带渠道+UTC日幂等;样本<8 诚实不落账)。给「聪明模型必须
    打赢的底线」提供每日真基线。config-gate:scheduler_tasks.vkpi_baseline_forecast_daily
    (默认 OFF,迁移 222 种子)。纯读快照表 + 写预测账本;零 LLM;零触 viltrox_fit_score。
    """
    if not _scheduler_task_enabled("vkpi_baseline_forecast_daily"):
        return
    try:
        from app.domains.market_brain import baseline_forecast

        result = await asyncio.to_thread(baseline_forecast.run_daily_baseline)
        logger.info(
            "scheduler.vkpi_baseline_forecast_daily",
            extra={
                "baseline_status": str(result.get("status")),
                "channels_seen": result.get("channels_seen"),
                "recorded": result.get("recorded"),
                "skipped_data_missing": result.get("skipped_data_missing"),
            },
        )
        _record_scheduler_run("vkpi_baseline_forecast_daily", ok=True)
    except Exception as exc:
        logger.exception("scheduler.vkpi_baseline_forecast_daily_failed")
        _record_scheduler_run("vkpi_baseline_forecast_daily", ok=False, error=str(exc)[:240])


async def job_vkpi_drift_monitor():
    """W9 漂移哨兵:每周从 vkpi_prediction_evals 近两窗残差算漂移并落信号账本。

    走 market_brain.drift_monitor.run_drift_monitor(window_days=7):参照期 vs 当前期
    残差(error_abs)漂移;表未建/样本荒 → status='empty' 诚实态永不抛。evidently 装了
    走库漂移否则 builtin PSI。config-gate:scheduler_tasks.vkpi_drift_monitor
    (默认 OFF,迁移 224 种子)。纯读评估账本 + 写信号账本;零 LLM;零触 viltrox_fit_score。
    """
    if not _scheduler_task_enabled("vkpi_drift_monitor"):
        return
    try:
        from app.domains.market_brain import drift_monitor

        result = await asyncio.to_thread(drift_monitor.run_drift_monitor, 7)
        logger.info(
            "scheduler.vkpi_drift_monitor",
            extra={
                "drift_status": str(result.get("status")),
                "recorded": result.get("recorded"),
                "engine": result.get("engine"),
                "residual_drift": result.get("residual_drift"),
            },
        )
        _record_scheduler_run("vkpi_drift_monitor", ok=True)
    except Exception as exc:
        logger.exception("scheduler.vkpi_drift_monitor_failed")
        _record_scheduler_run("vkpi_drift_monitor", ok=False, error=str(exc)[:240])
