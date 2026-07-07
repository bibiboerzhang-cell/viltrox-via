"""W9 漂移监控:预测残差分布与 KOL 核心特征的周对比(Evidently 纯库模式最小接法)。

职责(评估在线余量·漂移哨兵):
  - psi:群体稳定性指数(Population Stability Index),两分布的等宽分箱占比差异,
    纯函数,空样本 / 单点质量诚实降级。
  - residual_stats / compare_residuals:预测残差(error_abs)分布的均值/方差,
    以及参照期→当前期的均值漂移与方差比(纯函数)。
  - compute_drift_report:组合残差漂移 + 特征 PSI,产出一份漂移报告 dict;
    engine 字段标注走 evidently 还是自实现(builtin)。
  - record_drift_metrics:把报告落 vkpi_signal_ledger(source_type='drift_monitor'),
    复用 signal_ledger.record_signal 的幂等 UPSERT 与诚实降级。
  - run_drift_monitor:cron 友好的一键——从 vkpi_prediction_evals 取近两窗残差算漂移
    并落账;表未建 / 零样本诚实 empty,永不抛(增益件绝不炸调用方)。

Evidently 约定:纯库调用,未装则优雅降级为自实现最小漂移指标(不引硬依赖,
importlib 探测 spec 不 import)。evidently 在装时经 _evidently_feature_drift 取列漂移
verdict 附在报告里;任何版本不合/异常都 logger.warning 后回落 builtin,绝不静默吞。

红线:表未 apply 诚实降级不炸;零 LLM、零采集;绝不写 viltrox_fit_score、不碰 rule_v0。
本车道只交付函数 + 单测,不挂 job;后续挂载点见 run_drift_monitor docstring。
"""
from __future__ import annotations

import importlib.util
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SOURCE_TYPE = "drift_monitor"

# PSI 常用判读阈:< 0.1 稳定 / 0.1-0.25 中度 / >= 0.25 显著漂移(业界惯例)。
PSI_MODERATE = 0.1
PSI_SIGNIFICANT = 0.25
# 方差比越界视为分布形变(放大 2 倍或缩到一半)。
VARIANCE_RATIO_HI = 2.0
VARIANCE_RATIO_LO = 0.5


# ── 小工具(compat 宽容层,与 signal_ledger 同款口径) ────────────────


def _text_or_none(value: Any, limit: int = 300) -> str | None:
    text = " ".join(str(value or "").replace("\x00", " ").split())[:limit]
    return text or None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_floats(values: Any) -> list[float]:
    return [v for v in (_float_or_none(x) for x in (values or [])) if v is not None]


def _evidently_available() -> bool:
    """探测 evidently 是否可用(只查 spec 不 import,零副作用零硬依赖)。"""
    return importlib.util.find_spec("evidently") is not None


# ── 纯函数:PSI ──────────────────────────────────────────────────────


def psi(reference: Any, current: Any, bins: int = 10) -> float | None:
    """群体稳定性指数:sum((cur_pct - ref_pct) * ln(cur_pct / ref_pct))(等宽分箱)。

    - 任一侧无有效样本 → None(无从比对,诚实缺席)。
    - 参照+当前合并值域为单点(min==max)→ 0.0(同一点质量,无漂移)。
    - 空箱以 eps 兜底避免 log(0) 爆炸;结果保 6 位。
    """
    ref = _clean_floats(reference)
    cur = _clean_floats(current)
    if not ref or not cur:
        return None
    bins = max(2, min(int(bins), 50))
    lo = min(min(ref), min(cur))
    hi = max(max(ref), max(cur))
    if hi <= lo:
        return 0.0
    width = (hi - lo) / bins

    def _hist(values: list[float]) -> list[float]:
        counts = [0] * bins
        for value in values:
            idx = int((value - lo) / width)
            if idx < 0:
                idx = 0
            elif idx >= bins:
                idx = bins - 1
            counts[idx] += 1
        total = len(values)
        return [c / total for c in counts]

    ref_pct = _hist(ref)
    cur_pct = _hist(cur)
    eps = 1e-6
    score = 0.0
    for r_pct, c_pct in zip(ref_pct, cur_pct):
        r_safe = max(r_pct, eps)
        c_safe = max(c_pct, eps)
        score += (c_safe - r_safe) * math.log(c_safe / r_safe)
    return round(score, 6)


def psi_flag(score: Any) -> str | None:
    """PSI 判读:None → None(未知);<0.1 stable / <0.25 moderate / 否则 significant。"""
    value = _float_or_none(score)
    if value is None:
        return None
    if value < PSI_MODERATE:
        return "stable"
    if value < PSI_SIGNIFICANT:
        return "moderate"
    return "significant"


# ── 纯函数:残差分布对比 ─────────────────────────────────────────────


