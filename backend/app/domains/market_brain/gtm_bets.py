"""GTM-Loop L1:bet 合约构建器(preview 的 action_inbox_items 段,纯函数零写库)。

闭环波规格第一章:每条建议升级为 bet 七要素——
why(预判引用,带 gtm_plan_id/forecast 段落锚)/ what+who / expected(量化)/
cost+risk / escalate_if / retreat_if / review_at(按 action_type 默认复盘天数)。

本模块只构造 dict,绝不落库;真落库走 materialize.py
(producers.make_suggestion + inbox.persist_suggestions,requires_approval=True 人审红线)。

红线:
- 零写库、零 LLM、零采集(与 gtm_plan_preview 同一条零副作用红线);
- 绝不写 viltrox_fit_score / rule_v0;
- 预判措辞条件化:escalate_if / retreat_if 双线齐才出货,绝对化词根命中即丢弃该条;
- 裁决(verdict)绝不在这里发生——bet 只带 review_at 复盘钩,裁决只走人工 POST(L2)。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

BET_CONTRACT_VERSION = "bet_v1"
MAX_BETS = 30

# review_at 默认复盘天数(规格:7d 默认,类型可调;归因链路长的动作放宽到 14d)
BET_REVIEW_DAYS: dict[str, int] = {
    "kol_outreach": 7,
    "official_post": 7,
    "budget_allocation": 7,
    "content_brief": 7,
    "indie_site_update": 14,
}
DEFAULT_BET_REVIEW_DAYS = 7

# forecast 段缺席时的触发/撤退线兜底(与 gtm_plan_preview 规则库口径一致,条件化措辞)
_FALLBACK_ESCALATE_7D = (
    "首周外联有效回复率 ≥10%(规则库 outreach_reply_floor)且 ≥2 位候选进入排期 → 追加下一批外联并预约官号协同位。"
)
_FALLBACK_RETREAT_7D = "10 人外联样本有效回复率 <5% 或零候选进排期 → 收缩到官号内容路线,复核选人口径与触达渠道后再试。"
_FALLBACK_ESCALATE_14D = (
    "48h 内内容满足 2s hook 留存 ≥40%(规则库 hook_2s_retention_a)或 TikTok 完播 ≥70%,"
    "且 ER 高于账号 30 天基线 → 进放大清单。"
)
_FALLBACK_RETREAT_14D = "内容 ER 持续低于账号 30 天基线,或评论集中质疑价格/兼容 → 停止加码,换角度并把样本喂回复盘。"
_FALLBACK_ESCALATE_30D = (
    "素材变体按 ≥1000 曝光判胜负(规则库 per_variant_min_impressions),胜出变体 CTR ≥4% 且留存 ≥40%,"
    "且短链归因效率优于行业锚点 → 升激进档并铺佣金码。"
)
_FALLBACK_RETREAT_30D = "30 天内无可归因转化(短链点击+落地页行为代理),或 CPM 效率持续差于锚点 → 回退保守 70/10/20 档。"


# ── 小工具(与 gtm_plan_preview 同款语义的本地副本,避免 import 纠缠) ──


def _text(value: Any, limit: int = 300) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usd(value: Any) -> float | None:
    try:
        return round(float(value), 2) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _fmt_int(value: Any) -> str:
    return f"{_int0(value):,}"


def _slug(value: Any) -> str:
    return hashlib.sha1(_text(value, 300).encode("utf-8")).hexdigest()[:8]


def gtm_plan_id_for(sku: str, goal: str, on_date: str | None = None) -> str:
    """确定性 plan id:sha1(sku|UTC日期|goal) 前 12 位。同日同输入同 id(幂等锚)。"""
    day = _text(on_date, 20) or datetime.now(timezone.utc).date().isoformat()
    digest = hashlib.sha1(f"{_text(sku, 120)}|{day}|{_text(goal, 20)}".encode("utf-8")).hexdigest()[:12]
    return f"gtmp_{digest}"


def review_at_for(action_type: str, now: datetime | None = None) -> tuple[str, int]:
    """按 action_type 取默认复盘天数 → (review_at ISO 日期, 天数)。"""
    days = BET_REVIEW_DAYS.get(_text(action_type, 40), DEFAULT_BET_REVIEW_DAYS)
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(days=days)).date().isoformat(), days


def _metric(metric: str, op: str, target: Any, unit: str, basis: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"metric": metric, "op": op, "target": target, "unit": unit}
    if basis:
        out["basis"] = basis
    return out


def _mk_bet(
    *,
    action_type: str,
    bet_key: str,
    gtm_plan_id: str,
    why_statement: str,
    plan_section: str,
    plan_ref: str,
    forecast_anchor: str,
    what: str,
    who: str,
    expected_statement: str,
    metrics: list[dict[str, Any]],
    cost_statement: str,
    cost_usd_p50: Any,
    risk: str,
    risk_level: str,
    escalate_if: str,
    retreat_if: str,
    entity_type: str,
    entity_id: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """bet 七要素合约(全条件化措辞;requires_approval 恒 True,materialize 落库后走人审)。"""
    review_at, review_days = review_at_for(action_type, now)
    return {
        "contract": BET_CONTRACT_VERSION,
        "action_type": _text(action_type, 40),
        "bet_key": _text(bet_key, 120),
        # 1) why:预判引用,带 gtm_plan_id 与段落锚
        "why": {
            "statement": _text(why_statement, 400),
            "plan_anchor": {
                "gtm_plan_id": gtm_plan_id,
                "section": _text(plan_section, 60),
                "ref": _text(plan_ref, 160),
                "forecast_anchor": _text(forecast_anchor, 60),
            },
        },
        # 2) what / who
        "what": _text(what, 300),
        "who": _text(who, 200),
        # 3) expected:量化预期(达没达到由三窗对答案+人工裁决判定,非承诺)
        "expected": {"statement": _text(expected_statement, 400), "metrics": metrics},
        # 4) cost / risk
        "cost": {"statement": _text(cost_statement, 240), "usd_p50": _usd(cost_usd_p50)},
        "risk": _text(risk, 300) or "无标签",
        "risk_level": risk_level if risk_level in ("low", "medium", "high") else "medium",
        # 5) 加码线
        "escalate_if": _text(escalate_if, 500),
        # 6) 撤退线
        "retreat_if": _text(retreat_if, 500),
        # 7) 复盘钩(review_at 到期 → L2 生成裁决任务;裁决只人工,无自动路径)
        "review_at": review_at,
        "review_days": review_days,
        "requires_approval": True,
        # 落库主体锚(materialize 用作 entity_type/entity_id 与 evidence_refs)
        "subject": {"entity_type": _text(entity_type, 40), "entity_id": _text(entity_id, 80)},
    }


def _item(
    *,
    action: str,
    reason: str,
    evidence_summary: str,
    cost_note: str,
    risk: str,
    expected_gain: str,
    bet: dict[str, Any],
) -> dict[str, Any]:
    """preview 条目:既有六要素键位保持不变(既有冒烟兼容),bet 合约作为新增键并列。"""
    return {
        "action": _text(action, 300),
        "reason": _text(reason, 400),
        "evidence_summary": _text(evidence_summary, 400),
        "cost_note": _text(cost_note, 240),
        "risk": _text(risk, 300) or "无标签",
        "expected_gain": _text(expected_gain, 300),
        "review": {"enabled": False, "note": "materialize 落库后在 Action Inbox 人审(requires_approval=True)"},
        "bet": bet,
    }


def _fc_lines(forecast: Any, horizon: int, esc_default: str, ret_default: str) -> tuple[str, str]:
    """从 preview forecast 段取该窗触发/撤退线(段落已过措辞纪律);缺席用规则库兜底。"""
    if isinstance(forecast, list):
        for entry in forecast:
            if isinstance(entry, dict) and _int0(entry.get("horizon_days")) == horizon:
                esc = _text(entry.get("escalate_if"), 500)
                ret = _text(entry.get("retreat_if"), 500)
                if esc and ret:
                    return esc, ret
    return esc_default, ret_default


# ── 各来源 → bet 条目 ────────────────────────────────────────────────


def _kol_outreach_items(*, sku: str, goal: str, plan_id: str, kol_section: dict[str, Any],
                        esc: str, ret: str, now: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for cand in (kol_section.get("items") or []):
        kid = cand.get("kol_pool_id")
        handle = _text(cand.get("handle") or cand.get("display_name"), 100)
        platform = _text(cand.get("platform"), 30)
        cost = cand.get("cost_usd_p50")
        views = cand.get("expected_views_p50")
        risk = ";".join(cand.get("risk_labels") or []) or "无标签"
        expected_stmt = (
            f"{DEFAULT_BET_REVIEW_DAYS} 天内收到有效回复并进入排期;"
            + (f"首条内容预测播放 p50 约 {_fmt_int(views)}(逐人口径,非承诺)。" if views is not None
               else "预测缺样本,播放预期待首条内容校准。")
        )
        metrics = [_metric("reply_rate", ">=", 0.10, "reply_rate", "规则库 outreach_reply_floor(自建基线)"),
                   _metric("scheduled_candidates", ">=", 1, "kol")]
        if views is not None:
            metrics.append(_metric("expected_views_p50", "reference", _int0(views), "views",
                                   "performance_forecast dry-run 口径,参考值非目标线"))
        bet = _mk_bet(
            action_type="kol_outreach",
            bet_key=f"kol:{_int0(kid)}",
            gtm_plan_id=plan_id,
            why_statement=(
                f"new_launch_match 候选池排名靠前且可估价(goal={goal}),进入首批外联窗口;"
                f"匹配分 {cand.get('match_score')},预判引用见 kol_candidates 段与 forecast 7d 窗。"
            ),
            plan_section="kol_candidates",
            plan_ref=f"kol_pool_id={_int0(kid)}",
            forecast_anchor="forecast[horizon=7]",
            what=f"外联候选 KOL @{handle}({platform})并寄样 {sku}",
            who=f"@{handle}(kol_pool_id={_int0(kid)},{platform})",
            expected_statement=expected_stmt,
            metrics=metrics,
            cost_statement=f"预估报价 p50 ${cost if cost is not None else '未知'}(口径 {cand.get('cost_confidence') or '未知'} 置信,基准锚点仅供谈判参考)",
            cost_usd_p50=cost,
            risk=risk,
            risk_level="low",
            escalate_if=esc,
            retreat_if=ret,
            entity_type="kol",
            entity_id=kid,
            now=now,
        )
        # Frozen provider-free rule forecast seed.  materialize owns the later
        # action-id binding and builds the evaluation contract exclusively from
        # the server registry; no client field can select the actual metric.
        if platform and _int0(kid) > 0:
            from app.domains.market_brain import gtm_prediction_producer

            bet["channel"] = platform
            bet["prediction_seed"] = {
                "schema": gtm_prediction_producer.PRODUCER_SCHEMA,
                "registry_key": gtm_prediction_producer.REGISTRY_KEY,
                "method": "gtm_outreach_reply_probability_rule",
                "p10": 0.05,
                "p50": 0.10,
                "p90": 0.20,
                "confidence": "low",
                "channel": platform,
                "kol_pool_id": _int0(kid),
                "basis": [
                    "GTM outreach_reply_floor provider-free Bernoulli probability baseline"
                ],
            }
        items.append(_item(
            action=bet["what"],
            reason="new_launch_match 候选池排名靠前且可估价,进入首批外联窗口。",
            evidence_summary=(
                f"匹配分 {cand.get('match_score')};预估报价 p50 ${cost if cost is not None else '未知'};"
                f"预测播放 p50 {_fmt_int(views) if views is not None else '缺样本'};"
                f"招牌拍法 {(cand.get('signature') or {}).get('top_style') or '未析'}。"
            ),
            cost_note=f"报价口径 {cand.get('cost_confidence') or '未知'} 置信;基准锚点价仅供谈判参考。",
            risk=risk,
            expected_gain=(f"单人播放 p50 约 {_fmt_int(views)}(逐人口径,非承诺)" if views is not None
                           else "预测缺样本,收益待首条内容验证"),
            bet=bet,
        ))
    return items


def _official_post_items(*, plan_id: str, official_section: dict[str, Any],
                         now: datetime) -> list[dict[str, Any]]:
    """官号条目用专属触发/撤退线(账号分位与 30 天基线口径,规格第一章 escalate_if 示例)。"""
    items: list[dict[str, Any]] = []
    suggestions = [s for s in (official_section.get("suggestions") or []) if isinstance(s, dict) and s.get("line")]
    for idx, sug in enumerate(suggestions[:3]):
        line = _text(sug.get("line"), 240)
        bet = _mk_bet(
            action_type="official_post",
            bet_key=f"official:{idx}",
            gtm_plan_id=plan_id,
            why_statement=f"官号历史最优形式×平台×时段聚合给出协同位:{line}",
            plan_section="official_channel_actions",
            plan_ref=f"suggestions[{idx}]",
            forecast_anchor="forecast[horizon=14]",
            what=f"官号协同排期:{line}",
            who="官号运营(自有渠道)",
            expected_statement="复盘日前发布 ≥1 条同 SKU 协同内容,互动不低于该账号 30 天基线。",
            metrics=[_metric("posts_published", ">=", 1, "post"),
                     _metric("er_vs_account_30d_baseline", ">=", 1.0, "ratio",
                             "vkpi_channel_post_metrics 账号 30 天基线口径")],
            cost_statement="自有渠道,零现金成本(人力排期)",
            cost_usd_p50=0,
            risk="官号受众与 KOL 受众重叠度未测",
            risk_level="low",
            escalate_if=(
                "发布后 48h 观看/互动进入该账号近 30 天前 25% 分位 → 追加第二条协同位并为 KOL 内容预约二发。"
            ),
            retreat_if="两条协同内容均低于该账号 30 天基线 → 暂停该形式,回形式×时段聚合复盘。",
            entity_type="official_channel",
            entity_id=f"suggestion_{idx}",
            now=now,
        )
        items.append(_item(
            action="官号协同首发:按历史最优形式/平台/时段排一条同 SKU 内容" if idx == 0 else f"官号协同追加位 #{idx + 1}",
            reason=line,
            evidence_summary=(
                f"vkpi_channel_post_metrics 聚合;官号 {official_section.get('official_count')} 个、"
                f"指标 {official_section.get('metrics_rows')} 行真读。"
            ),
            cost_note="自有渠道,零现金成本(人力排期)。",
            risk="官号受众与 KOL 受众重叠度未测",
            expected_gain="官号协同位为 KOL 内容提供二次曝光,幅度待账本验证",
            bet=bet,
        ))
    return items


def _budget_item(*, sku: str, goal: str, plan_id: str, budget_section: dict[str, Any],
                 esc30: str, ret30: str, now: datetime) -> list[dict[str, Any]]:
    tier = _text(budget_section.get("recommended_tier"), 40)
    budget = _usd(budget_section.get("budget_usd"))
    sim_status = _text((budget_section.get("sim_summary") or {}).get("status"), 20)
    bet = _mk_bet(
        action_type="budget_allocation",
        bet_key="budget:tier",
        gtm_plan_id=plan_id,
        why_statement=(
            f"goal={goal} 的默认预算档为 {tier};三方案模拟状态 {sim_status},"
            "升/降档由 forecast 段触发/撤退线裁决,预判引用见 budget_mix 段。"
        ),
        plan_section="budget_mix",
        plan_ref=f"recommended_tier={tier}",
        forecast_anchor="forecast[horizon=30]",
        what=f"确认 {sku} 预算档:{tier}(${budget})并锁定首批外联额度",
        who="市场负责人(预算裁决人)",
        expected_statement="复盘日前锁定预算档并发出首批外联;档位变更只按触发/撤退线走,不拍脑袋。",
        metrics=[_metric("tier_locked", ">=", 1, "decision"),
                 _metric("first_outreach_batch_sent", ">=", 1, "batch")],
        cost_statement=f"总预算 ${budget}(报价多为基准锚点,总成本区间偏软)",
        cost_usd_p50=budget,
        risk="报价多为基准锚点,总成本区间偏软",
        risk_level="medium",
        escalate_if=esc30,
        retreat_if=ret30,
        entity_type="gtm_plan",
        entity_id=plan_id,
        now=now,
    )
    return [_item(
        action=f"确认预算档:{tier}(${budget})",
        reason="goal 默认档;升/降档由 forecast 段触发/撤退线裁决。",
        evidence_summary=f"三方案模拟状态 {sim_status};明细见 budget_mix 段。",
        cost_note=f"总预算 ${budget}。",
        risk="报价多为基准锚点,总成本区间偏软",
        expected_gain="锁档后可发首批外联与官号排期",
        bet=bet,
    )]


def _content_brief_items(*, plan_id: str, content_section: dict[str, Any],
                         esc14: str, ret14: str, now: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seg_count = len(content_section.get("high_performing_segments") or [])
    sources: list[tuple[str, str, str]] = []  # (kind, key, label)
    angles = content_section.get("persona_angles") or []
    for angle in angles[:2]:
        label = _text(angle, 80)
        if label:
            sources.append(("angle", f"content:angle:{_slug(label)}", label))
    for tpl in (content_section.get("rule_templates") or [])[:2]:
        if isinstance(tpl, dict) and tpl.get("template"):
            sources.append(("template", f"content:template:{_text(tpl.get('key'), 40)}", _text(tpl.get("template"), 120)))
    for kind, bet_key, label in sources:
        why = ("persona 推广角度可直接进 brief(persona.promotion_angles_json)。" if kind == "angle"
               else "规则库开场模板(规则口径非系统真理,可被自有数据推翻)。")
        bet = _mk_bet(
            action_type="content_brief",
            bet_key=bet_key,
            gtm_plan_id=plan_id,
            why_statement=f"{why}预判引用见 content_angles 段与 forecast 14d 窗。",
            plan_section="content_angles",
            plan_ref=f"{kind}:{label[:60]}",
            forecast_anchor="forecast[horizon=14]",
            what=f"内容 brief 起草:主推角度「{label}」",
            who="内容策划(内部人力)",
            expected_statement=(
                "复盘日前 brief 就绪并进入首批排期;变体按 ≥1000 曝光后判胜负,2s hook 留存目标 ≥40%。"
            ),
            metrics=[_metric("briefs_ready", ">=", 1, "brief"),
                     _metric("hook_2s_retention", ">=", 0.40, "retention", "规则库 hook_2s_retention_a"),
                     _metric("per_variant_impressions_gate", ">=", 1000, "impressions",
                             "规则库 per_variant_min_impressions,达门槛才判胜负")],
            cost_statement="brief 起草为内部人力,零现金成本",
            cost_usd_p50=0,
            risk=("角度为 persona 推断,未经本 SKU 实测" if kind == "angle" else "模板为规则库口径,未经本 SKU 实测"),
            risk_level="low",
            escalate_if=esc14,
            retreat_if=ret14,
            entity_type="content_angle",
            entity_id=bet_key,
            now=now,
        )
        items.append(_item(
            action=bet["what"],
            reason=("persona 推广角度 + 规则库开场模板可直接进 brief。" if kind == "angle"
                    else f"规则库模板「{label}」可直接进 brief(非系统真理,可被数据推翻)。"),
            evidence_summary=f"persona 角度 {len(angles)} 条;高表现段 {seg_count} 条可引用。",
            cost_note="brief 起草为内部人力。",
            risk=bet["risk"],
            expected_gain="每变体按曝光门槛裁决后沉淀可复用素材",
            bet=bet,
        ))
    return items


def _indie_site_items(*, plan_id: str, shopify_section: dict[str, Any],
                      now: datetime) -> list[dict[str, Any]]:
    """独立站条目用专属短链触发/撤退线(30d 窗线留给预算档,不混用)。"""
    items: list[dict[str, Any]] = []
    for idx, it in enumerate((shopify_section.get("items") or [])[:4]):
        if not isinstance(it, dict) or not it.get("action"):
            continue
        action = _text(it.get("action"), 200)
        note = _text(it.get("note"), 200)
        bet = _mk_bet(
            action_type="indie_site_update",
            bet_key=f"indie:{idx}",
            gtm_plan_id=plan_id,
            why_statement=(
                f"转化承接前置件:{note or action}。短链→webhook 归因是转化验证的前提,"
                "预判引用见 shopify_indie_site_actions 段与 forecast 30d 窗。"
            ),
            plan_section="shopify_indie_site_actions",
            plan_ref=f"items[{idx}]",
            forecast_anchor="forecast[horizon=30]",
            what=action,
            who="独立站运营(viltroxvia.com 短链域 + 独立站)",
            expected_statement=(
                "复盘日前完成该项配置且归因链路有活性信号(短链点击开始累计;"
                "本地无订单数据,商业结果窗诚实 pending)。"
            ),
            metrics=[_metric("setup_done", ">=", 1, "task"),
                     _metric("shortlink_clicks", ">=", 1, "clicks", "归因链路活性信号,非业绩承诺")],
            cost_statement="配置为内部人力;佣金档 10-15% 区间起步(跑通首批归因后再开)",
            cost_usd_p50=0,
            risk="本地库无 Shopify 订单数据,转化闭环需上线 webhook 归因后才可验证",
            risk_level="medium",
            escalate_if="短链 14 天点击成本优于 CPM 锚点且出现可归因行为 → 把码铺进更多表现位并开阶梯佣金。",
            retreat_if="短链 14 天零点击或落地页承接异常 → 先修埋点/挂载位与承接页,再谈放量。",
            entity_type="indie_site",
            entity_id=f"item_{idx}",
            now=now,
        )
        items.append(_item(
            action=action,
            reason=note or "转化承接前置件(模板建议,本地无订单数据)。",
            evidence_summary=f"shopify_indie_site_actions 段 items[{idx}];{note}",
            cost_note="内部人力;佣金档跑通首批归因后再开。",
            risk=bet["risk"],
            expected_gain="归因链路就绪后,转化验证才有账可对",
            bet=bet,
        ))
    return items


# ── 段构建主入口(gtm_plan_preview._build_action_inbox_items 委托至此) ──


def build_action_inbox_items(
    *,
    sku: str,
    goal: str,
    country: str | None,
    budget_usd: Any,
    kol_section: dict[str, Any],
    official_section: dict[str, Any],
    budget_section: dict[str, Any],
    content_section: dict[str, Any],
    shopify_section: dict[str, Any],
    forecast: Any,
) -> dict[str, Any]:
    """materialize 预览段:10-30 条 bet(七要素齐),零落库。

    真落库走 materialize.materialize_plan(dry_run=False);同日同输入 gtm_plan_id
    确定性一致,dedupe_key=gtm_bet:{gtm_plan_id}:{bet_key} 保证同 plan 同 action 不重插。
    """
    from app.domains.market_brain.gtm_plan_preview import absolute_wording_hits

    now = datetime.now(timezone.utc)
    plan_id = gtm_plan_id_for(sku, goal)
    esc7, ret7 = _fc_lines(forecast, 7, _FALLBACK_ESCALATE_7D, _FALLBACK_RETREAT_7D)
    esc14, ret14 = _fc_lines(forecast, 14, _FALLBACK_ESCALATE_14D, _FALLBACK_RETREAT_14D)
    esc30, ret30 = _fc_lines(forecast, 30, _FALLBACK_ESCALATE_30D, _FALLBACK_RETREAT_30D)

    items: list[dict[str, Any]] = []
    items.extend(_kol_outreach_items(sku=sku, goal=goal, plan_id=plan_id,
                                     kol_section=kol_section, esc=esc7, ret=ret7, now=now))
    items.extend(_official_post_items(plan_id=plan_id, official_section=official_section, now=now))
    items.extend(_budget_item(sku=sku, goal=goal, plan_id=plan_id, budget_section=budget_section,
                              esc30=esc30, ret30=ret30, now=now))
    items.extend(_content_brief_items(plan_id=plan_id, content_section=content_section,
                                      esc14=esc14, ret14=ret14, now=now))
    items.extend(_indie_site_items(plan_id=plan_id, shopify_section=shopify_section, now=now))

    # 措辞纪律守卫:bet 双线缺失或绝对化词根命中 → 该条不出货(与 forecast 段同一条纪律)
    disciplined: list[dict[str, Any]] = []
    for item in items:
        bet = item.get("bet") or {}
        blob = " ".join(_text(bet.get(k), 2000) for k in ("what", "escalate_if", "retreat_if"))
        blob += " " + _text((bet.get("expected") or {}).get("statement"), 2000)
        if not bet.get("escalate_if") or not bet.get("retreat_if") or absolute_wording_hits(blob):
            logger.warning("gtm_bets item dropped by wording discipline: %s", bet.get("bet_key"))
            continue
        disciplined.append(item)

    return {
        "status": "preview",
        "persisted": False,
        "gtm_plan_id": plan_id,
        "bet_contract": BET_CONTRACT_VERSION,
        "count": len(disciplined[:MAX_BETS]),
        "items": disciplined[:MAX_BETS],
        "inputs": {"sku": _text(sku, 120), "goal": _text(goal, 20),
                   "country": _text(country, 8) or None, "budget_usd": _usd(budget_usd)},
        "review_days_defaults": dict(BET_REVIEW_DAYS),
        "materialize_endpoint": "POST /api/admin/vkpi/market-brain/gtm-plan/materialize",
        "note": (
            "materialize 预览:零落库;dry_run=False 经 materialize 落 vkpi_action_inbox 后"
            "逐条走人审(requires_approval=True),裁决只走人工 POST,无自动裁决路径。"
        ),
    }


__all__ = [
    "BET_CONTRACT_VERSION",
    "BET_REVIEW_DAYS",
    "DEFAULT_BET_REVIEW_DAYS",
    "MAX_BETS",
    "gtm_plan_id_for",
    "review_at_for",
    "build_action_inbox_items",
]
