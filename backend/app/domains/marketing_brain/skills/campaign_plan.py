"""Skill【campaign_plan_v1】—— 给定 {product, market, budget_cents, goal} 产出一份可执行营销战役蓝图。

形式化规范(对齐 skills/__init__.py 铁律):
  thin wrapper,不重写算法 —— 底层全复用现有服务:
    ① 市场信号:复用 app.domains.market.market_brain.build_daily_brief(只读合成竞品动/机会窗/今日建议);
    ② 创作者底盘:复用 app.domains.kol.pool.list_pool(按 market 过滤拿候选池,读现成 fit 排序做展示)。
  LLM 策略步骤经可注入 model_fn(默认 None=走规则启发式 _rule_strategy,不真烧 LLM、不走外网代理)。
  record=True 时 best-effort 调 skill_registry.record_skill_run 落一行运行账本(缺表/异常绝不拖垮主流程)。

输入  INPUT_SCHEMA :{product, market, budget_cents, goal}
输出  OUTPUT_SCHEMA:{plan:{creator_mix[], budget_allocation[], timeline[], content_angles[]}, risks[]}

红线:零触 viltrox_fit_score —— 创作者只读 list_pool 现成排序展示,绝不写 fit。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

SKILL_NAME = "campaign_plan"
SKILL_VERSION = "v1"

# model_fn 签名:(prompt_ctx: dict) -> dict | None。返回 None / 抛错 → 回落规则启发式。
ModelFn = Callable[[dict[str, Any]], Optional[dict[str, Any]]]

INPUT_SCHEMA: dict[str, Any] = {
    "product": "str  产品 / SKU 名(必填,用于检索定位)",
    "market": "str  目标市场地区码或名(如 US / EU / CN;可空=全球)",
    "budget_cents": "int  战役总预算(分;>=0)",
    "goal": "str  战役目标(如 awareness / conversion / launch;可空=awareness)",
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "plan": {
        "creator_mix": "list[dict]  创作者梯队配比(tier / share / count / sample_creators)",
        "budget_allocation": "list[dict]  预算分配(bucket / pct / amount_cents)",
        "timeline": "list[dict]  阶段时间线(phase / week / focus)",
        "content_angles": "list[dict]  内容角度(angle / why / market_signal)",
    },
    "risks": "list[dict]  风险项(risk / severity / mitigation)",
    "meta": "dict  product / market / budget_cents / goal / model_used / signal_coverage",
}

# 战役目标 → 创作者梯队配比启发式(share 之和 = 1.0)。
_GOAL_MIX = {
    "awareness": [("mega", 0.45), ("mid", 0.35), ("micro", 0.20)],
    "launch": [("mega", 0.40), ("mid", 0.35), ("micro", 0.25)],
    "conversion": [("mid", 0.30), ("micro", 0.45), ("nano", 0.25)],
}
_DEFAULT_GOAL = "awareness"

# 目标 → 预算桶分配(pct 之和 = 1.0)。
_GOAL_BUDGET = {
    "awareness": [("creator_fees", 0.60), ("content_boost", 0.25), ("ops_buffer", 0.15)],
    "launch": [("creator_fees", 0.55), ("content_boost", 0.30), ("ops_buffer", 0.15)],
    "conversion": [("creator_fees", 0.50), ("paid_amplify", 0.35), ("ops_buffer", 0.15)],
}


def _clean_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _str(value: Any) -> str:
    return str(value or "").strip()


def _candidate_pool(market: str, product: str) -> dict[str, Any]:
    """复用 kol.pool.list_pool 拿候选创作者(按 market 当 country 过滤)。只读,缺库/异常返回空池。"""
    try:
        from app.domains.kol import pool

        res = pool.list_pool(limit=24, country=market, query=product, sort_by="fit")
        items = res.get("items") if isinstance(res, dict) else None
        return {"items": list(items or []), "status": "ready" if items else "empty"}
    except Exception:
        return {"items": [], "status": "unavailable"}


def _market_signals() -> dict[str, Any]:
    """复用 market_brain.build_daily_brief 拿市场信号(竞品动 / 机会窗 / 今日建议)。只读。"""
    try:
        from app.domains.market import market_brain

        brief = market_brain.build_daily_brief()
        if isinstance(brief, dict):
            return brief
    except Exception:
        pass
    return {"status": "unavailable", "sections": {}, "coverage": "0/5"}


def _sample_creators(pool_items: list[dict[str, Any]], tier_idx: int, count: int) -> list[dict[str, Any]]:
    """从候选池切一段做展示样本(读现成字段,绝不写 fit)。tier_idx 用于切片分层,不重算评分。"""
    if not pool_items:
        return []
    n = len(pool_items)
    start = (tier_idx * 6) % max(1, n)
    out: list[dict[str, Any]] = []
    for it in pool_items[start:start + max(0, count)]:
        if not isinstance(it, dict):
            continue
        out.append({
            "id": it.get("id"),
            "handle": it.get("handle") or it.get("username") or it.get("name"),
            "platform": it.get("platform"),
            # 只读展示现成 fit;绝不重算/回写。
            "fit": it.get("viltrox_fit_score") if "viltrox_fit_score" in it else it.get("fit_score"),
        })
    return out


def _rule_strategy(ctx: dict[str, Any]) -> dict[str, Any]:
    """默认规则启发式策略(不真烧 LLM)：依目标配创作者梯队/预算/时间线/内容角度。

    内容角度直接锚定市场信号(竞品动 + 机会窗 + 今日建议),让蓝图有真信号支撑。
    """
    goal = ctx.get("goal") or _DEFAULT_GOAL
    budget_cents = _clean_int(ctx.get("budget_cents"))
    pool_items: list[dict[str, Any]] = ctx.get("pool_items") or []
    signals: dict[str, Any] = ctx.get("signals") or {}

    mix_spec = _GOAL_MIX.get(goal, _GOAL_MIX[_DEFAULT_GOAL])
    creator_mix: list[dict[str, Any]] = []
    for i, (tier, share) in enumerate(mix_spec):
        # 梯队人数:粗略按 share 摊到一个 10 人盘,至少 1。
        count = max(1, round(share * 10))
        creator_mix.append({
            "tier": tier,
            "share": round(share, 2),
            "count": count,
            "sample_creators": _sample_creators(pool_items, i, min(count, 3)),
        })

    budget_spec = _GOAL_BUDGET.get(goal, _GOAL_BUDGET[_DEFAULT_GOAL])
    budget_allocation: list[dict[str, Any]] = []
    allocated = 0
    for idx, (bucket, pct) in enumerate(budget_spec):
        if idx == len(budget_spec) - 1:
            amount = max(0, budget_cents - allocated)  # 末桶吃掉取整余数,保证合计 = 总预算
        else:
            amount = int(round(budget_cents * pct))
            allocated += amount
        budget_allocation.append({"bucket": bucket, "pct": round(pct, 2), "amount_cents": amount})

    timeline = [
        {"phase": "seed", "week": 1, "focus": "签约 + 内容简报对齐"},
        {"phase": "ramp", "week": 2, "focus": f"{goal} 主推内容上线 + 首轮放量"},
        {"phase": "peak", "week": 3, "focus": "高峰投放 + 实时优化梯队配比"},
        {"phase": "harvest", "week": 4, "focus": "复盘 ROI + 沉淀高转化创作者"},
    ]

    # 内容角度锚定真实市场信号。
    sections = signals.get("sections") if isinstance(signals, dict) else {}
    sections = sections or {}
    content_angles: list[dict[str, Any]] = []
    moves = ((sections.get("competitor_moves") or {}).get("items")) or []
    if moves:
        m0 = moves[0] if isinstance(moves[0], dict) else {}
        content_angles.append({
            "angle": "对位竞品差异化",
            "why": "对标当前竞品动向,凸显产品差异点",
            "market_signal": f"{m0.get('brand', '')} {m0.get('signal_type', '')}".strip() or "competitor_move",
        })
    opps = ((sections.get("opportunities") or {}).get("items")) or []
    if opps:
        content_angles.append({
            "angle": "机会窗卡位",
            "why": "抓住市场大脑识别的机会窗口,先发占位",
            "market_signal": "opportunity_window",
        })
    actions = ((sections.get("today_actions") or {}).get("items")) or []
    if actions:
        a0 = actions[0] if isinstance(actions[0], dict) else {}
        content_angles.append({
            "angle": "今日建议落地",
            "why": _str(a0.get("why")) or "采纳市场大脑今日建议",
            "market_signal": _str(a0.get("title")) or "today_action",
        })
    if not content_angles:
        # 无真信号也给出产品本位的兜底角度,蓝图始终可执行。
        content_angles.append({
            "angle": "产品核心卖点",
            "why": "市场信号暂缺,先以产品本位卖点切入",
            "market_signal": "stale_or_empty",
        })

    return {
        "creator_mix": creator_mix,
        "budget_allocation": budget_allocation,
        "timeline": timeline,
        "content_angles": content_angles,
    }


def _derive_risks(plan: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """从蓝图 + 上下文派生风险项(规则,不烧 LLM)。"""
    risks: list[dict[str, Any]] = []
    budget_cents = _clean_int(ctx.get("budget_cents"))
    pool_status = ctx.get("pool_status")
    signal_coverage = ctx.get("signal_coverage")

    if budget_cents <= 0:
        risks.append({"risk": "预算为零或缺失", "severity": "high",
                      "mitigation": "确认 budget_cents 后再排期投放"})
    elif budget_cents < 100_000:  # < $1000
        risks.append({"risk": "预算偏低,梯队可能摊薄", "severity": "medium",
                      "mitigation": "收窄到 micro/nano 梯队聚焦单一市场"})

    if pool_status in ("empty", "unavailable"):
        risks.append({"risk": "候选创作者池为空", "severity": "high",
                      "mitigation": "先跑 KOL 发现/检索补池,再定梯队配比"})

    if signal_coverage in (None, "0/5", "stale_or_empty"):
        risks.append({"risk": "市场信号覆盖不足", "severity": "low",
                      "mitigation": "内容角度回落产品本位卖点,待信号刷新再校准"})

    # 始终给一条执行节奏风险,蓝图风险列表不空。
    if not risks:
        risks.append({"risk": "排期执行依赖创作者档期", "severity": "low",
                      "mitigation": "seed 阶段预留备选创作者缓冲档期"})
    return risks


def _shape_output(strategy: dict[str, Any], ctx: dict[str, Any], model_used: str) -> dict[str, Any]:
    """把策略 + 派生风险形状化成 OUTPUT_SCHEMA。"""
    plan = {
        "creator_mix": list(strategy.get("creator_mix") or []),
        "budget_allocation": list(strategy.get("budget_allocation") or []),
        "timeline": list(strategy.get("timeline") or []),
        "content_angles": list(strategy.get("content_angles") or []),
    }
    risks = _derive_risks(plan, ctx)
    return {
        "plan": plan,
        "risks": risks,
        "meta": {
            "product": ctx.get("product"),
            "market": ctx.get("market"),
            "budget_cents": _clean_int(ctx.get("budget_cents")),
            "goal": ctx.get("goal") or _DEFAULT_GOAL,
            "model_used": model_used,
            "signal_coverage": ctx.get("signal_coverage"),
        },
    }


def run(
    input: dict[str, Any],
    *,
    model_fn: ModelFn | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """生成营销战役蓝图。

    ① 校验/取 input → ② 复用 market_brain + pool.list_pool 取信号/候选池 →
    ③ LLM 策略经 model_fn(默认 None=走 _rule_strategy 规则启发式,不真烧)→
    ④ 形状化成 OUTPUT_SCHEMA → ⑤ record=True 时 best-effort 落 record_skill_run → ⑥ return。
    """
    t0 = time.monotonic()
    payload = input if isinstance(input, dict) else {}
    product = _str(payload.get("product"))
    if not product:
        return {"status": "error", "error": "product required"}
    market = _str(payload.get("market"))
    budget_cents = _clean_int(payload.get("budget_cents"))
    goal = _str(payload.get("goal")).lower() or _DEFAULT_GOAL

    # ② 复用现有服务(只读)。
    pool_res = _candidate_pool(market, product)
    signals = _market_signals()
    signal_coverage = signals.get("coverage") if isinstance(signals, dict) else None

    ctx: dict[str, Any] = {
        "product": product,
        "market": market,
        "budget_cents": budget_cents,
        "goal": goal,
        "pool_items": pool_res.get("items") or [],
        "pool_status": pool_res.get("status"),
        "signals": signals,
        "signal_coverage": signal_coverage,
    }

    # ③ 策略步骤:有 model_fn 试走注入模型,失败/返回 None 回落规则启发式(默认不真烧)。
    model_used = "rule_v0"
    strategy: dict[str, Any] | None = None
    if model_fn is not None:
        try:
            out = model_fn(ctx)
            if isinstance(out, dict) and out:
                strategy = out
                model_used = _str(out.get("_model")) or "model_fn"
        except Exception:
            strategy = None
    if strategy is None:
        strategy = _rule_strategy(ctx)

    # ④ 形状化。
    result = _shape_output(strategy, ctx, model_used)
    latency_ms = int((time.monotonic() - t0) * 1000)

    # ⑤ best-effort 落账(缺表/异常绝不拖垮主流程)。
    if record:
        try:
            from app.domains.marketing_brain import skill_registry

            skill_registry.record_skill_run(
                skill_name=SKILL_NAME,
                skill_version=SKILL_VERSION,
                input_schema={"product": product, "market": market,
                              "budget_cents": budget_cents, "goal": goal},
                model_used=model_used,
                retrieved_context={"pool_status": ctx.get("pool_status"),
                                   "signal_coverage": signal_coverage},
                output=result,
                cost_cents=0,
                latency_ms=latency_ms,
            )
        except Exception:
            pass

    result["status"] = "ok"
    return result


# ---------------------------------------------------------------------------
# EVAL_CASES —— 用 marketing_brain.evals.run_eval 可评。
# metric 检查输出符合 OUTPUT_SCHEMA 骨架(plan 四段非空 + risks 非空),不对照活数据。
# ---------------------------------------------------------------------------
def _schema_metric(expected: Any, actual: Any) -> tuple[bool, float]:
    """结构打分:plan 四子段都在 + risks 非空 → 满分;按命中比例给 partial。"""
    if not isinstance(actual, dict):
        return False, 0.0
    plan = actual.get("plan") or {}
    checks = [
        isinstance(plan.get("creator_mix"), list) and bool(plan.get("creator_mix")),
        isinstance(plan.get("budget_allocation"), list) and bool(plan.get("budget_allocation")),
        isinstance(plan.get("timeline"), list) and bool(plan.get("timeline")),
        isinstance(plan.get("content_angles"), list) and bool(plan.get("content_angles")),
        isinstance(actual.get("risks"), list) and bool(actual.get("risks")),
    ]
    score = sum(1 for c in checks if c) / len(checks)
    return (score == 1.0), score


def _eval_skill_fn(case_input: Any) -> dict[str, Any]:
    """eval 跑的被测函数:不落库(record=False)、不烧 LLM(model_fn=None)。"""
    return run(case_input, model_fn=None, record=False)


def _import_eval_case():  # 延迟导入,避免 import 期硬依赖
    from app.domains.marketing_brain.evals import EvalCase

    return EvalCase


def build_eval_cases() -> list[Any]:
    """构造 EVAL_CASES(延迟绑定 EvalCase,统一用 _schema_metric)。"""
    EvalCase = _import_eval_case()
    specs = [
        ("awareness_us", {"product": "viltrox-af-85mm", "market": "US",
                          "budget_cents": 500_000, "goal": "awareness"}),
        ("conversion_eu", {"product": "viltrox-af-35mm", "market": "EU",
                           "budget_cents": 200_000, "goal": "conversion"}),
        ("launch_global", {"product": "viltrox-drone-gimbal", "market": "",
                           "budget_cents": 1_000_000, "goal": "launch"}),
        ("zero_budget_guard", {"product": "viltrox-af-27mm", "market": "CN",
                               "budget_cents": 0, "goal": "awareness"}),
    ]
    return [EvalCase(name=name, input=inp, expected=None, metric=_schema_metric) for name, inp in specs]


try:  # 模块导入即暴露 EVAL_CASES(EvalCase 可用时);否则留空 list,run 仍可用。
    EVAL_CASES: list[Any] = build_eval_cases()
except Exception:
    EVAL_CASES = []
