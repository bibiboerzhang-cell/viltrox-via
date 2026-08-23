"""Skill 驾照真跑闸 —— vkpi_skill_auto_orchestrate 从「恒 dry_run」改为「按驾照决定」。

决策(全部纯规则,零自动晋升):
  1. 驾照:agents.autonomy_license.current_level('skill_orchestrate')。level < 2(观察/建议)→ 规则模式
     (dry_run=True,零 LLM);level >= 2(内部执行及以上)才允许为 creator_match 注入 model_fn。
     本模块只读驾照,绝不调用 evaluate_promotions / manual_override —— 不自动晋升 L3。
  2. 预算:costs.budget_guard.check_budget('agent_skill', est, require_configured=True)。
     cap 行缺(迁移 292 未 apply)或 hard stop → 回退规则模式并标 budget_blocked,绝不偷烧。
  3. model_fn:gemini-3.6-flash 经 platform.llm_production.generate_text(即 llm_gateway 原子边界),
     cost_tag='agent_skill';仅喂给 creator_match(按 skill_name 分发,其余 skill 收到 None);
     单次编排最多 VKPI_AGENT_SKILL_MAX_LLM_CALLS(默认 10)次,超出回退确定性理由。
  4. 输入:vkpi_product_launches 最近上市(deleted_at IS NULL,按 launch_window_start/updated_at 倒序)
     且能命中产品族的 SKU;全 miss 退回 skill_orchestrator.resolve_default_product(目录)。
  5. 输出:creator_match 的 recommendations 经 actions.producers.make_suggestion →
     actions.inbox.persist_suggestions 进 vkpi_action_inbox(category=skill_creator_match,
     dedupe_key 按 product+kol 幂等,requires_approval=True)。

运维:预算 scope 'agent_skill' 封顶 $40(vkpi_provider_budget_caps,迁移 292 种子);
env VKPI_AGENT_SKILL_EST_USD(单次编排预估,默认 0.05)/ VKPI_AGENT_SKILL_MAX_LLM_CALLS。
红线:零触 viltrox_fit_score / rule_v0;不写任何业务表(vkpi_skill_runs 由 skill 自落,inbox 为既有通道)。
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

ACTION_TYPE = "skill_orchestrate"
BUDGET_SCOPE = "agent_skill"
LLM_PURPOSE = "agent_skill_creator_match"
INBOX_CATEGORY = "skill_creator_match"
MIN_LEVEL_FOR_LLM = 2
ENV_EST_USD = "VKPI_AGENT_SKILL_EST_USD"
ENV_MAX_LLM_CALLS = "VKPI_AGENT_SKILL_MAX_LLM_CALLS"
_LAUNCH_PROBE_LIMIT = 10


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# ── ① 驾照 ─────────────────────────────────────────────────────────────


def license_level() -> dict[str, Any]:
    """当前 skill_orchestrate 驾照(只读);读失败按 L0 诚实降级。"""
    try:
        from app.domains.agents import autonomy_license

        lic = autonomy_license.current_level(ACTION_TYPE)
        return {"level": int(lic.get("level") or 0), "status": lic.get("status"),
                "label": lic.get("level_label"), "reason": lic.get("reason") or lic.get("last_change_reason") or ""}
    except Exception as exc:  # noqa: BLE001 — 驾照模块异常 = 无驾照
        logger.warning("skill_license_gate: license read failed: %s", exc)
        return {"level": 0, "status": "error", "label": "观察", "reason": str(exc)[:160]}


# ── ② 预算 ─────────────────────────────────────────────────────────────


def budget_allows(est_usd: float | None = None) -> tuple[bool, str]:
    """agent_skill scope 预算闸;cap 行缺/hard stop/子系统异常 → False(绝不偷烧)。"""
    est = _env_float(ENV_EST_USD, 0.05) if est_usd is None else float(est_usd)
    try:
        from app.domains.costs import budget_guard

        ok = bool(budget_guard.check_budget(BUDGET_SCOPE, est, require_configured=True))
        return ok, ("ok" if ok else f"scope {BUDGET_SCOPE} 未配置或已 hard stop(est ${est:.3f})")
    except Exception as exc:  # noqa: BLE001
        logger.debug("skill_license_gate: budget_guard unavailable", exc_info=True)
        return False, f"budget_guard 不可用: {str(exc)[:120]}"


# ── ③ model_fn(仅 creator_match)───────────────────────────────────────


def build_creator_match_model_fn(*, max_calls: int | None = None) -> Callable[[dict[str, Any]], str]:
    """gemini-3.6-flash 经 llm_production(=llm_gateway 原子边界)出 fit_reason;超次数上限即抛 → skill 回退规则理由。"""
    cap = max(1, _env_int(ENV_MAX_LLM_CALLS, 10) if max_calls is None else int(max_calls))
    state = {"calls": 0}

    def _model_fn(ctx: dict[str, Any]) -> str:
        if state["calls"] >= cap:
            raise RuntimeError(f"agent_skill llm call cap {cap} reached")
        state["calls"] += 1
        from app.core.gemini_models import DEFAULT_VIDEO_GEMINI_MODEL
        from app.platform import llm_production

        pros = "; ".join(str(x) for x in (ctx.get("evidence_pro") or [])[:5])
        cons = "; ".join(str(x) for x in (ctx.get("evidence_con") or [])[:3])
        prompt = (
            "你是镜头品牌的 KOL 合作分析师。用一句中文(不超过 60 字)说明该创作者为何适合推广该产品,"
            "只基于给定证据,不编造。\n"
            f"产品:{ctx.get('product')}\n创作者:{ctx.get('handle')}(地区 {ctx.get('country') or '-'},"
            f"匹配分 {ctx.get('score')})\n正面证据:{pros or '-'}\n顾虑:{cons or '-'}"
        )
        result = llm_production.generate_text(
            prompt, provider="google", model=DEFAULT_VIDEO_GEMINI_MODEL, purpose=LLM_PURPOSE,
            max_output_tokens=120, cost_tag=BUDGET_SCOPE,
            metadata={"surface": "skill_auto_orchestrate", "phase": "creator_match_fit_reason"},
        )
        return str(result.get("text") or "").strip()[:200]

    _model_fn.calls = state  # type: ignore[attr-defined]
    return _model_fn


def creator_match_only(model_fn: Callable[..., Any] | None) -> Callable[[str], Callable[..., Any] | None]:
    """按 skill_name 分发 model_fn:仅 creator_match 拿到真 model_fn,其余 None。"""
    def _pick(skill_name: str) -> Callable[..., Any] | None:
        return model_fn if _text(skill_name) == "creator_match" else None
    return _pick


# ── ④ 输入:最近上市 SKU ──────────────────────────────────────────────


def recent_launch_product() -> dict[str, Any]:
    """vkpi_product_launches 最近上市且能命中产品族的 SKU;miss → 退目录 resolve_default_product。"""
    from app.domains.marketing_brain import skill_orchestrator as so

    try:
        from app.db.connection import get_conn, table_exists

        if table_exists("vkpi_product_launches"):
            rows = get_conn().execute(
                """
                SELECT id, product_sku, product_name, name, target_market, launch_window_start
                FROM vkpi_product_launches
                WHERE deleted_at IS NULL AND status <> 'archived'
                ORDER BY launch_window_start DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                (_LAUNCH_PROBE_LIMIT,),
            ).fetchall()
            for row in rows:
                item = dict(row)
                for cand in (item.get("product_sku"), item.get("product_name"), item.get("name")):
                    if _text(cand) and so._resolves_to_family(_text(cand)):
                        return {"product": _text(cand), "source": "product_launches", "launch_id": item.get("id"),
                                "market": _text(item.get("target_market"))}
    except Exception:  # noqa: BLE001 — 上市表读失败退目录
        logger.debug("skill_license_gate: product_launches read failed", exc_info=True)
    fallback = so.resolve_default_product()
    if fallback:
        return {"product": fallback, "source": "catalog", "launch_id": None, "market": ""}
    return {"product": "", "source": "none", "launch_id": None, "market": ""}


