"""Marketing Brain · Skill Orchestrator —— 把 5 个 Skill 真正接进规划编排(非仅人工 HTTP)。

背景 / 缺口(审计实证):
  marketing_brain/skill_registry.py + 5 个 skill(creator_match / brief_generate / content_score /
  roi_review / campaign_plan)代码齐、可 rule 模式跑、有运行账本(vkpi_skill_runs),但唯一真实可达
  路径是人工 POST /api/admin/vkpi/skills/{name}/run;Skill 没接进规划 orchestrator —— agent/planner
  无法「在合适节点选择并调用 skill」。

本模块补的就是「planner / orchestrator 侧接线」:
  - plan_skills(goal, context)        : 据目标关键词 + context 信号,确定性地「选」该调哪些 skill +
                                        各自 input(零 LLM、零成本、纯规则,可单测)。
  - orchestrate_skills(goal, context) : 按计划【经 skill_registry.dispatch_skill 真调用】选中的 skill,
                                        产出进【既有运行账本 vkpi_skill_runs】(skill 内 record=True 落账),
                                        汇总成编排回执(下游 recommendation/feedback 可读)。

安全闸(与既有红线一致):
  - dry_run 默认 True → model_fn=None(走规则,绝不真烧 LLM);dry_run=False 仅在预算闸放行时才允许
    传 model_fn,且本编排器默认 model_fn 仍是 None(真烧 LLM 由更高层显式注入,这里不自作主张烧钱)。
  - 预算闸:costs.budget_guard.check_budget("marketing_brain_skill", est) 不通过 → 该次编排标 budget_blocked,
    跳过真调用(仍留计划痕),绝不偷偷烧钱。
  - gate 开关:VKPI_SKILL_ORCHESTRATION(默认 on)可整体关掉自动选择;关掉时 orchestrate 返回 disabled,
    既有人工 HTTP / action 闸路径完全不受影响。
  - 绝不触 viltrox_fit_score;只读 + 经 registry 分发 + 落既有账本,不新增写业务表。

注意:本编排器【不】碰 actions/executors.py 的 _maybe_run_skill 那条 action 自动跑闸(那是另一条已存在的
      接线,默认 OFF);这里是独立的 planner/orchestrator 侧路径,二者互不干扰。
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

from app.core.logging import get_logger
from app.domains.marketing_brain import skill_registry

logger = get_logger(__name__)

# 预算闸 scope(与 costs.budget_guard 的 scope 口径对齐;未配置预算时 check_budget 默认放行)。
_BUDGET_SCOPE = "marketing_brain_skill"

# 单个 skill dry-run 估价(分):走规则不烧 LLM → 0;真烧 LLM 时由调用方按实际计费,另走 record_cost。
_DRY_RUN_EST_CENTS = 0


def _gate_enabled() -> bool:
    """整体 gate:VKPI_SKILL_ORCHESTRATION 显式置 0/false/off/no 才关;默认开(此路径本就是显式编排)。"""
    raw = str(os.environ.get("VKPI_SKILL_ORCHESTRATION", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── ① 规划:据目标 + context 选 skill + 拼 input(确定性,零 LLM)─────────────────
def _select_skills(goal: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    """据目标关键词 + context 携带的实体信号,选出该调哪些 skill 以及各自 input。

    口径(对齐 agents.orchestrator._plan_steps 的关键词风格,但产出的是「可执行 skill 节点」):
      - 有产品/SKU 信号(product|sku 或目标提到 找人/launch/match)→ creator_match
      - 有 product + budget 信号(目标提到 战役/campaign/plan/预算)→ campaign_plan
      - 有 kol_pool_id + product(目标提到 话术/brief/合作)→ brief_generate
      - 有 analysis_cache_ref / target_id(目标提到 内容/打分/score)→ content_score
      - 有 project_id 或 kol_pool_id(目标提到 复盘/roi/回顾)→ roi_review
    至少选一个(默认 creator_match,只要有 product/sku;否则空选 + 诚实说明缺信号)。
    """
    g = _text(goal)
    gl = g.lower()
    ctx = context if isinstance(context, dict) else {}
    product = _text(ctx.get("product") or ctx.get("sku"))
    market = _text(ctx.get("market") or ctx.get("country"))
    budget_cents = _int_or_none(ctx.get("budget_cents") or ctx.get("budget"))
    kol_pool_id = _int_or_none(ctx.get("kol_pool_id"))
    project_id = _int_or_none(ctx.get("project_id"))
    target_id = _text(ctx.get("target_id") or ctx.get("analysis_cache_ref"))

    nodes: list[dict[str, Any]] = []

    def _kw(*words: str) -> bool:
        return any(w in g for w in words) or any(w.lower() in gl for w in words)

    want_match = bool(product) or _kw("找人", "匹配", "launch", "match", "creator", "推荐")
    want_campaign = bool(budget_cents) or _kw("战役", "campaign", "plan", "预算", "投放")
    want_brief = (kol_pool_id is not None) and (_kw("话术", "brief", "合作", "邀约", "outreach") or bool(product))
    want_content = bool(target_id) or _kw("内容打分", "content", "score", "打分", "视频质量")
    want_roi = (project_id is not None or kol_pool_id is not None) and _kw("复盘", "roi", "回顾", "review", "结算")

    if want_match and product:
        nodes.append({
            "skill_name": "creator_match",
            "input": {"product": product, "market": market, "limit": _int_or_none(ctx.get("limit")) or 10},
            "why": "目标涉及找人/产品匹配 → 经 registry 调 creator_match 出候选(进运行账本→recommendation)。",
        })
    if want_campaign and product:
        nodes.append({
            "skill_name": "campaign_plan",
            "input": {"product": product, "market": market,
                      "budget_cents": budget_cents or 0, "goal": _text(ctx.get("goal")) or "awareness"},
            "why": "目标涉及战役规划/预算 → 调 campaign_plan 出梯队配比+预算分配。",
        })
    if want_brief and kol_pool_id is not None:
        nodes.append({
            "skill_name": "brief_generate",
            "input": {"kol_pool_id": kol_pool_id, "product": product or _text(ctx.get("product_name")),
                      "angle": _text(ctx.get("angle"))},
            "why": "目标涉及合作话术 + 已锁定 KOL → 调 brief_generate 出可审 brief。",
        })
    if want_content and target_id:
        nodes.append({
            "skill_name": "content_score",
            "input": {"analysis_cache_ref": {"target_id": target_id}},
            "why": "目标涉及内容打分 + 有 final_v1 缓存 ref → 调 content_score 读缓存投影。",
        })
    if want_roi and (project_id is not None or kol_pool_id is not None):
        roi_input: dict[str, Any] = {}
        if project_id is not None:
            roi_input["project_id"] = project_id
        if kol_pool_id is not None:
            roi_input["kol_pool_id"] = kol_pool_id
        nodes.append({
            "skill_name": "roi_review",
            "input": roi_input,
            "why": "目标涉及复盘/ROI + 有 project/kol 主键 → 调 roi_review 聚合结果→feedback。",
        })

    # 兜底:只要有 product 且一个 skill 都没选中(关键词没命中),至少给一个 creator_match。
    if not nodes and product:
        nodes.append({
            "skill_name": "creator_match",
            "input": {"product": product, "market": market, "limit": _int_or_none(ctx.get("limit")) or 10},
            "why": "兜底:有产品信号 → 默认 creator_match 出候选。",
        })
    return nodes


def plan_skills(goal: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """PLAN-ONLY 的 skill 选择:据 goal + context 选该调哪些 skill(不执行)。

    返回 {goal, skill_nodes:[{skill_name, input, why}], available_skills, note}。
    供前端 / 上层编排预览「编排器会选哪些 skill」,与 orchestrate_skills 的选择口径一致。
    """
    ctx = context if isinstance(context, dict) else {}
    nodes = _select_skills(_text(goal), ctx)
    return {
        "goal": _text(goal),
        "skill_nodes": nodes,
        "available_skills": skill_registry.available_skills(),
        "note": "PLAN-ONLY:仅选 skill + 拼 input,不执行;真跑走 orchestrate_skills(dry_run 默认 True,不烧 LLM)。",
    }


# ── ② 编排执行:经 registry 真调选中的 skill + 落既有账本 ────────────────────────
def orchestrate_skills(
    goal: str,
    *,
    context: dict[str, Any] | None = None,
    dry_run: bool = True,
    model_fn: Optional[Callable[[dict[str, Any]], Any]] = None,
    record: bool = True,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """据 plan_skills 的选择,经 skill_registry.dispatch_skill 真调用选中的 skill。

    这是「skill 能经编排器被调用(非仅人工 HTTP)」的真入口:
      - gate 关 → {status:'disabled'}(既有人工/ action 路径不受影响);
      - 预算闸不通过 → 该次标 budget_blocked,跳过真调用(留计划),绝不偷烧钱;
      - dry_run=True(默认)→ 强制 model_fn=None(走规则不烧 LLM);
      - 每个 skill 经 dispatch_skill(record=True)→ 运行落 vkpi_skill_runs(既有账本→下游 recommendation/feedback);
      - 返回 {status, goal, dry_run, results:[{skill_name, status, output, recommendations?}], note}。
    红线:零触 viltrox_fit_score。
    """
    del staff  # 编排选择与 skill 运行不按 staff 过滤(skill 内部各自只读取既有数据);保留参数对齐调用方。
    if not _gate_enabled():
        return {"status": "disabled", "goal": _text(goal),
                "note": "VKPI_SKILL_ORCHESTRATION 已关;编排器侧 skill 自动选择被 gate 关闭(人工 HTTP 不受影响)。"}

    plan = plan_skills(goal, context=context)
    nodes = plan.get("skill_nodes") or []
    if not nodes:
        return {"status": "no_skill_selected", "goal": _text(goal), "results": [],
                "note": "据 goal+context 未选中任何 skill(缺 product/kol/project 等触发信号);诚实返回空,不空跑。"}

    # 预算闸:dry_run 估价 0(不烧 LLM)→ 默认放行;真烧时 est 由更高层先核。require_configured=False
    #（默认):未配置预算不误杀(对齐 vkpi-video-budget-gate 教训)。
    est_cents = _DRY_RUN_EST_CENTS if dry_run else max(_DRY_RUN_EST_CENTS, 1)
    budget_ok = True
    try:
        from app.domains.costs import budget_guard

        budget_ok = bool(budget_guard.check_budget(_BUDGET_SCOPE, est_cents / 100.0))
    except Exception:
        # 预算子系统不可用 → 仅 dry_run(0 成本)放行;非 dry_run 保守拦。
        logger.debug("skill_orchestrator: budget_guard unavailable", exc_info=True)
        budget_ok = bool(dry_run)

    if not budget_ok:
        return {"status": "budget_blocked", "goal": _text(goal),
                "planned": [n["skill_name"] for n in nodes],
                "note": "预算闸未放行;跳过真调用只留计划痕,绝不偷烧钱。重试请先在预算面板放量或用 dry_run。"}

    # dry_run 强制 model_fn=None(不烧 LLM);仅 dry_run=False 且调用方显式传 model_fn 才放行真烧。
    effective_model_fn = None if dry_run else model_fn

    results: list[dict[str, Any]] = []
    for node in nodes:
        name = _text(node.get("skill_name"))
        skill_input = node.get("input") if isinstance(node.get("input"), dict) else {}
        dispatched = skill_registry.dispatch_skill(
            name, skill_input, model_fn=effective_model_fn, record=record
        )
        out = dispatched.get("output") if isinstance(dispatched, dict) else {}
        item: dict[str, Any] = {
            "skill_name": name,
            "status": dispatched.get("status") if isinstance(dispatched, dict) else "error",
            "why": node.get("why"),
            "input": skill_input,
            "output": out,
        }
        # 下游 recommendation 直读口径:creator_match 的 recommendations 直接抬到顶层方便上层消费。
        if isinstance(out, dict) and isinstance(out.get("recommendations"), list):
            item["recommendations_count"] = len(out["recommendations"])
        results.append(item)

    ran = sum(1 for r in results if r.get("status") == "ok")
    return {
        "status": "ok",
        "goal": _text(goal),
        "dry_run": bool(dry_run),
        "skills_selected": [r["skill_name"] for r in results],
        "skills_ran_ok": ran,
        "results": results,
        "note": (
            "skill 经编排器(registry 分发)真调用,运行已落 vkpi_skill_runs 账本(record=True)；"
            "dry_run=True 走规则不烧 LLM;零触 viltrox_fit_score。"
        ),
    }
