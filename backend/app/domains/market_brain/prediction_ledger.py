"""W6 预测账本域层:vkpi_prediction_runs / vkpi_prediction_evals(迁移 220/221)的读写门面。

职责(执行路线第 2 刀 / 全景规格 5.2 节 B+C 表):
  - record_prediction_run:每次模型/规则/LLM 预测落账,(organization_id, run_id)
    幂等 UPSERT;input_fingerprint 保同输入可对账(缺省按输入摘要哈希)。
  - compute_eval_metrics:纯函数误差指标——error_abs(实际减 p50 取绝对值)/
    error_pct(分母 0 安全)/ interval_hit(p10<=actual<=p90,None 容错)/
    direction_hit(prev_actual 给定时判涨跌方向)。
  - record_eval:读回该 run 的 p10/p50/p90 → compute_eval_metrics → UPSERT 进
    evals((org, run_id, outcome_id) 口径;outcome_id 可空,NULL 不触发 PG 唯一
    约束冲突,故用先查后写的手工 UPSERT);run 不存在 → {ok: False, reason: 'run_not_found'}。
  - weekly_rollup:纯函数周评估——wape / interval_coverage / direction_hit_rate / n
    (空列表 n=0 全 None,诚实态)。

与 app/domains/agents/prediction_ledger 的分工:那边是既有预测战绩汇总
(ledger_summary,读推荐/押注/告警等旧账);本模块是 GTM 级通用预测运行账本
(任意 task_type,220/221 新表),互不替代、互不 import。

红线:
  - 表未 apply(迁移 220/221 并行在建)诚实降级不炸:写返回
    {ok: False, reason: 'table_missing'},绝不抛未捕获异常;
  - 零 LLM、零采集;绝不写 viltrox_fit_score、不碰 rule_v0。
compat 约定:SQL 占位符 ?;零字面 percent(不用 LIKE);jsonb 写走 ?::jsonb + json.dumps;
BOOLEAN 读回宽容(_bool_or_none,int 1/0 / 't' 都认)。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

RUNS_TABLE = "vkpi_prediction_runs"
EVALS_TABLE = "vkpi_prediction_evals"

# 商业化前多租户安全字段占位(迁移 220/221 列默认一致,单租户先缺省)。
DEFAULT_ORG = "viltrox"

# 置信度档位(DDL 口径 low / medium / high);未申报按最保守档,绝不虚标。
DEFAULT_CONFIDENCE = "low"

# record_prediction_run 认识的可选列(未知 kw 只 debug 留痕,绝不静默进库)。
_RUN_OPTIONAL_KEYS = (
    "organization_id", "sku", "product_sku", "market", "channel", "horizon_days",
    "input_fingerprint", "input_summary", "p10", "p50", "p90",
    "confidence", "confidence_score", "missing_data", "basis",
)
_EVAL_OPTIONAL_KEYS = (
    "organization_id", "prev_actual", "actual_json", "calibrated_bucket", "notes",
)


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


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    """BOOLEAN 三态读回:None/'' → None(未知);int 1/0、't'/'f' 等 compat 形态都认。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "t", "true", "yes", "y"):
        return True
    if text in ("0", "f", "false", "no", "n"):
        return False
    return None


def _dumps(value: Any, empty: Any = None) -> str:
    return json.dumps(value if value is not None else (empty if empty is not None else {}),
                      ensure_ascii=False, default=str)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# ── 预测落账:幂等 UPSERT ────────────────────────────────────────────