# ── ⑤ 输出:recommendations → action_inbox ────────────────────────────


def publish_to_inbox(orchestration: dict[str, Any], *, product: str, llm_used: bool, level: int) -> int:
    """creator_match 推荐经 producers.make_suggestion → inbox.persist_suggestions(幂等);返回落库条数。"""
    from app.domains.actions import inbox
    from app.domains.actions.producers import make_suggestion

    suggestions: list[dict[str, Any]] = []
    product_key = "".join(ch if ch.isalnum() else "_" for ch in _text(product).lower())[:40] or "unknown"
    for result in orchestration.get("results") or []:
        if _text(result.get("skill_name")) != "creator_match" or result.get("status") != "ok":
            continue
        recs = (result.get("output") or {}).get("recommendations") or []
        for rec in recs[:50]:
            kol_id = rec.get("kol_pool_id")
            if kol_id in (None, "", 0):
                continue
            handle = _text(rec.get("handle")) or f"kol_pool#{kol_id}"
            suggestions.append(make_suggestion(
                category=INBOX_CATEGORY,
                dedupe_key=f"{INBOX_CATEGORY}:{product_key}:{kol_id}",
                title=f"Skill 推荐创作者:@{handle} → {product}",
                detail=_text(rec.get("fit_reason")) or "规则匹配(无 LLM 理由)",
                reason=f"creator_match 经编排器自动跑(驾照 L{level},{'LLM 理由' if llm_used else '规则理由'});风险 {rec.get('risk') or '-'}",
                priority="medium", entity_type="kol_pool", entity_id=str(kol_id),
                suggested_endpoint=f"/api/vkpi/kol-pool/{kol_id}",
                estimated_cost_cents=int(rec.get("est_cost_cents") or 0),
                writes_business_data=False, uses_llm=bool(llm_used), requires_approval=True,
                payload={"product": product, "kol_pool_id": kol_id, "fit_reason": rec.get("fit_reason"),
                         "risk": rec.get("risk"), "license_level": level, "llm_used": bool(llm_used)},
                expected_gain="纳入候选后可直接发起合作邀约", risk_level="low",
                evidence_refs=[r for r in (rec.get("evidence_refs") or []) if isinstance(r, dict)],
                verification_plan=["人工确认候选后再邀约;未批准不产生任何外部动作"], affected_tables=[],
            ))
    if not suggestions:
        return 0
    try:
        return int(inbox.persist_suggestions(suggestions) or 0)
    except Exception:  # noqa: BLE001
        logger.warning("skill_license_gate: inbox persist failed", exc_info=True)
        return 0


