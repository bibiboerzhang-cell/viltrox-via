"""Skill【roi_review_v1】—— ROI 复盘 thin wrapper(封装现有 ROI/归因/outcome 服务,不重写算法)。

形式化规范(对齐 skill_registry / evals 既有约定):
  - INPUT_SCHEMA / OUTPUT_SCHEMA 用 dict 描述字段;
  - run(input, *, model_fn=None, record=True):
      ① 校验/取 input(project_id 或 kol_pool_id 二选一,window 可选)
      ② 调【现有服务】产出 ROI:
            project_id   → metrics.aggregation.aggregate_project_metrics(只读 cost/revenue 聚合)
            kol_pool_id  → kol.roi_aggregate.get_kol_roi_summary(只读 KOL 维度 ROI)
         outcome_labels 据 ROI status + KOL 推荐漏斗权重派生(复用 compute_next_recommendation_weight);
         next_action / confidence 默认走规则;若注入 model_fn 则交给它产出(默认 None=不真烧 LLM)。
      ③ 形状化成 OUTPUT_SCHEMA;无真数据诚实返回 missing_data 标记(绝不臆造 0 ROI)。
      ④ record=True 时 best-effort 调 skill_registry.record_skill_run 落一行。
      ⑤ return。

红线:本 skill 纯只读复盘 + 运行账本,绝不触 viltrox_fit_score(读它做展示可以,这里压根不读 fit)。
LLM/外网被墙:model_fn 默认 None → 全程走规则,零真烧;注入时由调用方负责代理。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

SKILL_NAME = "roi_review"
SKILL_VERSION = "v1"

# model_fn 签名:(prompt_context: dict) -> dict(可含 next_action / confidence / labels)
ModelFn = Callable[[dict[str, Any]], dict[str, Any]]

INPUT_SCHEMA: dict[str, Any] = {
    "project_id": "int? — 复盘单个项目的 ROI(与 kol_pool_id 二选一)",
    "kol_pool_id": "int? — 复盘单个 KOL 的 ROI(与 project_id 二选一)",
    "window": "int? — 数据窗口天数,默认 30(仅项目维度生效)",
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "scope": "str — 'project' | 'kol'",
    "subject_id": "int — 被复盘对象的 id",
    "roi": {
        "spend_cents": "int|None — 花费(cost)分;无真数据时 None",
        "attributed_gmv_cents": "int|None — 归因 GMV(revenue)分;无真数据时 None",
        "roi_ratio": "float|None — (gmv-spend)/spend;无 spend/gmv 时 None(绝不假 0)",
    },
    "outcome_labels": "list[str] — 派生标签,如 ['has_revenue'] / ['missing_data']",
    "next_action": "str — 建议下一步(规则或注入 model_fn 产出)",
    "confidence": "float — 0..1 置信度",
    "missing_data": "bool — True 表示无真实商业数据,字段诚实留空未臆造",
    "status": "str — 'ready' | 'missing_data' | 'not_found' | 'invalid_input'",
}


def _clean_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _roi_block(spend: int | None, gmv: int | None, ratio: Any) -> dict[str, Any]:
    """形状化 ROI 三元组。ratio 优先用上游算好的;否则在 spend>0 且 gmv 已知时自算。"""
    spend_c = _clean_int(spend)
    gmv_c = _clean_int(gmv)
    roi_ratio: float | None
    if isinstance(ratio, (int, float)):
        roi_ratio = round(float(ratio), 4)
    elif isinstance(gmv_c, int) and isinstance(spend_c, int) and spend_c > 0:
        roi_ratio = round((gmv_c - spend_c) / spend_c, 4)
    else:
        roi_ratio = None
    return {"spend_cents": spend_c, "attributed_gmv_cents": gmv_c, "roi_ratio": roi_ratio}


def _kol_weight(kol_pool_id: int) -> float | None:
    """复用 roi_aggregate 的推荐漏斗权重做 KOL 维度信号(独立展示信号,绝不并入 fit)。"""
    try:
        from app.domains.kol import roi_aggregate

        return roi_aggregate.compute_next_recommendation_weight(int(kol_pool_id))
    except Exception:
        logger.debug("roi_review.kol_weight_failed", exc_info=True)
        return None


def _derive_labels(*, has_revenue: bool, has_spend: bool, status: str, weight: float | None) -> list[str]:
    labels: list[str] = []
    if status in ("missing_data", "awaiting_m5", "no_projects"):
        labels.append("missing_data")
    if has_revenue:
        labels.append("has_revenue")
    if has_spend:
        labels.append("has_spend")
    if has_revenue and has_spend:
        labels.append("roi_computable")
    if weight is not None:
        labels.append("high_funnel_traction" if weight >= 0.5 else "low_funnel_traction")
    return labels or ["missing_data"]


def _rule_next_action(*, roi_ratio: float | None, missing: bool, weight: float | None) -> tuple[str, float]:
    """规则版 next_action + confidence(零 LLM)。"""
    if missing or roi_ratio is None:
        # 无真实商业数据:诚实建议补数据,低置信。
        return ("collect_attribution_data", 0.3)
    if roi_ratio >= 1.0:
        return ("scale_up_investment", 0.8)
    if roi_ratio >= 0.0:
        return ("maintain_and_monitor", 0.6)
    # 负 ROI。
    if weight is not None and weight >= 0.5:
        return ("optimize_creative_keep_relationship", 0.6)
    return ("pause_and_review", 0.7)


def run(input: dict[str, Any], *, model_fn: Optional[ModelFn] = None, record: bool = True) -> dict[str, Any]:
    """执行 roi_review_v1。返回符合 OUTPUT_SCHEMA 的 dict。best-effort 落账。"""
    started = time.monotonic()
    data = dict(input or {})
    project_id = _clean_int(data.get("project_id"))
    kol_pool_id = _clean_int(data.get("kol_pool_id"))
    # 非正 id 视为缺省(0/负数都不是合法主键)→ 落入 invalid_input,绝不打非法 id 进聚合。
    if not isinstance(project_id, int) or project_id <= 0:
        project_id = None
    if not isinstance(kol_pool_id, int) or kol_pool_id <= 0:
        kol_pool_id = None
    window = _clean_int(data.get("window")) or 30

    if not project_id and not kol_pool_id:
        out = {
            "scope": "none", "subject_id": 0,
            "roi": _roi_block(None, None, None),
            "outcome_labels": ["invalid_input"], "next_action": "provide_project_or_kol",
            "confidence": 0.0, "missing_data": True, "status": "invalid_input",
        }
        _maybe_record(out, data, model_fn, started, record)
        return out

    # ── ② 调现有服务产出 ROI ────────────────────────────────────────────────
    weight: float | None = None
    if project_id:
        scope = "project"
        subject_id = project_id
        try:
            from app.domains.metrics import aggregation

            agg = aggregation.aggregate_project_metrics(project_id, window_days=window)
        except Exception:
            logger.debug("roi_review.project_agg_failed", exc_info=True)
            agg = {"status": "missing_data"}
        status_raw = str(agg.get("status") or "")
        spend = agg.get("cost_cents")
        gmv = agg.get("revenue_cents")
        ratio = agg.get("roi")
        not_found = status_raw == "not_found"
    else:
        scope = "kol"
        subject_id = int(kol_pool_id)
        try:
            from app.domains.kol import roi_aggregate

            agg = roi_aggregate.get_kol_roi_summary(kol_pool_id)
        except Exception:
            logger.debug("roi_review.kol_agg_failed", exc_info=True)
            agg = {"status": "missing_data"}
        status_raw = str(agg.get("status") or "")
        spend = agg.get("cost_cents")
        gmv = agg.get("revenue_cents")
        ratio = agg.get("roi")
        not_found = status_raw == "not_found"
        weight = _kol_weight(kol_pool_id)

    if not_found:
        out = {
            "scope": scope, "subject_id": subject_id,
            "roi": _roi_block(None, None, None),
            "outcome_labels": ["not_found"], "next_action": "verify_subject_exists",
            "confidence": 0.0, "missing_data": True, "status": "not_found",
        }
        _maybe_record(out, data, model_fn, started, record)
        return out

    # ── ③ 形状化 + missing_data 诚实判定 ──────────────────────────────────────
    roi_block = _roi_block(spend, gmv, ratio)
    has_revenue = isinstance(roi_block["attributed_gmv_cents"], int) and roi_block["attributed_gmv_cents"] > 0
    has_spend = isinstance(roi_block["spend_cents"], int) and roi_block["spend_cents"] > 0
    missing = not has_revenue  # 无 revenue = 无真实商业数据(对齐上游 awaiting_m5 口径)
    status = "ready" if not missing else "missing_data"

    labels = _derive_labels(has_revenue=has_revenue, has_spend=has_spend, status=status, weight=weight)
    next_action, confidence = _rule_next_action(roi_ratio=roi_block["roi_ratio"], missing=missing, weight=weight)

    # 可注入 model_fn:默认 None=不真烧 LLM,走上面的规则;注入时由其覆盖 next_action/confidence/labels。
    model_used: str | None = None
    if model_fn is not None:
        try:
            suggestion = model_fn({
                "scope": scope, "subject_id": subject_id, "roi": roi_block,
                "outcome_labels": labels, "weight": weight, "missing_data": missing,
            }) or {}
            if suggestion.get("next_action"):
                next_action = str(suggestion["next_action"])
            if isinstance(suggestion.get("confidence"), (int, float)):
                confidence = round(float(suggestion["confidence"]), 4)
            if isinstance(suggestion.get("outcome_labels"), list) and suggestion["outcome_labels"]:
                labels = [str(x) for x in suggestion["outcome_labels"]]
            model_used = str(suggestion.get("model") or "injected_model_fn")
        except Exception:
            logger.debug("roi_review.model_fn_failed", exc_info=True)

    out = {
        "scope": scope, "subject_id": subject_id,
        "roi": roi_block,
        "outcome_labels": labels,
        "next_action": next_action,
        "confidence": round(float(min(1.0, max(0.0, confidence))), 4),
        "missing_data": missing,
        "status": status,
    }
    _maybe_record(out, data, model_fn, started, record, model_used=model_used)
    return out


def _maybe_record(out: dict[str, Any], input_data: dict[str, Any],
                  model_fn: Optional[ModelFn], started: float, record: bool,
                  *, model_used: str | None = None) -> None:
    """best-effort 落一行 vkpi_skill_runs(skill_name/version/model_used/cost/latency/output)。"""
    if not record:
        return
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        from app.domains.marketing_brain import skill_registry

        skill_registry.record_skill_run(
            skill_name=SKILL_NAME,
            skill_version=SKILL_VERSION,
            input_schema=dict(input_data or {}),
            model_used=model_used if model_used is not None else ("rule_v0" if model_fn is None else "injected_model_fn"),
            output=out,
            cost_cents=0,           # 规则路径零成本;真烧由调用方注入并自行记账
            latency_ms=latency_ms,
        )
    except Exception:
        logger.debug("roi_review.record_failed", exc_info=True)


# ── EVAL：纯规则、可注入打分,零真 LLM / 零真 DB(record=False 跑) ──────────────
def _eval_runner(case_input: dict[str, Any]) -> dict[str, Any]:
    """eval 专用 runner:不落账(record=False),用注入的 model_fn 模拟产出,断言形状/状态。"""
    fn = case_input.get("model_fn")
    payload = {k: v for k, v in case_input.items() if k != "model_fn"}
    return run(payload, model_fn=fn, record=False)


def _status_metric(expected: Any, actual: Any) -> tuple[bool, float]:
    """对照期望 status(+ missing_data 标记一致)打分。"""
    if not isinstance(actual, dict):
        return False, 0.0
    ok_status = str(actual.get("status")) == str(expected.get("status"))
    ok_missing = bool(actual.get("missing_data")) == bool(expected.get("missing_data"))
    # 形状自检:roi 三键齐 + outcome_labels 非空 + confidence 在 [0,1]。
    roi = actual.get("roi") or {}
    shape_ok = (
        set(roi.keys()) == {"spend_cents", "attributed_gmv_cents", "roi_ratio"}
        and isinstance(actual.get("outcome_labels"), list) and len(actual["outcome_labels"]) > 0
        and isinstance(actual.get("confidence"), (int, float)) and 0.0 <= actual["confidence"] <= 1.0
    )
    hit = ok_status and ok_missing and shape_ok
    score = (0.5 * ok_status + 0.3 * ok_missing + 0.2 * shape_ok)
    return hit, float(score)


def _build_eval_cases() -> list[Any]:
    from app.domains.marketing_brain.evals import EvalCase

    # 为 hermetic(零真 DB / 零真 LLM),EVAL_CASES 全走【输入校验】稳定路径:
    #   _clean_int(负数/0) 归零 → 既无 project_id 也无 kol_pool_id → invalid_input。
    # 真 ROI 聚合路径(需活 DB / monkeypatch)由 tests/test_skill_roi_review.py 覆盖。
    return [
        EvalCase(
            name="invalid_input_empty",
            input={},
            expected={"status": "invalid_input", "missing_data": True},
            metric=_status_metric,
        ),
        EvalCase(
            name="invalid_input_zero_project",
            input={"project_id": 0},
            expected={"status": "invalid_input", "missing_data": True},
            metric=_status_metric,
        ),
        EvalCase(
            name="invalid_input_negative_kol",
            input={"kol_pool_id": -5},
            expected={"status": "invalid_input", "missing_data": True},
            metric=_status_metric,
        ),
        EvalCase(
            name="invalid_input_with_model_fn",
            input={"model_fn": lambda ctx: {"next_action": "custom", "confidence": 0.9}},
            expected={"status": "invalid_input", "missing_data": True},
            metric=_status_metric,
        ),
    ]


EVAL_CASES = _build_eval_cases()


def evaluate() -> dict[str, Any]:
    """跑 EVAL_CASES,返回 EvalReport.to_dict()。零真 LLM、零落账。"""
    from app.domains.marketing_brain.evals import run_eval

    report = run_eval(_eval_runner, EVAL_CASES, suite="roi_review_v1")
    return report.to_dict()
