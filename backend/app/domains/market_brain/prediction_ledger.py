"""W6 预测账本域层:vkpi_prediction_runs / vkpi_prediction_evals(迁移 220/221)的读写门面。

职责(执行路线第 2 刀 / 全景规格 5.2 节 B+C 表):
  - record_prediction_run:每次模型/规则/LLM 预测落账,(organization_id, run_id)
    只允许完全相同的幂等重放;首次预测落账后不可事后改写分位或输入。
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
import math
import re
from typing import Any

from app.core.logging import get_logger
from app.domains.market_brain import prediction_truth
from app.domains.market_brain.prediction_rollup_truth import (
    MIN_BINARY_CLAIMABLE_EVALS,
    MIN_MEASURED_CLAIMABLE_EVALS,
    binary_brier_rollup as _binary_brier_rollup,
    measured_nonbinary_rollup,
    rollup_forecast_log_truth as weekly_forecast_log_rollup,
    verified_nonbinary_rollup,
)
from app.domains.platform import review_contract

logger = get_logger(__name__)

RUNS_TABLE = "vkpi_prediction_runs"
EVALS_TABLE = "vkpi_prediction_evals"

# 商业化前多租户安全字段占位(迁移 220/221 列默认一致,单租户先缺省)。
DEFAULT_ORG = "viltrox"

# 置信度档位(DDL 口径 low / medium / high);未申报按最保守档,绝不虚标。
DEFAULT_CONFIDENCE = "low"
MIN_CLAIMABLE_EVALS = 5

# source_step 口径(FVA 用):产生该预测的步骤;'baseline' 与 'model' 两版可对账。
_SOURCE_STEPS = ("rule", "model", "human_override", "baseline")

# record_prediction_run 认识的可选列(未知 kw 只 debug 留痕,绝不静默进库)。
_RUN_OPTIONAL_KEYS = (
    "organization_id", "sku", "product_sku", "market", "channel", "horizon_days",
    "input_fingerprint", "input_summary", "p10", "p50", "p90",
    "confidence", "confidence_score", "missing_data", "basis",
    "baseline_value", "source_step",
    # Internal-only truth plumbing for registered GTM predictions.  The same
    # connection/database timestamp is used for immutable run.created_at and
    # evaluation_contract.observation_start_at.
    "_connection", "_created_at",
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
        if isinstance(value, bool) or value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _verified_outcome_actual_binding(
    value: Any,
    *,
    outcome_row: dict[str, Any],
    outcome_id: int,
    actual_value: float,
) -> dict[str, Any] | None:
    """Verify that an eval's numeric actual comes from its linked outcome.

    The generic outcome ledger can hold different metrics, so callers must name
    the exact JSON field and metric path used.  This prevents an arbitrary
    number from becoming a claimable eval merely because it points at some
    finalized outcome.
    """
    binding = _json_object(value)
    if _int_or_none(binding.get("outcome_id")) != outcome_id:
        return None
    evidence_field = _text_or_none(binding.get("evidence_field"), 40)
    if evidence_field not in {"actual_result", "window_7d", "window_14d", "window_28d"}:
        return None
    metric_path = _text_or_none(binding.get("metric_path"), 200)
    if not metric_path:
        return None
    node: Any = _json_object(outcome_row.get(evidence_field))
    for segment in metric_path.split("."):
        if not segment or not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    observed_value = _float_or_none(node)
    declared_value = _float_or_none(binding.get("value"))
    if observed_value is None or declared_value is None:
        return None
    if observed_value != actual_value or declared_value != actual_value:
        return None
    return {
        **binding,
        "outcome_id": outcome_id,
        "evidence_field": evidence_field,
        "metric_path": metric_path,
        "value": actual_value,
        "binding_status": "verified_against_outcome",
    }


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


# ── 预测落账:append-only + identical replay ─────────────────────────


def record_prediction_run(
    run_id: str,
    model_name: str,
    model_version: str,
    task_type: str,
    prediction: dict[str, Any],
    **kw: Any,
) -> dict[str, Any]:
    """预测运行落账:(organization_id, run_id) 只允许完全相同的幂等重放。

    重跑语义:同 run_id 的模型、输入、预测、分位、证据和版本必须逐项一致；
    任一字段漂移均返回 ``prediction_run_conflict``，绝不改写首次预测。
    返回 {ok, id|None, deduped: bool};
    表未建 → {ok: False, reason: 'table_missing'}。

    FVA 两字段(迁移 223,可选 kw):
      - baseline_value:naive / seasonal-naive 基线预测值(与本预测同口径,做 FVA 分母参照)。
      - source_step:产生该预测的步骤(rule / model / human_override / baseline);
        weekly_rollup 的 fva 段据此把同 (sku, market, channel) 的 baseline 与 model
        两版预测对齐算误差增量。缺省 None,COALESCE 保底(重跑不给不清空)。
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
    conn = None
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(RUNS_TABLE):
            return {"ok": False, "id": None, "deduped": False, "reason": "table_missing"}

        conn = kw.get("_connection") or get_conn()
        expected = prediction_truth.canonical_prediction_payload(
            model_name=str(required["model_name"]),
            model_version=str(required["model_version"]),
            task_type=str(required["task_type"]),
            product_sku=sku,
            market=market,
            channel=channel,
            horizon_days=horizon_days,
            input_fingerprint=str(fingerprint),
            input_summary=kw.get("input_summary"),
            prediction=prediction,
            p10=kw.get("p10"),
            p50=kw.get("p50"),
            p90=kw.get("p90"),
            confidence=_text_or_none(kw.get("confidence"), 40) or DEFAULT_CONFIDENCE,
            confidence_score=kw.get("confidence_score"),
            missing_data=kw.get("missing_data") if kw.get("missing_data") is not None else [],
            basis=kw.get("basis") if kw.get("basis") is not None else [],
            baseline_value=kw.get("baseline_value"),
            source_step=kw.get("source_step"),
        )
        evaluable_error = prediction_truth.evaluable_prediction_error(expected)
        if evaluable_error is not None:
            return {
                "ok": False, "id": None, "deduped": False,
                "reason": evaluable_error,
            }
        contract = prediction_truth.parse_evaluation_contract(expected)
        created_at_value: str | None = None
        if contract is not None:
            if kw.get("_connection") is None:
                return {
                    "ok": False, "id": None, "deduped": False,
                    "reason": "evaluable_prediction_server_connection_required",
                }
            created = prediction_truth.parse_iso_datetime(kw.get("_created_at"))
            observation_start = prediction_truth.parse_iso_datetime(
                contract.get("observation_start_at")
            )
            if created is None or observation_start is None or created != observation_start:
                return {
                    "ok": False, "id": None, "deduped": False,
                    "reason": "evaluable_prediction_server_clock_mismatch",
                }
            created_at_value = created.isoformat()
        created_column = ", created_at" if created_at_value is not None else ""
        created_placeholder = ", ?" if created_at_value is not None else ""
        insert_params: tuple[Any, ...] = (
            org, required["run_id"], required["model_name"],
            required["model_version"], required["task_type"],
            sku, market, channel, horizon_days,
            fingerprint,
            _dumps(expected["input_summary"]),
            _dumps(expected["prediction"]),
            expected["p10"], expected["p50"], expected["p90"],
            expected["confidence"], expected["confidence_score"],
            _dumps(expected["missing_data"], empty=[]),
            _dumps(expected["basis"], empty=[]),
            expected["baseline_value"], expected["source_step"],
        ) + ((created_at_value,) if created_at_value is not None else ())
        row = conn.execute(
            f"""
            INSERT INTO {RUNS_TABLE} (
                organization_id, run_id, model_name, model_version, task_type,
                product_sku, market, channel, horizon_days,
                input_fingerprint, input_summary, prediction,
                p10, p50, p90, confidence, confidence_score, missing_data, basis,
                baseline_value, source_step{created_column}
            ) VALUES (?,?,?,?,?, ?,?,?,?, ?, ?::jsonb, ?::jsonb, ?,?,?, ?,?, ?::jsonb, ?::jsonb, ?, ?{created_placeholder})
            ON CONFLICT (organization_id, run_id) DO NOTHING
            RETURNING id
            """,
            insert_params,
        ).fetchone()
        if row is None:
            existing = conn.execute(
                f"""
                SELECT id, model_name, model_version, task_type, product_sku, market,
                       channel, horizon_days, input_fingerprint, input_summary, prediction,
                       p10, p50, p90, confidence, confidence_score, missing_data, basis,
                       baseline_value, source_step
                FROM {RUNS_TABLE}
                WHERE organization_id = ? AND run_id = ?
                """,
                (org, required["run_id"]),
            ).fetchone()
            if existing is None:
                conn.rollback()
                return {
                    "ok": False, "id": None, "deduped": False,
                    "reason": "prediction_run_conflict",
                }
            existing_row = dict(existing)
            existing_id = _int_or_none(existing_row.get("id"))
            if not prediction_truth.prediction_payload_matches(existing_row, expected):
                conn.rollback()
                return {
                    "ok": False, "id": existing_id, "deduped": False,
                    "reason": "prediction_run_conflict",
                }
            conn.commit()
            return {"ok": True, "id": existing_id, "deduped": True}
        conn.commit()
        new_id = _int_or_none(dict(row).get("id")) if row else None
        return {"ok": True, "id": new_id, "deduped": False}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("prediction_ledger.record_prediction_run rollback failed", exc_info=True)
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
    """Record a legacy descriptive eval that is not bound to an outcome.

    Outcome-bound actuals are intentionally rejected here.  They must use
    :func:`record_eval_from_finalized_outcome`, which resolves the value from a
    closed server-side evidence window and atomically writes its verification
    event.  This keeps generic internal callers from manufacturing claimable
    learning evidence.
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
    if oid is not None:
        return {
            "ok": False,
            "id": None,
            "deduped": False,
            "reason": "verified_actual_writer_required",
        }
    act = _float_or_none(actual_value)
    if act is None:
        return {
            "ok": False,
            "id": None,
            "deduped": False,
            "reason": "missing_required_field",
            "missing": ["actual_value"],
        }
    conn = None
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
        actual_json = _dumps(kw.get("actual_json"))
        bucket = _text_or_none(kw.get("calibrated_bucket"), 80)
        notes = _text_or_none(kw.get("notes"), 1000)

        existing = conn.execute(
            f"SELECT id FROM {EVALS_TABLE} "
            "WHERE organization_id = ? AND run_id = ? AND outcome_id IS NULL",
            (org, rid),
        ).fetchone()

        if existing:
            eval_id = _int_or_none(dict(existing).get("id"))
            # The weekly rollup re-records forecast-log actuals without an
            # actual_json; keep the stored payload (e.g. the measured-from-
            # snapshots binding written by prediction_rollup_truth) instead of
            # blanking it to "{}".
            keep_payload = kw.get("actual_json") is None
            conn.execute(
                f"""
                UPDATE {EVALS_TABLE} SET
                    actual_value = ?,
                    actual_json = CASE WHEN ? THEN actual_json ELSE CAST(? AS jsonb) END,
                    error_abs = ?, error_pct = ?, interval_hit = ?, direction_hit = ?,
                    calibrated_bucket = ?, evaluated_at = NOW(), notes = ?
                WHERE id = ?
                """,
                (act, keep_payload, actual_json,
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
                (org, rid, None, act, actual_json,
                 metrics["error_abs"], metrics["error_pct"],
                 metrics["interval_hit"], metrics["direction_hit"],
                 bucket, notes),
            ).fetchone()
            eval_id = _int_or_none(dict(row).get("id")) if row else None
        conn.commit()
        return {"ok": True, "id": eval_id, "deduped": bool(existing), **metrics}
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            logger.debug("prediction eval rollback failed", exc_info=True)
        logger.warning("prediction_ledger.record_eval failed run_id=%s", rid, exc_info=True)
        return {"ok": False, "id": None, "deduped": False, "reason": "db_error"}


def record_eval_from_finalized_outcome(
    run_id: str,
    *,
    staff: dict[str, Any] | None,
    outcome_id: int,
    evidence_field: str,
    metric_path: str,
    correlation_id: str,
    organization_id: str = DEFAULT_ORG,
    notes: str | None = None,
) -> dict[str, Any]:
    """Resolve an actual from server-side outcome evidence, then record eval.

    The caller never supplies ``actual_value``.  The numeric truth is read from
    one finalized GTM outcome and is re-verified by :func:`record_eval` before
    the eval is committed.
    """
    rid = _text_or_none(run_id, 200)
    oid = _int_or_none(outcome_id)
    field = _text_or_none(evidence_field, 40)
    path = _text_or_none(metric_path, 200)
    org = _text_or_none(organization_id, 80) or DEFAULT_ORG
    reviewer = review_contract.reviewer_context(staff)
    correlation = review_contract.normalize_correlation(correlation_id)
    normalized_notes = (
        review_contract.normalize_review_text(notes, max_length=1000)
        if notes is not None else None
    )
    if not rid or oid is None or oid <= 0 or field not in {
        "actual_result", "window_7d", "window_14d", "window_28d",
    } or not path or re.fullmatch(r"[A-Za-z0-9_.-]+", path) is None:
        return {"ok": False, "id": None, "deduped": False, "reason": "invalid_actual_binding"}
    if reviewer is None or org != DEFAULT_ORG:
        return {"ok": False, "id": None, "deduped": False, "reason": "actual_scope_unavailable"}
    if correlation is None:
        return {"ok": False, "id": None, "deduped": False, "reason": "actual_correlation_required"}
    if notes is not None and normalized_notes is None:
        return {"ok": False, "id": None, "deduped": False, "reason": "actual_notes_invalid"}
    actor_id, _organization_id = reviewer
    conn = None
    try:
        from app.db.connection import get_conn, table_exists

        if (
            not table_exists("vkpi_gtm_outcomes")
            or not table_exists(RUNS_TABLE)
            or not table_exists(EVALS_TABLE)
            or not table_exists("vkpi_event_ledger")
        ):
            return {"ok": False, "id": None, "deduped": False, "reason": "outcome_table_missing"}
        conn = get_conn()
        row = conn.execute(
            """
            SELECT decision, decided_at, decided_by, action_type, action_inbox_id,
                   product_sku, market, channel,
                   actual_result, window_7d, window_14d, window_28d
            FROM vkpi_gtm_outcomes
            WHERE id = ?
            """,
            (oid,),
        ).fetchone()
        if row is None:
            return {"ok": False, "id": None, "deduped": False, "reason": "outcome_not_found"}
        outcome = dict(row)
        if (
            _text_or_none(outcome.get("decision"), 20) in (None, "open")
            or outcome.get("decided_at") is None
            or (_int_or_none(outcome.get("decided_by")) or 0) <= 0
        ):
            return {"ok": False, "id": None, "deduped": False, "reason": "outcome_not_finalized"}
        from app.domains.market_brain.data_readiness import (
            has_observed_outcome_evidence,
            has_verified_outcome_evidence,
        )

        selected_evidence = {
            "actual_result": {}, "window_7d": {}, "window_14d": {}, "window_28d": {},
            str(field): outcome.get(str(field)),
        }
        if not has_observed_outcome_evidence(selected_evidence):
            return {
                "ok": False, "id": None, "deduped": False,
                "reason": "outcome_missing_observed_evidence",
            }
        if not has_verified_outcome_evidence(
            conn,
            {**outcome, "id": oid},
            evidence_field=str(field),
        ):
            return {
                "ok": False, "id": None, "deduped": False,
                "reason": "outcome_missing_observed_evidence",
            }
        run_row = conn.execute(
            f"""
            SELECT model_name, model_version, task_type, product_sku, market, channel,
                   horizon_days, input_fingerprint, input_summary, prediction,
                   p10, p50, p90, created_at,
                   (created_at < ?) AS chronology_valid
            FROM {RUNS_TABLE}
            WHERE organization_id = ? AND run_id = ?
            """,
            (outcome.get("decided_at"), org, rid),
        ).fetchone()
        if run_row is None:
            return {"ok": False, "id": None, "deduped": False, "reason": "run_not_found"}
        run = dict(run_row)
        contract = prediction_truth.parse_evaluation_contract(run)
        if contract is None:
            return {
                "ok": False, "id": None, "deduped": False,
                "reason": "prediction_evaluation_contract_missing",
            }
        if int(contract["target_action_inbox_id"]) != (_int_or_none(outcome.get("action_inbox_id")) or 0):
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_outcome_mismatch"}
        if str(contract["task_type"]) != str(run.get("task_type") or ""):
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_task_mismatch"}
        if str(contract["outcome_action_type"]) != str(outcome.get("action_type") or ""):
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_action_mismatch"}
        if (
            str(contract["evidence_field"]) != str(field)
            or str(contract["metric_path"]) != str(path)
            or str(contract["metric_key"]) != str(path).split(".")[-1]
        ):
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_metric_contract_mismatch"}
        for dimension in ("product_sku", "market", "channel"):
            run_value = _text_or_none(run.get(dimension), 120)
            outcome_value = _text_or_none(outcome.get(dimension), 120)
            if not run_value or not outcome_value or run_value.casefold() != outcome_value.casefold():
                return {
                    "ok": False, "id": None, "deduped": False,
                    "reason": f"actual_{dimension}_mismatch",
                }
        expected_horizon = {"window_7d": 7, "window_14d": 14, "window_28d": 28}.get(str(field))
        run_horizon = _int_or_none(run.get("horizon_days"))
        if run_horizon is None or run_horizon <= 0 or (
            expected_horizon is not None and run_horizon != expected_horizon
        ) or run_horizon != int(contract["horizon_days"]):
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_horizon_mismatch"}
        if _bool_or_none(run.get("chronology_valid")) is not True:
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_chronology_invalid"}
        if prediction_truth.parse_iso_datetime(contract.get("observation_start_at")) != (
            prediction_truth.parse_iso_datetime(run.get("created_at"))
        ):
            return {
                "ok": False, "id": None, "deduped": False,
                "reason": "actual_observation_anchor_invalid",
            }
        if not prediction_truth.outcome_evidence_is_closed(
            outcome.get(str(field)),
            evidence_field=str(field),
            horizon_days=run_horizon,
            run_created_at=run.get("created_at"),
            outcome_decided_at=outcome.get("decided_at"),
            observation_start_at=contract.get("observation_start_at"),
        ):
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_window_not_closed"}
        node: Any = _json_object(outcome.get(field))
        for segment in path.split("."):
            if not segment or not isinstance(node, dict) or segment not in node:
                return {
                    "ok": False, "id": None, "deduped": False,
                    "reason": "actual_metric_not_found",
                }
            node = node[segment]
        actual = _float_or_none(node)
        if actual is None:
            return {
                "ok": False, "id": None, "deduped": False,
                "reason": "actual_metric_not_numeric",
            }
        run_snapshot = {
            "run_id": rid,
            "model_name": str(run.get("model_name") or ""),
            "model_version": str(run.get("model_version") or ""),
            "task_type": str(run.get("task_type") or ""),
            "input_fingerprint": str(run.get("input_fingerprint") or ""),
            "product_sku": run.get("product_sku"),
            "market": run.get("market"),
            "channel": run.get("channel"),
            "horizon_days": run_horizon,
            "p10": _float_or_none(run.get("p10")),
            "p50": _float_or_none(run.get("p50")),
            "p90": _float_or_none(run.get("p90")),
            "created_at": str(run.get("created_at") or ""),
            "prediction": prediction_truth.json_value(run.get("prediction"), empty={}),
        }
        run_snapshot["sha256"] = hashlib.sha256(
            json.dumps(
                run_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")
        ).hexdigest()
        binding = _verified_outcome_actual_binding(
            {
                "outcome_id": oid,
                "evidence_field": field,
                "metric_path": path,
                "metric_key": contract["metric_key"],
                "unit": contract["unit"],
                "task_type": contract["task_type"],
                "evaluation_contract_schema": contract["schema"],
                "evaluation_registry_key": contract["registry_key"],
                "observation_start_at": contract["observation_start_at"],
                "value": actual,
                "source": "server_resolved_finalized_outcome",
                "reviewed_by_staff_id": actor_id,
                "outcome_decided_by_staff_id": int(outcome["decided_by"]),
                "correlation_id": correlation,
                "run_snapshot_sha256": run_snapshot["sha256"],
            },
            outcome_row=outcome,
            outcome_id=oid,
            actual_value=actual,
        )
        if binding is None:
            return {"ok": False, "id": None, "deduped": False, "reason": "actual_evidence_binding_required"}
        binding["outcome_evidence_sha256"] = hashlib.sha256(
            json.dumps(
                prediction_truth.json_value(outcome.get(str(field)), empty={}),
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")
        ).hexdigest()
        binding["binding_sha256"] = hashlib.sha256(
            json.dumps(
                binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")
        ).hexdigest()
        from app.domains.market_brain import prediction_reviews

        metrics = compute_eval_metrics(
            run.get("p10"), run.get("p50"), run.get("p90"), actual, None,
        )
        return prediction_reviews.record_verified_eval(
            conn,
            organization_id=org,
            run_id=rid,
            outcome_id=oid,
            actual_value=actual,
            actual_json=binding,
            metrics=metrics,
            notes=normalized_notes,
            actor_id=actor_id,
            correlation_id=correlation,
            run_snapshot=run_snapshot,
        )
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            logger.debug("prediction actual verification rollback failed", exc_info=True)
        logger.warning(
            "prediction_ledger.record_eval_from_finalized_outcome failed run_id=%s outcome_id=%s",
            rid,
            oid,
            exc_info=True,
        )
        return {"ok": False, "id": None, "deduped": False, "reason": "db_error"}


# ── 纯函数:WAPE 与 FVA(预测相对基线的增益) ────────────────────────


def _wape_of(rows: list[dict[str, Any]]) -> tuple[float | None, int]:
    """一批行的 WAPE = sum(error_abs) / sum(abs(actual_value));返回 (wape, 合格样本数)。

    只算 error_abs 与 actual_value 双双齐全的行;分母 0 或无合格行 → wape None(分母 0 安全)。
    """
    err_sum = 0.0
    act_sum = 0.0
    n = 0
    for row in rows:
        err = _float_or_none(row.get("error_abs"))
        act = _float_or_none(row.get("actual_value"))
        if err is not None and act is not None:
            err_sum += abs(err)
            act_sum += abs(act)
            n += 1
    wape = round(err_sum / act_sum, 6) if n > 0 and act_sum > 0 else None
    return wape, n


def _fva_rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """FVA(Forecast Value Add)段:同 (sku, market, channel) 有 baseline 与 model
    两版预测的,算 model 相对 baseline 的 WAPE 增量(delta = model_wape - baseline_wape,
    负 = 模型更好)。

    只认 source_step ∈ {'baseline', 'model'} 的行;缺 source_step / 只有单版的组不出
    对照(诚实态,不虚构增益)。返回 {n_groups, mean_delta, model_better_share, groups};
    无合格组 → n_groups 0、mean_delta/model_better_share None、groups []。
    """
    grouped: dict[tuple[Any, Any, Any], dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        step = _text_or_none(row.get("source_step"), 40)
        if step not in ("baseline", "model"):
            continue
        key = (
            _text_or_none(row.get("product_sku") or row.get("sku"), 120),
            _text_or_none(row.get("market"), 40),
            _text_or_none(row.get("channel"), 60),
        )
        bucket = grouped.setdefault(key, {"baseline": [], "model": []})
        bucket[step].append(row)

    groups: list[dict[str, Any]] = []
    deltas: list[float] = []
    better = 0
    for key, bucket in grouped.items():
        baseline_wape, baseline_n = _wape_of(bucket["baseline"])
        model_wape, model_n = _wape_of(bucket["model"])
        if baseline_wape is None or model_wape is None:
            continue
        delta = round(model_wape - baseline_wape, 6)
        deltas.append(delta)
        if delta < 0:
            better += 1
        groups.append({
            "sku": key[0], "market": key[1], "channel": key[2],
            "baseline_wape": baseline_wape, "model_wape": model_wape,
            "delta": delta, "baseline_n": baseline_n, "model_n": model_n,
        })
    groups.sort(key=lambda g: g["delta"])  # 最优(delta 最负)在前
    n_groups = len(groups)
    return {
        "n_groups": n_groups,
        "mean_delta": round(sum(deltas) / n_groups, 6) if n_groups else None,
        "model_better_share": round(better / n_groups, 4) if n_groups else None,
        "groups": groups,
    }


# ── 纯函数:周评估汇总 ───────────────────────────────────────────────


def _rollup_readiness(sample_size: int) -> dict[str, Any]:
    from app.domains.market_brain.data_readiness import DataRequirement, evaluate_requirements

    return evaluate_requirements([
        DataRequirement(
            key="prediction_evals",
            label="prediction evaluations with actual values",
            observed=sample_size,
            minimum=MIN_CLAIMABLE_EVALS,
        )
    ]).to_dict()


def weekly_rollup(
    rows: list[dict[str, Any]],
    *,
    outreach_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一批评估行(evals 形态)→ 周指标(纯函数,空列表 n=0 全 None)。

    - wape:sum(error_abs) / sum(abs(actual_value)),只算两者齐全的行;
      分母 0 或无合格行 → None(分母 0 安全)。
    - interval_coverage / direction_hit_rate:只算命中位非 None 的行
      (BOOLEAN 读回 int 1/0 宽容);无已知行 → None。
    - fva:同 (sku, market, channel) 有 baseline 与 model 两版预测的误差增量对照
      (需行带 source_step + sku/market/channel;缺则该段 n_groups=0 诚实空)。
    - n 为总行数;各指标另带样本数(wape_n / interval_n / direction_n)诚实可查。
    """
    rows = rows or []
    n = len(rows)
    brier = _binary_brier_rollup(rows, outreach_coverage=outreach_coverage)
    verified = verified_nonbinary_rollup(rows, minimum=MIN_CLAIMABLE_EVALS)
    # Snapshot-measured KOL view forecasts (forecast_log rollup): instrument
    # truth with its own 20-sample floor; never mixed into the verified tier.
    measured = measured_nonbinary_rollup(rows, minimum=MIN_MEASURED_CLAIMABLE_EVALS)
    if n == 0:
        readiness = _rollup_readiness(0)
        return {"n": 0, "wape": None, "interval_coverage": None, "direction_hit_rate": None,
                "wape_n": 0, "interval_n": 0, "direction_n": 0, "fva": _fva_rollup([]),
                "binary_probability": brier, "brier_score": None, "brier_n": 0,
                "verified_nonbinary": verified, "measured_nonbinary": measured,
                "claimable": False, "data_readiness": readiness,
                "claimable_metrics": {"wape": None, "interval_coverage": None,
                                      "direction_hit_rate": None, "brier_score": None}}

    err_sum = 0.0
    act_sum = 0.0
    wape_n = 0
    interval_known: list[bool] = []
    direction_known: list[bool] = []
    for row in rows:
        if str(row.get("task_type") or "") == "kol_outreach_reply_probability":
            # WAPE is undefined/misleading for Bernoulli probabilities.  Those
            # rows are evaluated only by the task-aware Brier path above.
            continue
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

    result = {
        "n": n,
        "wape": round(err_sum / act_sum, 4) if wape_n > 0 and act_sum > 0 else None,
        "interval_coverage": (round(sum(interval_known) / len(interval_known), 4)
                              if interval_known else None),
        "direction_hit_rate": (round(sum(direction_known) / len(direction_known), 4)
                               if direction_known else None),
        "wape_n": wape_n,
        "interval_n": len(interval_known),
        "direction_n": len(direction_known),
        "fva": _fva_rollup([
            row for row in rows
            if str(row.get("task_type") or "") != "kol_outreach_reply_probability"
        ]),
        "binary_probability": brier,
        "brier_score": brier["brier_score"],
        "brier_n": brier["n"],
    }
    readiness = _rollup_readiness(verified["n"])
    result["claimable"] = bool(readiness["claimable"] or brier["claimable"])
    result["data_readiness"] = readiness
    result["verified_nonbinary"] = verified
    result["measured_nonbinary"] = measured
    result["claimable_metrics"] = {
        "wape": verified["wape"] if readiness["claimable"] else None,
        "interval_coverage": (
            verified["interval_coverage"]
            if readiness["claimable"] and verified["interval_n"] >= MIN_CLAIMABLE_EVALS
            else None
        ),
        "direction_hit_rate": (
            verified["direction_hit_rate"]
            if readiness["claimable"] and verified["direction_n"] >= MIN_CLAIMABLE_EVALS
            else None
        ),
        "brier_score": brier["brier_score"] if brier["claimable"] else None,
    }
    return result


__all__ = [
    "record_prediction_run", "compute_eval_metrics", "record_eval", "weekly_rollup",
    "weekly_forecast_log_rollup",
    "RUNS_TABLE", "EVALS_TABLE", "DEFAULT_ORG", "MIN_CLAIMABLE_EVALS",
    "MIN_BINARY_CLAIMABLE_EVALS", "MIN_MEASURED_CLAIMABLE_EVALS",
]