def residual_stats(values: Any) -> dict[str, Any]:
    """一批残差 → {n, mean, variance}(总体方差;空样本 n=0 均 None,诚实态)。"""
    vals = _clean_floats(values)
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "variance": None}
    mean = sum(vals) / n
    variance = sum((v - mean) ** 2 for v in vals) / n
    return {"n": n, "mean": round(mean, 6), "variance": round(variance, 6)}


def compare_residuals(reference: Any, current: Any) -> dict[str, Any]:
    """参照期→当前期残差对比:均值漂移(current-reference)与方差比(current/reference)。

    任一侧缺均值 → mean_shift None;参照方差为 0 或缺 → variance_ratio None(分母 0 安全)。
    """
    ref = residual_stats(reference)
    cur = residual_stats(current)
    mean_shift = (round(cur["mean"] - ref["mean"], 6)
                  if ref["mean"] is not None and cur["mean"] is not None else None)
    variance_ratio = (round(cur["variance"] / ref["variance"], 6)
                      if ref["variance"] not in (None, 0) and cur["variance"] is not None else None)
    return {"reference": ref, "current": cur,
            "mean_shift": mean_shift, "variance_ratio": variance_ratio}


def _variance_drift(variance_ratio: float | None) -> bool | None:
    if variance_ratio is None:
        return None
    return variance_ratio > VARIANCE_RATIO_HI or variance_ratio < VARIANCE_RATIO_LO


def _drift_detected(feature_psi: float | None, variance_ratio: float | None) -> bool | None:
    """漂移判定:特征 PSI 显著 或 方差越界即 True;两项皆未知 → None(诚实不判)。"""
    psi_significant = None if feature_psi is None else feature_psi >= PSI_SIGNIFICANT
    var_drift = _variance_drift(variance_ratio)
    if psi_significant is None and var_drift is None:
        return None
    return bool(psi_significant) or bool(var_drift)


# ── evidently 最小接法(装了才走,异常回落 builtin) ─────────────────


def _evidently_feature_drift(reference: Any, current: Any, feature_name: str | None) -> dict[str, Any] | None:
    """evidently 列漂移 verdict(装了 evidently + pandas 才走);任何异常回落 builtin。

    返回 {drift_score, drift_detected, stattest} 或 None(未装 / 版本不合 / 样本不足)。
    """
    if not _evidently_available():
        return None
    ref = _clean_floats(reference)
    cur = _clean_floats(current)
    if not ref or not cur:
        return None
    column = _text_or_none(feature_name, 80) or "feature"
    try:
        import pandas as pd
        from evidently.metrics import ColumnDriftMetric
        from evidently.report import Report

        report = Report(metrics=[ColumnDriftMetric(column_name=column)])
        report.run(
            reference_data=pd.DataFrame({column: ref}),
            current_data=pd.DataFrame({column: cur}),
        )
        metric = report.as_dict()["metrics"][0]["result"]
        return {
            "drift_score": _float_or_none(metric.get("drift_score")),
            "drift_detected": bool(metric.get("drift_detected")),
            "stattest": _text_or_none(metric.get("stattest_name"), 80),
        }
    except Exception as exc:  # noqa: BLE001 — evidently 版本不合诚实回落 builtin,不炸调用方
        logger.warning("drift_monitor evidently path failed, falling back to builtin: %s", exc)
        return None


# ── 组合报告 ────────────────────────────────────────────────────────


def compute_drift_report(
    reference_residuals: Any,
    current_residuals: Any,
    *,
    feature_reference: Any = None,
    feature_current: Any = None,
    feature_name: str | None = None,
) -> dict[str, Any]:
    """一份漂移报告:预测残差分布对比 + 可选 KOL 核心特征 PSI。

    engine:'evidently'(装了且列漂移取到)/ 'builtin'(自实现)。特征两侧给齐才算
    PSI;drift_detected 综合特征 PSI 显著与残差方差越界。纯函数(evidently 分支
    内部异常自吞回落),绝不抛。
    """
    residual = compare_residuals(reference_residuals, current_residuals)
    feature_psi = None
    if feature_reference is not None and feature_current is not None:
        feature_psi = psi(feature_reference, feature_current)

    evidently_drift = _evidently_feature_drift(feature_reference, feature_current, feature_name)
    engine = "evidently" if evidently_drift is not None else "builtin"
    return {
        "engine": engine,
        "residual": residual,
        "feature_name": _text_or_none(feature_name, 120),
        "feature_psi": feature_psi,
        "psi_flag": psi_flag(feature_psi),
        "drift_detected": _drift_detected(feature_psi, residual.get("variance_ratio")),
        "evidently": evidently_drift,
    }


# ── 落账:写入信号账本(source_type='drift_monitor') ──────────────────