def record_prediction_run(
    run_id: str,
    model_name: str,
    model_version: str,
    task_type: str,
    prediction: dict[str, Any],
    **kw: Any,
) -> dict[str, Any]:
    """预测运行落账:(organization_id, run_id) 幂等 UPSERT。

    重跑语义:同 run_id 再来 → 预测正文/分位/置信度以最新为准,created_at 保留
    首次落账时刻(对答案窗口锚点不漂移)。返回 {ok, id|None, deduped: bool};
    表未建 → {ok: False, reason: 'table_missing'}。
    """
    required = {
        "run_id": _text_or_none(run_id, 200),
        "model_name": _text_or_none(model_name, 120),
        "model_version": _text_or_none(model_version, 120),
        "task_type": _text_or_none(task_type, 80),
    }
    missing = [key for key, val in required.items() if not val]
    if prediction is None:
        missing.append("prediction")
    if missing:
        return {"ok": False, "id": None, "deduped": False,
                "reason": "missing_required_field", "missing": missing}

    unknown = sorted(set(kw) - set(_RUN_OPTIONAL_KEYS))
    if unknown:
        logger.debug("prediction_ledger.record_prediction_run unknown kwargs ignored: %s", unknown)

    org = _text_or_none(kw.get("organization_id"), 80) or DEFAULT_ORG
    sku = _text_or_none(kw.get("sku") or kw.get("product_sku"), 120)
    market = _text_or_none(kw.get("market"), 40)
    channel = _text_or_none(kw.get("channel"), 60)
    horizon_days = _int_or_none(kw.get("horizon_days"))
    fingerprint = _text_or_none(kw.get("input_fingerprint"), 200) or _fingerprint({
        "task_type": required["task_type"], "product_sku": sku, "market": market,
        "channel": channel, "horizon_days": horizon_days, "prediction": prediction,
    })
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(RUNS_TABLE):
            return {"ok": False, "id": None, "deduped": False, "reason": "table_missing"}

        conn = get_conn()
        existing = conn.execute(
            f"SELECT id FROM {RUNS_TABLE} WHERE organization_id = ? AND run_id = ?",
            (org, required["run_id"]),
        ).fetchone()

        row = conn.execute(
            f"""
            INSERT INTO {RUNS_TABLE} (
                organization_id, run_id, model_name, model_version, task_type,
                product_sku, market, channel, horizon_days,
                input_fingerprint, input_summary, prediction,
                p10, p50, p90, confidence, confidence_score, missing_data, basis
            ) VALUES (?,?,?,?,?, ?,?,?,?, ?, ?::jsonb, ?::jsonb, ?,?,?, ?,?, ?::jsonb, ?::jsonb)
            ON CONFLICT (organization_id, run_id) DO UPDATE SET
                model_name = excluded.model_name,
                model_version = excluded.model_version,
                task_type = excluded.task_type,
                product_sku = COALESCE(excluded.product_sku, {RUNS_TABLE}.product_sku),
                market = COALESCE(excluded.market, {RUNS_TABLE}.market),
                channel = COALESCE(excluded.channel, {RUNS_TABLE}.channel),
                horizon_days = COALESCE(excluded.horizon_days, {RUNS_TABLE}.horizon_days),
                input_fingerprint = excluded.input_fingerprint,
                input_summary = excluded.input_summary,
                prediction = excluded.prediction,
                p10 = excluded.p10,
                p50 = excluded.p50,
                p90 = excluded.p90,
                confidence = excluded.confidence,
                confidence_score = excluded.confidence_score,
                missing_data = excluded.missing_data,
                basis = excluded.basis
            RETURNING id
            """,
            (
                org, required["run_id"], required["model_name"],
                required["model_version"], required["task_type"],
                sku, market, channel, horizon_days,
                fingerprint,
                _dumps(kw.get("input_summary")),
                _dumps(prediction),
                _float_or_none(kw.get("p10")),
                _float_or_none(kw.get("p50")),
                _float_or_none(kw.get("p90")),
                _text_or_none(kw.get("confidence"), 40) or DEFAULT_CONFIDENCE,
                _float_or_none(kw.get("confidence_score")),
                _dumps(kw.get("missing_data"), empty=[]),
                _dumps(kw.get("basis"), empty=[]),
            ),
        ).fetchone()
        conn.commit()
        new_id = _int_or_none(dict(row).get("id")) if row else None
        return {"ok": True, "id": new_id, "deduped": bool(existing)}
    except Exception:
        logger.warning("prediction_ledger.record_prediction_run failed run_id=%s",
                       required["run_id"], exc_info=True)
        return {"ok": False, "id": None, "deduped": False, "reason": "db_error"}


