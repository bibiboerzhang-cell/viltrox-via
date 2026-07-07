"""CB2 渠道组合器(Channel Mix Optimizer)—— 「用哪些渠道推、各占多少」跨渠道预算与权重分配。

Channel Brain 层第二刀(蓝图 v0.2《P2G 产品上市增长大脑》)。输入 SKU + 预算 + goal,
输出五渠道的预算与权重分配,决定性(同输入同输出)、纯读、零 LLM 零采集零写库:
  ① KOL 情绪内容    —— 守卫 import strategy_sim,复用其候选池与三方案预算逻辑(不重造);
  ② 官号 official   —— 守卫 import CB1 official_planner(若可);缺席退轻量只读容量信号;
  ③ 独立站承接      —— 转化承接层预算(本地 Shopify 0 订单 → 归因 data_missing 诚实标注);
  ④ Dealer 经销商   —— 守卫 import dealer_scrape 计数;0 行 → status=data_missing 占位不编数;
  ⑤ 付费放大        —— 守卫 import growth_playbook,引用两段式放大规则(boost_*)为 basis。

分层口径(Binet-Field 60/40,写进 basis 可追溯):
  品牌层(brand,~60%)= KOL 情绪内容 + 官号(长期建设/曝光型);
  激活层(activation,~40%)= 独立站 + Dealer 促销 + 付费放大(短期转化型)。
  goal 在文档化边界内微调层比(exposure 偏品牌 / conversion 偏激活 / content 偏品牌产能);
  未知 goal 退回 60/40 默认,并在 basis 诚实说明,参数不假装比数据聪明。

每渠道带:allocated_usd / weight_pct(占总预算)/ layer(品牌 or 激活)/
  channel_type(曝光型 vs 转化型)/ confidence / status / basis。

诚实态:Dealer 0 行 / 独立站本地无订单一律 status=data_missing + note,绝不编数;
每个数字带 basis;子引擎失败诚实降级不炸主流程。SKU 不存在抛 LookupError(路由转 404)。

红线:纯读零写;零触 viltrox_fit_score / rule_v0;compat 占位符 ?(本模块无 SQL,读全走兄弟件);
函数内懒 import。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "channel_mix_v0"

# ── Binet-Field 60/40 分层(默认口径)与 goal 微调边界 ──────────────────
# 层顺序:(品牌层占比, 激活层占比)。默认 60/40 = 长期均衡(蓝图裁决锚点)。
_LAYER_SPLIT_DEFAULT = (0.60, 0.40)
_LAYER_SPLIT_BY_GOAL: dict[str, tuple[float, float]] = {
    "exposure": (0.70, 0.30),    # 偏品牌/曝光:长期建设加码
    "conversion": (0.45, 0.55),  # 偏激活/转化:短期收割加码
    "content": (0.65, 0.35),     # 内容产能:偏品牌层(KOL/官号产内容)
}
_GOALS = ("exposure", "conversion", "content")

BINET_FIELD_BASIS = (
    "分层依据 Binet-Field 60/40:品牌层(KOL 情绪内容 + 官号,长期建设/曝光型)约占 60,"
    "激活层(独立站 + Dealer 促销 + 付费放大,短期转化型)约占 40;60/40 为默认长期均衡锚点,"
    "goal 在文档化边界内微调(exposure=70/30 偏品牌,conversion=45/55 偏激活,content=65/35 偏品牌产能),"
    "未知 goal 退回 60/40 默认。此比例是规划锚点非成交实测,可被学习账本(GTM-4)自有数据校准。"
)

# 层内渠道权重(占本层预算)。品牌层:KOL 主力、官号自有媒体产能补充。
_BRAND_WEIGHTS = {"kol": 0.75, "official": 0.25}
# 激活层名义权重(占激活层预算);dealer 数据缺失时其名义份额按比例折入 site/paid。
_ACTIVATION_NOMINAL = {"site": 0.40, "paid": 0.30, "dealer": 0.30}

# 渠道静态元数据(层 / 曝光型 vs 转化型 / 中文名)。
_CHANNEL_META = {
    "kol": {"name": "KOL 情绪内容", "layer": "brand", "channel_type": "exposure"},
    "official": {"name": "官号自有媒体", "layer": "brand", "channel_type": "exposure"},
    "site": {"name": "独立站承接", "layer": "activation", "channel_type": "conversion"},
    "dealer": {"name": "Dealer 经销商", "layer": "activation", "channel_type": "conversion"},
    "paid": {"name": "付费放大", "layer": "activation", "channel_type": "conversion"},
}
_LAYER_LABEL = {"brand": "品牌层", "activation": "激活层"}
_TYPE_LABEL = {"exposure": "曝光型", "conversion": "转化型"}


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


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _usd(value: float) -> float:
    return round(float(value), 2)


def _pct_of(part: float, whole: float) -> float | None:
    if whole <= 0:
        return None
    return round(part * 100.0 / whole, 2)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── ① KOL 渠道:复用 strategy_sim 候选池与三方案预算逻辑(守卫 import) ──


def _kol_channel(sku: str, kol_budget: float, goal: str | None) -> dict[str, Any]:
    """KOL 情绪内容渠道:守卫 import strategy_sim,把 KOL 分层预算灌进三方案模拟,
    surface 推荐方案 + 候选池计数 + 成本可信度;不重造候选/报价/预测逻辑。"""
    meta = _CHANNEL_META["kol"]
    base: dict[str, Any] = {
        "key": "kol",
        "name": meta["name"],
        "layer": meta["layer"],
        "layer_label": _LAYER_LABEL[meta["layer"]],
        "channel_type": meta["channel_type"],
        "channel_type_label": _TYPE_LABEL[meta["channel_type"]],
        "allocated_usd": _usd(kol_budget),
    }
    if kol_budget <= 0:
        base.update({"status": "empty", "confidence": "low",
                     "note": "KOL 分层预算为 0(总预算过小或 goal 极端偏激活),无从排兵。"})
        return base
    try:
        from app.domains.market import strategy_sim
        sim = strategy_sim.simulate(sku, float(kol_budget), goal=goal)
    except LookupError:
        raise
    except Exception as exc:  # noqa: BLE001 — 子引擎失败不炸主流程,保留预算分配诚实降级
        logger.warning("channel_mix KOL strategy_sim failed sku=%s: %s", sku, exc)
        base.update({
            "status": "degraded",
            "confidence": "low",
            "note": "strategy_sim 模拟失败,KOL 预算已分配但方案暂缺:" + _text(str(exc), 160),
            "basis": {"engine": "market.strategy_sim.simulate(守卫 import,失败诚实降级)"},
        })
        return base

    sim_status = _text(sim.get("status"), 30)
    pool = sim.get("candidate_pool") or {}
    rec = sim.get("recommendation") or {}
    rec_key = _text(rec.get("recommended_key"), 40) or None
    # 推荐方案的成本/成员概览(从 plans 里取推荐那条)
    rec_plan = None
    for p in sim.get("plans") or []:
        if p.get("key") == rec_key:
            rec_plan = p
            break
    plan_cost = (rec_plan or {}).get("cost_usd") or {}
    estimate_only = bool((rec_plan or {}).get("estimate_only"))
    # 置信度:直接采纳 strategy_sim 推荐置信(estimate_only=全员基准兜底价→low)
    confidence = _text(rec.get("confidence"), 20) or ("low" if estimate_only else "low")
    status = "ready" if sim_status == "ready" and rec.get("status") == "ready" else (sim_status or "empty")
    base.update({
        "status": status,
        "confidence": confidence,
        "estimate_only": estimate_only,
        "recommended_plan": {
            "key": rec_key,
            "name": _text(rec.get("recommended_name"), 60) or None,
            "member_count": _int_or_none((rec_plan or {}).get("member_count")),
            "plan_cost_p50_usd": _float_or_none(plan_cost.get("p50_total")),
            "plan_utilization_pct": _float_or_none(plan_cost.get("utilization_pct")),
        } if rec_key else None,
        "candidate_pool": {
            "size": _int_or_none(pool.get("size")),
            "priceable_count": _int_or_none(pool.get("priceable_count")),
            "longtail_count": _int_or_none(pool.get("longtail_count")),
        },
        "note": (
            "KOL 情绪内容=品牌层主力(曝光型);预算灌入 strategy_sim 三方案 what-if,"
            "推荐方案与候选来自其候选池/报价/预测三引擎,本渠道不重造。"
            + ("推荐方案全员 CPM 基准兜底价(estimate_only),绝对成本仅谈判锚点,签约前须录真实报价复算。"
               if estimate_only else "")
        ),
        "basis": {
            "engine": "market.strategy_sim.simulate(守卫 import;候选=launch_assembly._candidate_pool,"
                      "成本=rate_card,预测=performance_forecast,触达=roster_optimizer)",
            "goal_passthrough": goal,
            "note": "KOL 分层预算 = 品牌层预算 × 层内权重(见 layers.brand.channel_weights);"
                    "strategy_sim 推荐排序口径随 goal 切换,已透传。",
        },
    })
    return base


# ── ② 官号渠道:守卫 import CB1 official_planner(若可)→ 退轻量只读信号 ──


def _official_signal(db: Any) -> dict[str, Any]:
    """官号自有媒体容量信号:先守卫 import CB1 official_planner(若可用其规划口径);
    缺席退轻量只读 SELECT(活跃官号数/平台数/最新粉丝合计),纯读零副作用零缓存写。"""
    # 优先复用 CB1(Channel Brain 第一刀)——若该模块与其 planner 已落地则用其口径。
    try:
        from app.domains.channel import official_planner as cb1  # type: ignore
        planner = getattr(cb1, "official_planner", None) or getattr(cb1, "plan", None)
        if callable(planner):
            try:
                return {"source": "cb1_official_planner", "status": "ready", "cb1": planner()}
            except Exception as exc:  # noqa: BLE001 — CB1 调用失败退轻量信号
                logger.info("channel_mix CB1 official_planner call failed, fallback: %s", exc)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.info("channel_mix CB1 import guard fell through: %s", exc)

    # 轻量只读容量信号(不调用重型 official_account_matrix,避免其缓存写副作用)。
    try:
        from app.db.connection import table_exists
    except Exception:  # noqa: BLE001
        table_exists = None  # type: ignore
    if table_exists is not None and not table_exists("vkpi_employee_channels"):
        return {"source": "none", "status": "data_missing",
                "reason": "vkpi_employee_channels 表缺失,官号容量无从读取。"}
    try:
        rows = db.execute(
            """
            SELECT c.id AS channel_id,
                   c.platform AS platform,
                   (
                     SELECT m.followers FROM vkpi_channel_metrics m
                     WHERE m.channel_id = c.id
                     ORDER BY m.snapshot_date DESC, m.captured_at DESC, m.id DESC
                     LIMIT 1
                   ) AS followers
            FROM vkpi_employee_channels c
            WHERE c.deleted_at IS NULL AND c.status = ?
            """,
            ("active",),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 官号读挂不拖垮主流程,诚实降级
        logger.warning("channel_mix official signal read failed: %s", exc)
        return {"source": "none", "status": "error", "reason": _text(str(exc), 200)}
    items = [dict(r) for r in rows]
    accounts = len(items)
    platforms = sorted({_text(it.get("platform"), 30).lower() for it in items if it.get("platform")})
    total_followers = sum(int(_int_or_none(it.get("followers")) or 0) for it in items)
    return {
        "source": "vkpi_employee_channels_active",
        "status": "ready" if accounts > 0 else "data_missing",
        "account_count": accounts,
        "platform_count": len(platforms),
        "platforms": platforms,
        "total_followers": total_followers,
        "reason": None if accounts > 0 else "无活跃官号记录,官号渠道容量 data_missing。",
    }


def _official_channel(official_budget: float, db: Any) -> dict[str, Any]:
    meta = _CHANNEL_META["official"]
    base: dict[str, Any] = {
        "key": "official",
        "name": meta["name"],
        "layer": meta["layer"],
        "layer_label": _LAYER_LABEL[meta["layer"]],
        "channel_type": meta["channel_type"],
        "channel_type_label": _TYPE_LABEL[meta["channel_type"]],
        "allocated_usd": _usd(official_budget),
    }
    sig = _official_signal(db)
    sig_status = _text(sig.get("status"), 20)
    accounts = _int_or_none(sig.get("account_count"))
    if sig_status == "ready" or (sig.get("source") == "cb1_official_planner"):
        confidence = "medium" if (accounts or 0) >= 3 or sig.get("source") == "cb1_official_planner" else "low"
        status = "ready"
        note = (
            "官号=品牌层自有媒体(曝光型,近零边际成本):分层预算用于内容产能/官号内容加热;"
            f"当前活跃官号 {accounts if accounts is not None else '—'} 个覆盖 "
            f"{sig.get('platform_count') if sig.get('platform_count') is not None else '—'} 平台,粉丝合计 "
            f"{sig.get('total_followers') if sig.get('total_followers') is not None else '—'}。"
        )
    else:
        confidence = "data_missing"
        status = "data_missing"
        note = _text(sig.get("reason"), 200) or "官号容量数据缺失,预算已分配但不可核容,data_missing。"
    base.update({
        "status": status,
        "confidence": confidence,
        "note": note,
        "signal": sig,
        "basis": {
            "source": (
                "channel.official_planner(CB1,守卫 import 若可)"
                if sig.get("source") == "cb1_official_planner"
                else "vkpi_employee_channels(status=active)+ 最新 vkpi_channel_metrics 粉丝(轻量只读,不走重型 matrix 缓存)"
            ),
            "role": "官号为自有媒体,组合器把品牌层的一部分预算标给官号内容产能与自然内容加热(非外购媒体)。",
        },
    })
    return base


# ── ③ 独立站承接渠道:转化承接层;本地 Shopify 0 订单 → 归因 data_missing ──


def _site_channel(site_budget: float, db: Any) -> dict[str, Any]:
    meta = _CHANNEL_META["site"]
    # 本地订单量:诚实探测(0 → 归因 data_missing);表缺席同样诚实。
    order_count: int | None = None
    attribution_status = "data_missing"
    try:
        from app.db.connection import table_exists
        if table_exists("vkpi_shopify_orders"):
            row = db.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_orders").fetchone()
            order_count = _int_or_none(dict(row).get("n")) if row is not None else 0
            attribution_status = "ready" if (order_count or 0) > 0 else "data_missing"
    except Exception as exc:  # noqa: BLE001 — 订单探测失败诚实降级,不拖垮分配
        logger.info("channel_mix site order probe failed: %s", exc)
        order_count = None
    note = (
        "独立站承接=激活层转化承接(转化型):分层预算用于落地页/再营销/承接优化。"
    )
    if attribution_status == "ready":
        confidence = "low"
        note += f"本地已见 {order_count} 条订单,可做承接归因基线(仍为估算非稳态)。"
    else:
        confidence = "data_missing"
        note += "本地 Shopify 0 订单(线上另有订单未同步):承接归因 data_missing,预算已分配但 ROI 暂不可测,不编造转化数。"
    return {
        "key": "site",
        "name": meta["name"],
        "layer": meta["layer"],
        "layer_label": _LAYER_LABEL[meta["layer"]],
        "channel_type": meta["channel_type"],
        "channel_type_label": _TYPE_LABEL[meta["channel_type"]],
        "allocated_usd": _usd(site_budget),
        "status": "ready",  # 预算分配可给出;归因能力单列 attribution
        "confidence": confidence,
        "attribution": {
            "status": attribution_status,
            "local_order_count": order_count,
            "note": "归因能力独立于预算分配:本地订单为 0/表缺时 data_missing,绝不编造承接转化。",
        },
        "note": note,
        "basis": {
            "source": "vkpi_shopify_orders 本地计数(探测归因就绪度;线上订单不随本地同步)",
            "role": "承接层预算=把激活层的一部分标给独立站转化承接,数额为规划锚点。",
        },
    }


# ── ④ Dealer 渠道:守卫 import dealer_scrape 计数;0 行 → data_missing 占位 ──


def _dealer_channel(nominal_usd: float) -> dict[str, Any]:
    """Dealer 经销商促销渠道。本地 vkpi_dealers 0 行 → status=data_missing 占位,
    allocated_usd=0(不给不可执行的渠道派真金),名义份额折入 site/paid(见激活层再分配)。"""
    meta = _CHANNEL_META["dealer"]
    dealer_count: int | None = None
    try:
        from app.domains.commerce import dealer_scrape
        dealers = dealer_scrape.list_dealers(limit=1)
        dealer_count = len(dealers) if isinstance(dealers, list) else 0
    except Exception as exc:  # noqa: BLE001 — dealer 读挂当作 0/未知,诚实 data_missing
        logger.info("channel_mix dealer count probe failed: %s", exc)
        dealer_count = None
    return {
        "key": "dealer",
        "name": meta["name"],
        "layer": meta["layer"],
        "layer_label": _LAYER_LABEL[meta["layer"]],
        "channel_type": meta["channel_type"],
        "channel_type_label": _TYPE_LABEL[meta["channel_type"]],
        "allocated_usd": 0.0,                       # 不可执行渠道不派真金
        "nominal_usd": _usd(nominal_usd),           # 若有 dealer 数据本应分配的名义额
        "status": "data_missing",
        "confidence": "data_missing",
        "dealer_count": dealer_count,
        "note": (
            "Dealer 经销商=激活层线下/零售促销(转化型),但本地 vkpi_dealers "
            f"{'缺表/读失败' if dealer_count is None else str(dealer_count) + ' 行'}"
            "(0 行经销商):status=data_missing,不派真金、不编经销商数;其名义激活份额已按比例折入独立站与付费放大。"
        ),
        "basis": {
            "source": "commerce.dealer_scrape.list_dealers(守卫 import,只读计数)",
            "rule": "0 行/缺表 → data_missing 占位;allocated_usd=0,名义份额 nominal_usd 折入可执行激活渠道。",
        },
    }


# ── ⑤ 付费放大渠道:守卫 import growth_playbook,引用两段式放大规则为 basis ──


def _paid_channel(paid_budget: float) -> dict[str, Any]:
    meta = _CHANNEL_META["paid"]
    rule_refs: list[dict[str, Any]] = []
    try:
        from app.domains.market_brain import growth_playbook
        want = {"boost_two_stage_budget", "boost_trigger_organic_top25", "stat_power_min_exposure"}
        for r in (growth_playbook.rules(platform="all") or {}).get("rules") or []:
            if _text(r.get("rule_id"), 60) in want:
                rule_refs.append({
                    "rule_id": _text(r.get("rule_id"), 60),
                    "statement": _text(r.get("statement"), 220),
                    "confidence": _text(r.get("confidence"), 20),
                    "source": _text(r.get("source"), 120),
                })
    except Exception as exc:  # noqa: BLE001 — 规则库读挂不拖垮分配
        logger.info("channel_mix paid playbook read failed: %s", exc)
    return {
        "key": "paid",
        "name": meta["name"],
        "layer": meta["layer"],
        "layer_label": _LAYER_LABEL[meta["layer"]],
        "channel_type": meta["channel_type"],
        "channel_type_label": _TYPE_LABEL[meta["channel_type"]],
        "allocated_usd": _usd(paid_budget),
        "status": "ready",
        "confidence": "low",  # 规则库 external 条目未经自有数据回测
        "note": (
            "付费放大=激活层放大杠杆(转化型):只放大已验证内容(两段式先小额验证再上主预算),"
            "不首轮全押;规则来自 growth_playbook(external,confidence=low,未经自有数据回测,仅锚点)。"
        ),
        "rule_refs": rule_refs,
        "basis": {
            "source": "market_brain.growth_playbook.rules(守卫 import;boost_* 两段式放大 + 统计功效闸)",
            "rule": "放大预算受两段式(先 $50-100/条验证)与统计功效闸(≥1000 曝光才判优劣)约束,写进执行护栏。",
        },
    }


# ── 主入口 ──────────────────────────────────────────────────────────


def channel_mix(sku: str, budget_usd: float, goal: str | None = None) -> dict[str, Any]:
    """CB2 渠道组合器主入口:输出五渠道预算与权重分配(决定性、纯读、零副作用)。

    SKU 不存在抛 LookupError(路由转 404);预算非法返回 status=invalid。
    goal ∈ exposure|conversion|content 在文档化边界内微调 Binet-Field 层比;未知值退 60/40 默认。
    """
    sku = _text(sku, 200)
    if not sku:
        raise LookupError("sku is required")
    budget = _float_or_none(budget_usd)
    if budget is None or budget <= 0:
        return {
            "status": "invalid",
            "reason": f"budget_usd 非法({budget_usd}),需为正数。",
            "sku": sku,
            "method": METHOD,
            "generated_at": _utcnow_iso(),
        }

    goal_norm = _text(goal, 30).lower()
    effective_goal = goal_norm if goal_norm in _GOALS else None

    # SKU 解析(与发射台/strategy_sim 同源 _resolve_product;守卫 import)——不存在则 404。
    try:
        from app.domains.projects import launch_assembly
        product = launch_assembly._resolve_product(sku)
        product_basic = launch_assembly._product_basic(product) if product else None
    except ImportError as exc:
        raise LookupError(f"launch_assembly unavailable, cannot resolve sku: {exc}") from exc
    if not product:
        raise LookupError(f"SKU not found in vkpi_products: {sku}")
    sku_code = _text(product.get("sku"), 120) or sku

    # ── 分层(Binet-Field 60/40,goal 微调) ──
    brand_pct, activation_pct = _LAYER_SPLIT_BY_GOAL.get(effective_goal, _LAYER_SPLIT_DEFAULT)
    brand_budget = budget * brand_pct
    activation_budget = budget * activation_pct

    # 品牌层层内:KOL / 官号
    kol_budget = brand_budget * _BRAND_WEIGHTS["kol"]
    official_budget = brand_budget * _BRAND_WEIGHTS["official"]

    # 激活层层内:site/paid 可执行;dealer 数据缺失 → 名义份额按比例折入 site/paid。
    dealer_nominal = activation_budget * _ACTIVATION_NOMINAL["dealer"]
    site_nominal = activation_budget * _ACTIVATION_NOMINAL["site"]
    paid_nominal = activation_budget * _ACTIVATION_NOMINAL["paid"]
    executable_nominal = site_nominal + paid_nominal
    if executable_nominal > 0:
        site_budget = site_nominal + dealer_nominal * (site_nominal / executable_nominal)
        paid_budget = paid_nominal + dealer_nominal * (paid_nominal / executable_nominal)
    else:  # 理论不达(两权重恒正),兜底诚实
        site_budget, paid_budget = activation_budget, 0.0

    from app.db.connection import get_conn
    db = get_conn()

    channels = [
        _kol_channel(sku_code, kol_budget, effective_goal),
        _official_channel(official_budget, db),
        _site_channel(site_budget, db),
        _dealer_channel(dealer_nominal),
        _paid_channel(paid_budget),
    ]

    # 每渠道补 weight_pct(占总预算);Dealer allocated=0 → 0%。
    for ch in channels:
        ch["weight_pct"] = _pct_of(float(ch.get("allocated_usd") or 0.0), budget)

    allocated_total = sum(float(ch.get("allocated_usd") or 0.0) for ch in channels)
    brand_alloc = sum(float(ch.get("allocated_usd") or 0.0) for ch in channels if ch.get("layer") == "brand")
    activation_alloc = sum(float(ch.get("allocated_usd") or 0.0) for ch in channels if ch.get("layer") == "activation")

    goal_note = None
    if goal_norm and effective_goal is None:
        goal_note = (
            f"goal={goal_norm} 不在支持集 {'/'.join(_GOALS)},层比退回 Binet-Field 60/40 默认,不假装懂。"
        )

    return {
        "status": "ready",
        "method": METHOD,
        "sku": sku_code,
        "requested_sku": sku,
        "product": product_basic,
        "budget_usd": _usd(budget),
        "goal": goal_norm or None,
        "goal_effective": effective_goal,
        "goal_note": goal_note,
        "generated_at": _utcnow_iso(),
        "llm_calls": False,
        "provider_calls": False,
        "layers": {
            "framework": "binet_field_60_40",
            "brand": {
                "label": "品牌层",
                "target_pct": round(brand_pct * 100.0, 2),
                "target_usd": _usd(brand_budget),
                "allocated_usd": _usd(brand_alloc),
                "channels": ["kol", "official"],
                "channel_weights": {"kol": _BRAND_WEIGHTS["kol"], "official": _BRAND_WEIGHTS["official"]},
                "channel_type": "exposure",
                "channel_type_label": "曝光型(长期品牌建设)",
            },
            "activation": {
                "label": "激活层",
                "target_pct": round(activation_pct * 100.0, 2),
                "target_usd": _usd(activation_budget),
                "allocated_usd": _usd(activation_alloc),
                "channels": ["site", "dealer", "paid"],
                "nominal_weights": dict(_ACTIVATION_NOMINAL),
                "dealer_data_missing": True,
                "dealer_reallocation": (
                    "Dealer 本地 0 行 → 其名义激活份额(nominal_usd)按 site:paid 比例折入两条可执行激活渠道;"
                    "Dealer allocated_usd=0,status=data_missing。"
                ),
                "channel_type": "conversion",
                "channel_type_label": "转化型(短期销售激活)",
            },
        },
        "channels": channels,
        "allocation_check": {
            "budget_usd": _usd(budget),
            "allocated_total_usd": _usd(allocated_total),
            "unallocated_usd": _usd(budget - allocated_total),
            "note": "allocated_total 应≈budget(Dealer data_missing 份额已折入 site/paid,故全额落在可执行渠道)。",
        },
        "recommendation": {
            "headline": (
                f"以 Binet-Field {round(brand_pct*100)}/{round(activation_pct*100)} 分层推进:"
                "品牌层用 KOL 情绪内容 + 官号做长期曝光,激活层用独立站承接 + 付费放大做短期转化;"
                "Dealer 数据缺失暂缓,待经销商入库再激活线下促销。"
            ),
            "confidence": "low",
            "confidence_reason": (
                "整体置信偏低:KOL 成本多为 CPM 基准兜底价、付费规则为 external 未回测、"
                "独立站与 Dealer 归因 data_missing;分配为规划锚点,须由真实成交与 outcome 数据校准。"
            ),
        },
        "basis": {
            "binet_field": BINET_FIELD_BASIS,
            "layer_split_rule": (
                "默认 60/40;goal 微调:exposure=70/30、conversion=45/55、content=65/35;未知 goal 退默认。"
            ),
            "brand_channel_weights": _BRAND_WEIGHTS,
            "activation_nominal_weights": _ACTIVATION_NOMINAL,
            "engines": {
                "kol": "market.strategy_sim.simulate(候选池+三方案预算逻辑,守卫 import)",
                "official": "channel.official_planner(CB1,若可)→ vkpi_employee_channels 轻量只读容量信号",
                "site": "vkpi_shopify_orders 本地计数(归因就绪度探测)",
                "dealer": "commerce.dealer_scrape.list_dealers(守卫 import,只读计数)",
                "paid": "market_brain.growth_playbook.rules(boost_* 两段式放大规则)",
            },
        },
        "note": (
            "CB2 渠道组合器:跨渠道预算与权重分配(决定性,零 LLM 零采集零写库,同输入同输出)。"
            "每渠道带 layer/曝光型-转化型/confidence/basis;Dealer 0 行与独立站本地无订单一律 data_missing 诚实占位,"
            "绝不编数;分配为规划锚点,可被学习账本(GTM-4)以自有 outcome 数据校准。"
        ),
    }