def record_drift_metrics(
    report: dict[str, Any],
    *,
    sku: str | None = None,
    market: str | None = None,
    feature_name: str | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """漂移报告 → vkpi_signal_ledger(source_type='drift_monitor');复用 record_signal 幂等。

    dedupe_key 缺省按 特征名 + UTC 日 组(同日重跑覆盖)。表未建 → record_signal 回
    {ok: False, reason: 'table_missing'};signal_value 记特征 PSI 便于聚合排序。
    """
    from app.domains.market_brain import signal_ledger

    report = report or {}
    fname = _text_or_none(feature_name or report.get("feature_name"), 120) or "prediction_residual"
    residual = report.get("residual") or {}
    key = _text_or_none(dedupe_key, 300) or (
        f"drift_{fname}_{datetime.now(timezone.utc).date().isoformat()}"
    )
    text = (
        f"漂移监控 engine={report.get('engine')} feature={fname} "
        f"psi={report.get('feature_psi')} flag={report.get('psi_flag')} "
        f"mean_shift={residual.get('mean_shift')} var_ratio={residual.get('variance_ratio')} "
        f"drift={report.get('drift_detected')}"
    )
    return signal_ledger.record_signal(
        SOURCE_TYPE,
        fname,
        "drift_check",
        text,
        key,
        sku=sku,
        market=market,
        signal_value=_float_or_none(report.get("feature_psi")),
        normalized={"report": report},
    )


# ── cron 友好一键(本车道不挂 job,仅备) ────────────────────────────


def run_drift_monitor(
    window_days: int = 7,
    *,
    feature_reference: Any = None,
    feature_current: Any = None,
    feature_name: str | None = None,
) -> dict[str, Any]:
    """从 vkpi_prediction_evals 取近两窗残差(error_abs)算漂移并落账;永不抛。

    参照期 = [now-2w, now-1w),当前期 = [now-1w, now)。KOL 核心特征两侧可由调用方
    传入(feature_reference / feature_current)做 PSI;不传则只出残差漂移。
    表未建 / 两窗任一零样本 → status='empty' 诚实态。

    后续挂载点(本车道不接):在 scheduler 里包一个 config-gate 的周 job(仿
    job_vkpi_prediction_weekly_rollup),feature_* 由 KOL 特征快照喂入,即成漂移哨兵 cron。
    """
    window_days = max(1, min(int(window_days), 90))
    result: dict[str, Any] = {
        "status": "ok", "recorded": False, "signal_id": None,
        "report": None, "window_days": window_days,
    }
    try:
        from app.db.connection import get_conn, table_exists
        from app.domains.market_brain import prediction_ledger

        if not table_exists(prediction_ledger.EVALS_TABLE):
            result["status"] = "empty"
            result["reason"] = f"{prediction_ledger.EVALS_TABLE} 未建(迁移 221 未 apply),无残差可算漂移。"
            return result

        conn = get_conn()
        now = datetime.now(timezone.utc)
        current_cut = now - timedelta(days=window_days)
        reference_cut = now - timedelta(days=2 * window_days)
        current_rows = conn.execute(
            f"""
            SELECT error_abs FROM {prediction_ledger.EVALS_TABLE}
            WHERE evaluated_at >= ? AND error_abs IS NOT NULL
            ORDER BY id DESC LIMIT ?
            """,
            (current_cut, 5000),
        ).fetchall()
        reference_rows = conn.execute(
            f"""
            SELECT error_abs FROM {prediction_ledger.EVALS_TABLE}
            WHERE evaluated_at >= ? AND evaluated_at < ? AND error_abs IS NOT NULL
            ORDER BY id DESC LIMIT ?
            """,
            (reference_cut, current_cut, 5000),
        ).fetchall()

        current = [dict(r).get("error_abs") for r in current_rows]
        reference = [dict(r).get("error_abs") for r in reference_rows]
        if not current or not reference:
            result["status"] = "empty"
            result["reason"] = "近两窗残差样本不足(参照期或当前期为空),无从比对漂移。"
            result["current_n"] = len(current)
            result["reference_n"] = len(reference)
            return result

        report = compute_drift_report(
            reference, current,
            feature_reference=feature_reference,
            feature_current=feature_current,
            feature_name=feature_name,
        )
        result["report"] = report
        recorded = record_drift_metrics(report, feature_name=feature_name)
        result["recorded"] = bool(recorded.get("ok"))
        result["signal_id"] = recorded.get("id")
        if not recorded.get("ok"):
            result["signal_reason"] = recorded.get("reason")
        return result
    except Exception as exc:  # noqa: BLE001 — 漂移哨兵是增益件,永不炸调用方(cron)
        logger.warning("drift_monitor.run_drift_monitor failed: %s", exc, exc_info=True)
        result["status"] = "error"
        result["reason"] = str(exc)[:300]
        return result


__all__ = [
    "psi", "psi_flag", "residual_stats", "compare_residuals", "compute_drift_report",
    "record_drift_metrics", "run_drift_monitor", "SOURCE_TYPE",
]