# ── 纯函数:单条误差指标 ─────────────────────────────────────────────


def compute_eval_metrics(
    p10: Any,
    p50: Any,
    p90: Any,
    actual: Any,
    prev_actual: Any = None,
) -> dict[str, Any]:
    """单条预测的误差指标(纯函数,None 全容错,绝不抛)。

    - error_abs:abs(actual - p50);任一缺席 → None。
    - error_pct:error_abs / abs(actual);actual 为 0 或缺席 → None(分母 0 安全)。
    - interval_hit:p10 <= actual <= p90;p10/p90/actual 任一缺席 → None。
    - direction_hit:prev_actual 给定时,预测涨跌方向(p50 vs prev)与实际涨跌
      方向(actual vs prev)同号(含双双持平)即命中;prev 缺席 → None。
    """
    lo = _float_or_none(p10)
    mid = _float_or_none(p50)
    hi = _float_or_none(p90)
    act = _float_or_none(actual)
    prev = _float_or_none(prev_actual)

    error_abs = round(abs(act - mid), 6) if act is not None and mid is not None else None
    error_pct = (round(error_abs / abs(act), 6)
                 if error_abs is not None and act is not None and act != 0 else None)
    interval_hit = (lo <= act <= hi) if lo is not None and hi is not None and act is not None else None
    direction_hit = (_sign(mid - prev) == _sign(act - prev)
                     if prev is not None and mid is not None and act is not None else None)
    return {
        "error_abs": error_abs,
        "error_pct": error_pct,
        "interval_hit": interval_hit,
        "direction_hit": direction_hit,
    }


# ── 评估落账:读回 run 分位 → 指标 → 手工 UPSERT ─────────────────────