# ── 入口 ───────────────────────────────────────────────────────────────


def licensed_auto_orchestrate(*, record: bool = True, publish: bool = True,
                              model_fn_factory: Optional[Callable[[], Callable[..., Any]]] = None) -> dict[str, Any]:
    """按驾照决定的自动编排入口(scheduler 用)。返回编排回执 + gate 决策痕。"""
    from app.domains.marketing_brain import skill_orchestrator as so

    lic = license_level()
    level = int(lic.get("level") or 0)
    decision: dict[str, Any] = {"license_level": level, "license_status": lic.get("status"),
                                "llm_allowed": False, "budget": "not_checked", "mode": "rule"}
    model_fn: Callable[..., Any] | None = None
    if level >= MIN_LEVEL_FOR_LLM:
        ok, why = budget_allows()
        decision["budget"] = why
        if ok:
            factory = model_fn_factory or build_creator_match_model_fn
            model_fn = factory()
            decision.update({"llm_allowed": True, "mode": "llm"})
        else:
            decision["mode"] = "rule_budget_blocked"
    else:
        decision["budget"] = f"驾照 L{level} < L{MIN_LEVEL_FOR_LLM},不调 LLM"

    source = recent_launch_product()
    ctx: dict[str, Any] = {}
    if source.get("product"):
        ctx["product"] = source["product"]
        if source.get("market"):
            ctx["market"] = source["market"]
    out = so.auto_orchestrate(
        context=ctx or None, dry_run=model_fn is None, record=record,
        model_fn_by_skill=creator_match_only(model_fn) if model_fn is not None else None,
    )
    if not isinstance(out, dict):
        out = {"status": "error", "results": []}
    out["gate"] = decision
    out["product_source_detail"] = source
    llm_calls = int(getattr(model_fn, "calls", {}).get("calls", 0)) if model_fn is not None else 0
    out["llm_calls"] = llm_calls
    out["inbox_persisted"] = (
        publish_to_inbox(out, product=_text(ctx.get("product")), llm_used=llm_calls > 0, level=level)
        if publish and out.get("status") == "ok" else 0
    )
    return out


__all__ = [
    "ACTION_TYPE", "BUDGET_SCOPE", "INBOX_CATEGORY", "MIN_LEVEL_FOR_LLM",
    "budget_allows", "build_creator_match_model_fn", "creator_match_only", "license_level",
    "licensed_auto_orchestrate", "publish_to_inbox", "recent_launch_product",
]
