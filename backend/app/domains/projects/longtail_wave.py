"""G3 长尾波次一键铺量(longtail_wave v0)—— 「给这支 SKU 铺 50-100 个 nano/micro」
的批量建议生成器(dry_run 预览,零写库零外联零 LLM)。

复用 strategy_sim 的候选池与长尾选人逻辑(守卫 import,绝不重造):
- 候选池:launch_assembly._candidate_pool(new_launch_match 决定性 dry-run,
  与发射台/strategy_sim 同一真源;pool_limit 按 count 放大,前段与 strategy_sim
  的 60 人池完全同序 —— 长尾人选与 strategy_sim longtail_spread 方案一致级);
- 逐人读数:strategy_sim._build_candidates(rate_card 估价 + 层级;本波次刻意
  不跑 performance_forecast —— 长尾选人只按报价升序,与 _pick_longtail 同键,
  省掉逐人预测的读放大,expected_views 缺席如实说);
- 长尾口径:strategy_sim.LONGTAIL_TIERS(nano/micro,rate_card.TIER_BOUNDS 同源);
- 排序键:与 strategy_sim._pick_longtail 完全同键(cost_p50 升序,kol_pool_id
  tie-break);给了 budget_usd 时用 strategy_sim._greedy_pick 同款贪心装包。

每人产出:小额预算(rate_card p50/low/high + method/confidence)+ 建议动作包
(送样 + 佣金码);佣金码只出「待生成」占位 —— goaffpro 真生成留给执行层
(vkpi_goaffpro),本模块绝不外调。

批量建议:守卫 import actions.producers.make_suggestion 拼出与 vkpi_action_inbox
列对齐的 suggestion dict 列表(inbox_preview)——只预览绝不落库,由人工在
action inbox 侧确认后走既有审批链。

红线(强约束):dry_run 恒 True,零写库;绝不真发外联/邮件(一切生成物=草稿供人审);
佣金码绝不真生成;零触 viltrox_fit_score / rule_v0;数据不足诚实空态,绝不编名单。
compat 约定:SQL 占位符 ?(本模块自身零 SQL,读数全走兄弟件);函数内懒 import。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "longtail_wave_v0"

# 波次人数护栏:目标带 50-100;下限放宽到 5(小库/窄品类不硬凑)。
COUNT_MIN = 5
COUNT_MAX = 100
COUNT_DEFAULT = 50
# 候选池放大:count×3 给长尾筛选留余量,封顶 300 控制逐人估价读放大。
POOL_FACTOR = 3
POOL_HARD_CAP = 300

# 动作包模板(每人同款;佣金码只占位,真生成留给执行层)。
ACTION_PACK_NOTE = (
    "动作包=送样+佣金码草案:佣金码状态恒为 pending_generation 占位,"
    "goaffpro 真生成/真发外联留给执行层与人工审批,本接口绝不外调、绝不真发。"
)

CONSISTENCY_NOTE = (
    "长尾选人与 strategy_sim longtail_spread 同源同键:同一候选池真源"
    "(launch_assembly._candidate_pool)+ 同一层级口径(LONGTAIL_TIERS)"
    "+ 同一排序键(cost_p50 升序,kol_pool_id tie-break);"
    "本波次只是把人数上限从 20 放大到 50-100 并附动作包。"
)


# ── 小工具 ───────────────────────────────────────────────────────────


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _usd(value: float) -> float:
    return round(float(value), 2)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_count(count: Any) -> int:
    try:
        parsed = int(count or COUNT_DEFAULT)
    except (TypeError, ValueError):
        parsed = COUNT_DEFAULT
    return max(COUNT_MIN, min(COUNT_MAX, parsed))


# ── 建议动作包(纯模板拼装,占位不执行) ─────────────────────────────


def _action_pack(sku: str, cand: dict[str, Any]) -> dict[str, Any]:
    """每人建议动作包:送样 + 佣金码(待生成占位)。一切=草稿供人审。"""
    return {
        "actions": [
            {
                "kind": "product_seeding",
                "label": "送样",
                "detail": f"寄送 {sku} 测评样品(nano/micro 长尾以样品置换内容为主,不谈高价坑位)",
                "status": "draft",
            },
            {
                "kind": "commission_code",
                "label": "佣金码",
                "detail": f"为 @{cand.get('handle') or cand['kol_pool_id']} 生成 {sku} 专属佣金码",
                "status": "pending_generation",
                "provider": "goaffpro",
                "code": None,
                "note": "占位——真生成走执行层 goaffpro 集成,需人工批准后执行",
            },
        ],
        "note": ACTION_PACK_NOTE,
    }


def _inbox_suggestions(sku: str, members: list[dict[str, Any]], wave_key: str) -> dict[str, Any]:
    """可直接喂 action inbox 的批量建议(只预览绝不落库)。

    守卫 import actions.producers.make_suggestion —— 字段与 vkpi_action_inbox
    列对齐;写库/审批/执行全留给 inbox 既有链路,本函数零写。
    """
    try:
        from app.domains.actions.producers import make_suggestion
    except ImportError as exc:
        return {
            "status": "unavailable",
            "reason": f"actions.producers 模块缺席,inbox 预览缺席:{exc}",
            "suggestions": [],
        }
    suggestions: list[dict[str, Any]] = []
    for m in members:
        kid = m["kol_pool_id"]
        budget = m.get("budget") or {}
        p50 = _float_or_none(budget.get("usd_p50"))
        try:
            suggestions.append(
                make_suggestion(
                    category="longtail_wave_outreach",
                    dedupe_key=f"{wave_key}:{kid}",
                    title=f"长尾波次:@{m.get('handle') or kid} 送样+佣金码({sku})",
                    detail=(
                        f"{m.get('tier') or 'longtail'} 档 KOL(粉丝 {m.get('followers') or '未知'}),"
                        f"建议小额预算 ~${p50 if p50 is not None else '未知'}(rate_card {budget.get('method') or '无估价'});"
                        "动作包=送样+佣金码(佣金码待生成占位)。"
                    ),
                    reason=(
                        f"SKU {sku} 长尾铺量波次成员:报价升序优先的 nano/micro,"
                        "小成本换真实内容与佣金归因样本。"
                    ),
                    priority="low",
                    entity_type="kol_pool",
                    entity_id=str(kid),
                    suggested_endpoint=f"/api/admin/vkpi/kol-pool/{kid}/outreach-pack",
                    estimated_cost_cents=int(round(p50 * 100)) if p50 is not None else 0,
                    writes_business_data=True,   # 真执行会写外联/台账 → inbox 侧强制人审
                    uses_llm=False,
                    requires_approval=True,
                    payload={
                        "wave_key": wave_key,
                        "sku": sku,
                        "kol_pool_id": kid,
                        "budget_usd_p50": p50,
                        "action_pack": m.get("action_pack"),
                    },
                    expected_gain="小额送样置换真实内容;佣金码带来可归因转化样本",
                    risk_level="low",
                    evidence_refs=[{"type": "kol_pool", "id": str(kid)}],
                    verification_plan=["外联草稿生成且经人审", "签收后进入既有履约观察窗"],
                    affected_tables=["vkpi_action_inbox"],
                )
            )
        except Exception as exc:  # noqa: BLE001 — 单条建议拼装失败不拖垮整批
            logger.warning("longtail_wave suggestion build failed for kol=%s: %s", kid, exc)
    return {
        "status": "ready" if suggestions else "empty",
        "note": "预览态:未写入 vkpi_action_inbox;由人工确认后走 inbox 既有落库+审批链。",
        "count": len(suggestions),
        "suggestions": suggestions,
    }


# ── 主入口 ──────────────────────────────────────────────────────────


def longtail_wave(sku: str, count: int = COUNT_DEFAULT, budget_usd: float | None = None) -> dict[str, Any]:
    """长尾波次模板:50-100 个 nano/micro 名单 + 每人小额预算 + 动作包 + inbox 预览。

    dry_run 恒 True(零写库);SKU 不存在抛 LookupError(路由转 404);
    决定性:同输入同输出(全复用 strategy_sim 同源决定性部件)。
    """
    sku = _text(sku, 200)
    if not sku:
        raise LookupError("sku is required")
    count = _clamp_count(count)
    budget = _float_or_none(budget_usd)
    if budget is not None and budget <= 0:
        budget = None  # 非法预算按不限预算处理并如实标注

    # SKU 解析(与发射台/strategy_sim 同一真源;守卫 import)。
    try:
        from app.domains.projects import launch_assembly
    except ImportError as exc:
        raise LookupError(f"launch_assembly unavailable, cannot resolve sku: {exc}") from exc
    product = launch_assembly._resolve_product(sku)
    if not product:
        raise LookupError(f"SKU not found in vkpi_products: {sku}")
    product_basic = launch_assembly._product_basic(product)
    sku_code = _text(product.get("sku"), 120) or sku

    # strategy_sim 部件守卫装载(候选读数/长尾口径/贪心装包全复用)。
    try:
        from app.domains.market import strategy_sim
    except ImportError as exc:
        return {
            "status": "unavailable",
            "reason": f"strategy_sim 模块缺席,长尾波次无从选人:{exc}",
            "method": METHOD,
            "sku": sku_code,
            "generated_at": _utcnow_iso(),
        }

    pool_limit = max(strategy_sim.POOL_LIMIT, min(POOL_HARD_CAP, count * POOL_FACTOR))
    try:
        pool = launch_assembly._candidate_pool(sku_code, pool_limit)
    except Exception as exc:  # noqa: BLE001 — 候选池失败诚实降级
        logger.warning("longtail_wave candidate pool failed for sku=%s: %s", sku_code, exc)
        pool = {"status": "error", "reason": _text(str(exc), 300), "items": []}
    pool_items = pool.get("items") or []

    rate_card, _forecast_mod, _roster_mod, engines_missing = strategy_sim._load_engines()

    from app.db.connection import get_conn

    db = get_conn()
    # forecast 传 None:长尾选人只按报价升序(与 _pick_longtail 同键),
    # 刻意省掉逐人预测的读放大;expected_views 缺席在 note 里如实说。
    cands = strategy_sim._build_candidates(sku_code, pool_items, rate_card, None, db)

    # 长尾池:与 strategy_sim._pick_longtail 完全同键的决定性排序。
    ranked = sorted(
        (c for c in cands if c["cost_ready"] and c.get("tier") in strategy_sim.LONGTAIL_TIERS),
        key=lambda c: (c["cost_p50"] or 0.0, c["kol_pool_id"]),
    )
    if budget is not None:
        picked, spent = strategy_sim._greedy_pick(ranked, budget, count)
        selection_note = (
            f"预算 ${_usd(budget)} 内贪心装包(_greedy_pick 同款):报价 p50 升序,"
            f"长尾池 {len(ranked)} 人中选出 {len(picked)} 人(目标 {count})"
        )
    else:
        picked = ranked[:count]
        spent = sum(float(c["cost_p50"] or 0.0) for c in picked)
        selection_note = (
            f"不限预算:报价 p50 升序取前 {count},长尾池 {len(ranked)} 人中选出 {len(picked)} 人"
        )

    base = {
        "status": "ready",
        "method": METHOD,
        "sku": sku_code,
        "requested_sku": sku,
        "product": product_basic,
        "count_requested": count,
        "budget_usd": _usd(budget) if budget is not None else None,
        "dry_run": True,
        "generated_at": _utcnow_iso(),
        "llm_calls": False,
        "provider_calls": False,
        "note": (
            "长尾波次 dry_run 预览(零写库零外联零 LLM):名单/预算/动作包/inbox 建议"
            "全部是草稿供人审;真送样/真佣金码/真外联走执行层既有审批链。"
        ),
    }
    pool_block = {
        "status": _text(pool.get("status"), 30),
        "reason": _text(pool.get("reason"), 300),
        "pool_limit": pool_limit,
        "size": len(pool_items),
        "priceable_count": sum(1 for c in cands if c["cost_ready"]),
        "longtail_count": len(ranked),
        "basis": pool.get("basis") or {},
        "note": CONSISTENCY_NOTE,
    }
    if engines_missing:
        pool_block["engines_missing"] = engines_missing

    if not picked:
        return {
            **base,
            "status": "empty",
            "reason": (
                _text(pool.get("reason"), 200)
                or f"候选池 {len(pool_items)} 人中无可估价的 nano/micro 长尾候选 — 诚实空态,不硬凑名单。"
            ),
            "candidate_pool": pool_block,
            "members": [],
            "member_count": 0,
            "inbox_preview": {"status": "empty", "reason": "无成员,无建议可预览。", "suggestions": []},
        }

    wave_key = f"longtail_wave:{sku_code}:{count}:{_usd(budget) if budget is not None else 'nobudget'}"
    members: list[dict[str, Any]] = []
    for c in picked:
        members.append(
            {
                "kol_pool_id": c["kol_pool_id"],
                "handle": c["handle"],
                "display_name": c["display_name"],
                "platform": c["platform"],
                "country": c["country"],
                "tier": c.get("tier"),
                "followers": c.get("followers"),
                "match_score": c.get("match_score"),
                "budget": {
                    "usd_p50": c.get("cost_p50"),
                    "usd_low": c.get("cost_low"),
                    "usd_high": c.get("cost_high"),
                    "method": c.get("cost_method"),
                    "confidence": c.get("cost_confidence"),
                    "basis": "rate_card.estimate_rate(B件契约):真实报价中位数优先,无报价 CPM 基准兜底",
                },
                "action_pack": _action_pack(sku_code, c),
            }
        )

    cost_low = sum(float(c["cost_low"]) for c in picked if c.get("cost_low") is not None)
    cost_high = sum(float(c["cost_high"]) for c in picked if c.get("cost_high") is not None)
    estimate_only = all(c.get("cost_method") == "cpm_benchmark_v0" for c in picked)
    tier_counts: dict[str, int] = {}
    for c in picked:
        key = str(c.get("tier") or "unknown")
        tier_counts[key] = tier_counts.get(key, 0) + 1

    return {
        **base,
        "candidate_pool": pool_block,
        "member_count": len(members),
        "members": members,
        "totals": {
            "cost_usd_p50": _usd(spent),
            "cost_usd_low": _usd(cost_low),
            "cost_usd_high": _usd(cost_high),
            "avg_usd_p50_per_head": _usd(spent / len(members)) if members else None,
            "leftover_p50": _usd(budget - spent) if budget is not None else None,
            "tier_counts": dict(sorted(tier_counts.items())),
        },
        "estimate_only": estimate_only,
        "estimate_only_warning": (
            "全员报价均为 CPM 行业基准兜底(cpm_benchmark_v0,无一条真实报价)——"
            "预算数字仅是谈判锚点非成交价(对齐第4轮护栏),签约前务必录入真实报价复算。"
            if estimate_only else None
        ),
        "shortfall_note": (
            f"目标 {count} 人只选出 {len(picked)} 人(长尾候选或预算不足)— 诚实如实,不硬凑。"
            if len(picked) < count else None
        ),
        "inbox_preview": _inbox_suggestions(sku_code, members, wave_key),
        "basis": {
            "selection_rule": selection_note,
            "consistency": CONSISTENCY_NOTE,
            "engines": {
                "pool": "launch_assembly._candidate_pool(new_launch_match 决定性 dry-run)",
                "cost": "rate_card.estimate_rate(经 strategy_sim._build_candidates 逐人挂载)",
                "forecast": "刻意未跑(长尾选人不依赖预测;省读放大),expected_views 缺席如实",
            },
            "commission_code": "恒为 pending_generation 占位;goaffpro 真生成留给执行层。",
        },
    }
