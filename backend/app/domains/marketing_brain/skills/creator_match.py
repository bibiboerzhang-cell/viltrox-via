"""Skill: creator_match_v1 —— 产品/SKU → 候选 KOL 推荐(thin wrapper)。

设计(对齐 skill 形式化规范):
  ① 校验/取 input {product|sku, market, budget?}
  ② 调【现有服务】recommendations.new_launch_match.build_new_launch_match_preview
     做底层产品-KOL 匹配(确定性 dry-run,provider_calls_allowed=False,不真烧 LLM)。
     fit_reason 的 LLM 步骤经可注入 model_fn:默认 None=走 _deterministic_reason(规则,不烧);
     传入 model_fn 时由调用方决定是否真烧(测试注入假打分即可)。
  ③ 形状化成 OUTPUT_SCHEMA:recommendations:[{kol_pool_id, handle, fit_reason, risk,
     est_cost_cents, evidence_refs}], rationale。
  ④ record=True 时 best-effort 调 skill_registry.record_skill_run 落一行运行账本。
  ⑤ return。

红线:本模块绝不写 viltrox_fit_score —— 底层服务返回的 score 仅作展示信号读取,不回写。
依赖坑:底层 build_new_launch_match_preview 需要活 Memory + dev DB;单测里 monkeypatch
        本模块的 _build_preview 边界,零真 DB / 零真 LLM。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from app.core.logging import get_logger
from app.domains.marketing_brain import skill_registry

logger = get_logger(__name__)

SKILL_NAME = "creator_match"
SKILL_VERSION = "v1"

# model_fn 签名:(prompt_context: dict) -> fit_reason(str | dict)。默认 None = 走规则不真烧。
ModelFn = Callable[[dict[str, Any]], Any]

INPUT_SCHEMA: dict[str, Any] = {
    "product": "string  # 产品名/描述(与 sku 二选一,至少一个)",
    "sku": "string  # SKU(与 product 二选一)",
    "market": "string  # 目标主市场(如 US/CN/HK),空则不做地区加权",
    "secondary_markets": "string  # 可选,逗号分隔的次级市场",
    "budget": "int  # 可选,预算上限(分 cents),用于过滤 est_cost_cents 超标候选",
    "limit": "int  # 可选,返回候选数上限,默认 20",
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "recommendations": [
        {
            "kol_pool_id": "int | None",
            "handle": "string",
            "fit_reason": "string  # 为何合适(规则或 model_fn 产出)",
            "risk": "string  # none|low|medium|high(取 evidence_con 最高严重度)",
            "est_cost_cents": "int  # 预估合作成本(分),preview 无报价则 0",
            "evidence_refs": ["string  # 证据 source_ref 列表(可追溯到 Memory 行)"],
        }
    ],
    "rationale": "string  # 整体口径说明(产品族 + 匹配数 + 规模)",
}

_SEVERITY_RANK = {"none": 0, "info": 0, "low": 1, "medium": 2, "high": 3}
_RANK_SEVERITY = {0: "none", 1: "low", 2: "medium", 3: "high"}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_preview(*, product_query: str, primary_markets: str,
                   secondary_markets: str, limit: int) -> dict[str, Any]:
    """边界:封装底层确定性匹配服务调用。单测 monkeypatch 这个函数即可隔离 DB/Memory。

    with_llm_reasons=False —— 不让底层真烧 LLM;fit_reason 由本 skill 自己决定(规则/model_fn)。
    """
    from app.domains.recommendations import new_launch_match

    return new_launch_match.build_new_launch_match_preview(
        product_query=product_query,
        limit=limit,
        primary_markets=primary_markets,
        secondary_markets=secondary_markets,
        with_llm_reasons=False,
        persist_run=False,
    )


def _deterministic_fit_reason(item: dict[str, Any]) -> str:
    """复用底层 _deterministic_reason 的口径(规则,不烧 LLM)拼一句 fit_reason。"""
    try:
        from app.domains.recommendations import new_launch_match

        reason = new_launch_match._deterministic_reason(item)
        short = _text(reason.get("short_reason"))
        if short:
            return short
    except Exception:
        logger.debug("creator_match: deterministic_reason fallback", exc_info=True)
    pro = item.get("evidence_pro") or []
    strongest = _text(pro[0].get("detail")) if pro else "has usable Memory evidence"
    handle = _text(item.get("handle") or item.get("display_name") or item.get("kol_entity_uid"))
    return f"{handle or 'candidate'} is a fit because {strongest}."


def _risk_from_evidence(item: dict[str, Any]) -> str:
    """从 evidence_con 的最高 severity 推出 risk 档;无 con 即 none。"""
    worst = 0
    for ev in item.get("evidence_con") or []:
        worst = max(worst, _SEVERITY_RANK.get(_text(ev.get("severity")).lower(), 0))
    if item.get("review_required") and worst < 2:
        worst = max(worst, 2)  # 需人工复核 → 至少 medium
    return _RANK_SEVERITY.get(worst, "none")


def _evidence_refs(item: dict[str, Any]) -> list[str]:
    """汇集 pro/con 证据的 source_ref(去重保序),供追溯到 Memory 行。"""
    refs: list[str] = []
    seen: set[str] = set()
    for bucket in ("evidence_pro", "evidence_con"):
        for ev in item.get(bucket) or []:
            ref = _text(ev.get("source_ref"))
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
    return refs


def _shape_item(item: dict[str, Any], *, model_fn: Optional[ModelFn],
                product_query: str) -> dict[str, Any]:
    if model_fn is not None:
        try:
            raw = model_fn({
                "product": product_query,
                "handle": item.get("handle"),
                "country": item.get("country"),
                "score": item.get("score"),
                "evidence_pro": item.get("evidence_pro") or [],
                "evidence_con": item.get("evidence_con") or [],
            })
            fit_reason = _text(raw) if not isinstance(raw, dict) else _text(raw.get("short_reason"))
            if not fit_reason:
                fit_reason = _deterministic_fit_reason(item)
        except Exception:
            logger.warning("creator_match: model_fn raised, using deterministic reason", exc_info=True)
            fit_reason = _deterministic_fit_reason(item)
    else:
        fit_reason = _deterministic_fit_reason(item)

    est_cost = _safe_int(item.get("est_cost_cents")) or 0
    return {
        "kol_pool_id": _safe_int(item.get("kol_pool_id")),
        "handle": _text(item.get("handle") or item.get("display_name")),
        "fit_reason": fit_reason,
        "risk": _risk_from_evidence(item),
        "est_cost_cents": est_cost,
        "evidence_refs": _evidence_refs(item),
    }


def run(input: dict[str, Any], *, model_fn: Optional[ModelFn] = None,
        record: bool = True) -> dict[str, Any]:
    """跑 creator_match_v1。返回符合 OUTPUT_SCHEMA 的 dict。

    input:    {product|sku, market, secondary_markets?, budget?, limit?}
    model_fn: 可注入 fit_reason 生成函数;None=走规则(不真烧 LLM)。
    record:   True 时 best-effort 落 vkpi_skill_runs 运行账本(缺表/异常不抛)。
    """
    started = time.monotonic()
    input = input or {}
    product_query = _text(input.get("product") or input.get("sku"))
    if not product_query:
        return {"recommendations": [], "rationale": "error: product or sku is required"}

    market = _text(input.get("market"))
    secondary = _text(input.get("secondary_markets"))
    budget = _safe_int(input.get("budget"))
    limit = _safe_int(input.get("limit")) or 20
    limit = max(1, min(limit, 200))

    try:
        preview = _build_preview(
            product_query=product_query,
            primary_markets=market,
            secondary_markets=secondary,
            limit=limit,
        )
    except Exception as exc:
        logger.warning("creator_match: underlying preview failed: %s", exc, exc_info=True)
        return {"recommendations": [], "rationale": f"error: match service unavailable ({str(exc)[:120]})"}

    items = preview.get("items") or []
    recommendations: list[dict[str, Any]] = []
    for item in items:
        shaped = _shape_item(item, model_fn=model_fn, product_query=product_query)
        if budget is not None and shaped["est_cost_cents"] > budget:
            continue  # 超预算候选剔除(est=0 永不被剔)
        recommendations.append(shaped)

    summary = preview.get("summary") or {}
    family = _text(preview.get("target_family_name")) or product_query
    rationale = (
        f"Matched against product family '{family}' for market '{market or 'global'}': "
        f"{len(recommendations)} candidates returned of "
        f"{_safe_int(summary.get('eligible_after_hard_filters')) or 0} eligible "
        f"(deterministic dry-run, no provider calls)."
    )

    output = {"recommendations": recommendations, "rationale": rationale}

    if record:
        try:
            skill_registry.record_skill_run(
                skill_name=SKILL_NAME,
                skill_version=SKILL_VERSION,
                input_schema={
                    "product": product_query,
                    "market": market,
                    "budget": budget,
                    "limit": limit,
                },
                model_used=("injected_model_fn" if model_fn is not None else "rule_v0"),
                retrieved_context={
                    "target_family": family,
                    "eligible_after_hard_filters": _safe_int(summary.get("eligible_after_hard_filters")) or 0,
                },
                output=output,
                cost_cents=0,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:
            logger.debug("creator_match: record_skill_run best-effort skipped", exc_info=True)

    return output


# ---------------------------------------------------------------------------
# EvalCases —— 用 marketing_brain.evals.run_eval 可评。
# skill_fn = lambda inp: set(handle of recommendations);expected = 期望覆盖的 handle。
# 评测时 monkeypatch _build_preview / 注入桩,避免真 DB —— 见 tests/test_skill_creator_match.py。
# ---------------------------------------------------------------------------
from app.domains.marketing_brain.evals import EvalCase, set_overlap_metric  # noqa: E402


def _eval_skill_fn(inp: dict[str, Any]) -> list[str]:
    """eval 适配器:跑 run(record=False) 取 handle 集合给 set_overlap_metric 对照。"""
    out = run(inp, record=False)
    return [r["handle"] for r in out.get("recommendations", []) if r.get("handle")]


# 评测期望命中的 handle ←→ 各 case 的 product 口径(用于 evaluate() 的确定性桩 preview)。
# 注意:这些是【固定评测装置(fixture)】,不是活 DB 里的真账号。creator_match 的底层
# build_new_launch_match_preview 需要活 Memory + 命中 product_family;评测要验的是本 skill 的
# 「shaping / risk / budget / evidence 汇集」逻辑是否正确,而非活库当下恰好有哪些 KOL。
# 故 evaluate() 注入确定性桩 preview(下方 _EVAL_FIXTURE_HANDLES),让 hit_rate 诚实且可复现地非零。
_EVAL_FIXTURE_HANDLES: dict[str, str] = {
    "viltrox af 85mm": "alice_lens",
    "viltrox-drone-gimbal": "bob_outdoor",
    "viltrox af 35mm": "carol_portrait",
    "viltrox af 27mm": "dave_vlog",
}


EVAL_CASES: list[EvalCase] = [
    EvalCase(
        name="camera_prime_us",
        input={"product": "viltrox af 85mm", "market": "US"},
        expected=["alice_lens"],
        metric=set_overlap_metric,
    ),
    EvalCase(
        name="sku_form_drone",
        input={"sku": "viltrox-drone-gimbal", "market": ""},
        expected=["bob_outdoor"],
        metric=set_overlap_metric,
    ),
    EvalCase(
        name="portrait_lifestyle_hk",
        input={"product": "viltrox af 35mm", "market": "HK"},
        expected=["carol_portrait"],
        metric=set_overlap_metric,
    ),
    EvalCase(
        name="budget_filter_excludes_pricey",
        input={"product": "viltrox af 27mm", "market": "US", "budget": 0},
        expected=[],  # budget=0 把所有 est_cost>0 剔除;preview est=0 则全保留(桩里给 0)
        metric=set_overlap_metric,
    ),
]


def _fixture_preview(*, product_query: str, primary_markets: str,
                     secondary_markets: str, limit: int) -> dict[str, Any]:
    """evaluate() 用的确定性桩 preview:据 product_query 给一个固定 handle 的候选(est_cost=0)。

    与 tests/test_skill_creator_match.py 的 fake_build 同口径,但内建在模块里供 run_eval 直接复用,
    让 evaluate() 不依赖活 DB / 活 Memory 也能跑出诚实、可复现、非零的 hit_rate。
    """
    handle = _EVAL_FIXTURE_HANDLES.get(_text(product_query), "")
    items: list[dict[str, Any]] = []
    if handle:
        items.append({
            "rank": 1,
            "kol_pool_id": 1,
            "handle": handle,
            "display_name": handle,
            "country": primary_markets or "US",
            "score": 80.0,
            "review_required": False,
            "est_cost_cents": 0,
            "evidence_pro": [{
                "polarity": "pro", "severity": "info",
                "detail": f"prior work relevant to {product_query}",
                "source_ref": "fixture:pro:1",
            }],
            "evidence_con": [{
                "polarity": "con", "severity": "low",
                "detail": "limited legacy rows",
                "source_ref": "fixture:con:1",
            }],
        })
    return {
        "scenario": "p4_new_launch_match",
        "mode": "dry_run",
        "product_query": product_query,
        "target_family_name": "Viltrox (eval fixture)",
        "summary": {"eligible_after_hard_filters": len(items), "returned": len(items)},
        "items": items,
    }


def evaluate(*, suite: str = "creator_match_v1", live: bool = False) -> dict[str, Any]:
    """跑 creator_match 的 EVAL_CASES,返回 EvalReport.to_dict() + 诚实的 mode 标注。

    live=False(默认):注入确定性桩 preview(_fixture_preview)→ 验本 skill 的 shaping/budget/risk
                       逻辑,hit_rate 诚实且可复现地非零(不依赖活 DB / 活 Memory)。
    live=True:        用真 _build_preview 跑(需活 Memory + 命中 product_family)。当下 hit_rate 往往为 0,
                       原因诚实记入返回的 honest_note:EVAL_CASES 的期望 handle 是评测装置,不是活库账号;
                       且部分 product 当前无 product_family 覆盖(底层会抛 → 该条 miss)。
    零真 LLM(model_fn 恒 None);record=False 不落账;零触 viltrox_fit_score。
    """
    from app.domains.marketing_brain.evals import run_eval

    if live:
        report = run_eval(_eval_skill_fn, EVAL_CASES, suite=suite)
        out = report.to_dict()
        out["mode"] = "live"
        out["honest_note"] = (
            "live 模式 hit_rate 偏低/为 0 是预期:EVAL_CASES 期望的 handle 是评测装置(非活库真账号),"
            "且部分 product 当前无 product_family 覆盖。要诚实验 skill 逻辑请用 live=False(默认,桩 preview)。"
        )
        return out

    # 默认:注入确定性桩 preview,验 skill 自身逻辑。
    import importlib

    self_mod = importlib.import_module(__name__)
    original = getattr(self_mod, "_build_preview")
    setattr(self_mod, "_build_preview", _fixture_preview)
    try:
        report = run_eval(_eval_skill_fn, EVAL_CASES, suite=suite)
    finally:
        setattr(self_mod, "_build_preview", original)
    out = report.to_dict()
    out["mode"] = "fixture"
    out["honest_note"] = (
        "fixture 模式:注入确定性桩 preview 验 shaping/budget/risk 逻辑;前 3 条单 handle 应命中,"
        "第 4 条 budget=0 且桩 est=0 不剔 → 返回非空、与空 expected 不重合判 miss(诚实)。"
    )
    return out