def record_eval(
    run_id: str,
    actual_value: Any,
    outcome_id: int | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """结果回来后对某 run 落一条评估((org, run_id, outcome_id) 幂等)。

    先读回该 run 的 p10/p50/p90 → compute_eval_metrics(prev_actual 可经 kw 传入
    判方向)。outcome_id 可空;PG 唯一约束对 NULL 不触发冲突,故用先查后写的
    手工 UPSERT。run 不存在 → {ok: False, reason: 'run_not_found'};
    表未建 → {ok: False, reason: 'table_missing'}。
    """
    rid = _text_or_none(run_id, 200)
    if not rid:
        return {"ok": False, "id": None, "deduped": False,
                "reason": "missing_required_field", "missing": ["run_id"]}

    unknown = sorted(set(kw) - set(_EVAL_OPTIONAL_KEYS))
    if unknown:
        logger.debug("prediction_ledger.record_eval unknown kwargs ignored: %s", unknown)

    org = _text_or_none(kw.get("organization_id"), 80) or DEFAULT_ORG
    oid = _int_or_none(outcome_id)
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(RUNS_TABLE) or not table_exists(EVALS_TABLE):
            return {"ok": False, "id": None, "deduped": False, "reason": "table_missing"}

        conn = get_conn()
        run = conn.execute(
            f"SELECT p10, p50, p90 FROM {RUNS_TABLE} WHERE organization_id = ? AND run_id = ?",
            (org, rid),
        ).fetchone()
        if not run:
            return {"ok": False, "id": None, "deduped": False, "reason": "run_not_found"}
        run_row = dict(run)

        metrics = compute_eval_metrics(
            run_row.get("p10"), run_row.get("p50"), run_row.get("p90"),
            actual_value, kw.get("prev_actual"),
        )
        act = _float_or_none(actual_value)
        actual_json = _dumps(kw.get("actual_json"))
        bucket = _text_or_none(kw.get("calibrated_bucket"), 80)
        notes = _text_or_none(kw.get("notes"), 1000)

        if oid is None:
            existing = conn.execute(
                f"SELECT id FROM {EVALS_TABLE} WHERE organization_id = ? AND run_id = ? AND outcome_id IS NULL",
                (org, rid),
            ).fetchone()
        else:
            existing = conn.execute(
                f"SELECT id FROM {EVALS_TABLE} WHERE organization_id = ? AND run_id = ? AND outcome_id = ?",
                (org, rid, oid),
            ).fetchone()

        if existing:
            eval_id = _int_or_none(dict(existing).get("id"))
            conn.execute(
                f"""
                UPDATE {EVALS_TABLE} SET
                    actual_value = ?, actual_json = ?::jsonb,
                    error_abs = ?, error_pct = ?, interval_hit = ?, direction_hit = ?,
                    calibrated_bucket = ?, evaluated_at = NOW(), notes = ?
                WHERE id = ?
                """,
                (act, actual_json,
                 metrics["error_abs"], metrics["error_pct"],
                 metrics["interval_hit"], metrics["direction_hit"],
                 bucket, notes, eval_id),
            )
        else:
            row = conn.execute(
                f"""
                INSERT INTO {EVALS_TABLE} (
                    organization_id, run_id, outcome_id, actual_value, actual_json,
                    error_abs, error_pct, interval_hit, direction_hit,
                    calibrated_bucket, notes
                ) VALUES (?,?,?,?,?::jsonb, ?,?,?,?, ?,?)
                RETURNING id
                """,
                (org, rid, oid, act, actual_json,
                 metrics["error_abs"], metrics["error_pct"],
                 metrics["interval_hit"], metrics["direction_hit"],
                 bucket, notes),
            ).fetchone()
            eval_id = _int_or_none(dict(row).get("id")) if row else None
        conn.commit()
        return {"ok": True, "id": eval_id, "deduped": bool(existing), **metrics}
    except Exception:
        logger.warning("prediction_ledger.record_eval failed run_id=%s", rid, exc_info=True)
        return {"ok": False, "id": None, "deduped": False, "reason": "db_error"}


# ── 纯函数:周评估汇总 ───────────────────────────────────────────────


def weekly_rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """一批评估行(evals 形态)→ 周指标(纯函数,空列表 n=0 全 None)。

    - wape:sum(error_abs) / sum(abs(actual_value)),只算两者齐全的行;
      分母 0 或无合格行 → None(分母 0 安全)。
    - interval_coverage / direction_hit_rate:只算命中位非 None 的行
      (BOOLEAN 读回 int 1/0 宽容);无已知行 → None。
    - n 为总行数;各指标另带样本数(wape_n / interval_n / direction_n)诚实可查。
    """
    rows = rows or []
    n = len(rows)
    if n == 0:
        return {"n": 0, "wape": None, "interval_coverage": None, "direction_hit_rate": None,
                "wape_n": 0, "interval_n": 0, "direction_n": 0}

    err_sum = 0.0
    act_sum = 0.0
    wape_n = 0
    interval_known: list[bool] = []
    direction_known: list[bool] = []
    for row in rows:
        err = _float_or_none(row.get("error_abs"))
        act = _float_or_none(row.get("actual_value"))
        if err is not None and act is not None:
            err_sum += abs(err)
            act_sum += abs(act)
            wape_n += 1
        interval = _bool_or_none(row.get("interval_hit"))
        if interval is not None:
            interval_known.append(interval)
        direction = _bool_or_none(row.get("direction_hit"))
        if direction is not None:
            direction_known.append(direction)

    return {
        "n": n,
        "wape": round(err_sum / act_sum, 4) if wape_n > 0 and act_sum > 0 else None,
        "interval_coverage": (round(sum(interval_known) / len(interval_known), 4)
                              if interval_known else None),
        "direction_hit_rate": (round(sum(direction_known) / len(direction_known), 4)
                               if direction_known else None),
        "wape_n": wape_n,
        "interval_n": len(interval_known),
        "direction_n": len(direction_known),
    }


__all__ = [
    "record_prediction_run", "compute_eval_metrics", "record_eval", "weekly_rollup",
    "RUNS_TABLE", "EVALS_TABLE", "DEFAULT_ORG",
]
