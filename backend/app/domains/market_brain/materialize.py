"""GTM-Loop L1 materialize:GTM Plan preview 的 bet 条目 → vkpi_action_inbox 真落库。

materialize_plan(sku, country, budget_usd, goal, dry_run=True):
- 调 gtm_plan_preview.build_preview 拿 action_inbox_items(bet 合约版,纯读);
- dry_run=True(默认):只出 bet 预览与幂等对账(would_insert / already_present),零写库;
- dry_run=False:逐条经 producers.make_suggestion → inbox.persist_suggestions 落
  vkpi_action_inbox,payload 带完整 bet 七要素 + gtm_plan_id(确定性 sku+date+goal 哈希)。

人审红线(用户点名「保证审」):
- 落库的每条 bet requires_approval=True,无一例外(落库后 SQL 侧复核计数);
- 本模块没有任何裁决(verdict)路径——裁决只走人工 POST(L2 端点);
- 幂等:dedupe_key = gtm_bet:{gtm_plan_id}:{bet_key},同 plan 同 action 不重插
  (UPSERT 只刷新仍是 suggested 的行,已人审的行绝不复活)。

其他红线:绝不写 viltrox_fit_score / rule_v0;compat SQL 用 ? 占位、无字面 %。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

CATEGORY = "gtm_bet"
_TABLE = "vkpi_action_inbox"

# bet 七要素齐才落库(缺任一要素 → 该条 skip 并如实计数,绝不半合约入账)
_REQUIRED_BET_FIELDS = ("why", "what", "expected", "cost", "risk", "escalate_if", "retreat_if", "review_at")


def _text(value: Any, limit: int = 300) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _existing_dedupe_keys(conn: Any, keys: list[str]) -> set[str]:
    """既有 dedupe_key 集合(幂等对账用;compat:? 占位,无字面 %)。"""
    if not keys:
        return set()
    placeholders = ",".join(["?"] * len(keys))
    rows = conn.execute(
        f"SELECT dedupe_key FROM {_TABLE} WHERE dedupe_key IN ({placeholders})",
        tuple(keys),
    ).fetchall()
    return {str(dict(r).get("dedupe_key") or "") for r in rows}


def _suggestion_for_bet(
    *,
    item: dict[str, Any],
    bet: dict[str, Any],
    gtm_plan_id: str,
    sku: str,
    country: str | None,
    goal: str,
    budget_usd: float,
    window_days: int,
    plan_generated_at: str,
    actor_staff_id: int | None,
) -> dict[str, Any]:
    from app.domains.actions.producers import make_suggestion

    subject = bet.get("subject") or {}
    expected = bet.get("expected") or {}
    why = bet.get("why") or {}
    dedupe_key = f"gtm_bet:{gtm_plan_id}:{_text(bet.get('bet_key'), 120)}"
    payload: dict[str, Any] = {
        "gtm_plan_id": gtm_plan_id,
        "bet": bet,  # 完整七要素合约(why/what+who/expected/cost+risk/escalate_if/retreat_if/review_at)
        "sku": _text(sku, 120),
        "country": _text(country, 8) or None,
        "goal": _text(goal, 20),
        "budget_usd": float(budget_usd),
        "window_days": int(window_days),
        "plan_generated_at": _text(plan_generated_at, 40),
        "review_at": _text(bet.get("review_at"), 20),
        "source": "gtm_plan_materialize_v1",
        "requires_approval": True,
        "verdict": None,  # 裁决占位:只由 L2 人工 POST 写,materialize 永不自裁
        "requested_by_staff_id": int(actor_staff_id) if actor_staff_id else None,
    }
    # KOL 桥:subject 指向 kol 时把 kol_pool_id 提到 payload 顶层(int 化,失败不加不炸),
    # 供 L2 verdict_flow._bet_context 直取 → vkpi_gtm_outcomes.kol_pool_id 不再落 NULL(三窗不饿死)。
    if _text(subject.get("entity_type"), 40) == "kol":
        kol_pool_id = _int_or_none(subject.get("entity_id"))
        if kol_pool_id:
            payload["kol_pool_id"] = kol_pool_id
    suggestion = make_suggestion(
        category=CATEGORY,
        dedupe_key=dedupe_key,
        title=f"[GTM bet] {_text(bet.get('what') or item.get('action'), 140)}",
        detail=(
            f"{_text(bet.get('what'), 200)};预期:{_text(expected.get('statement'), 160)};"
            f"复盘 {_text(bet.get('review_at'), 20)}({bet.get('review_days')}d,{_text(bet.get('action_type'), 40)})"
        ),
        reason=_text(why.get("statement") or item.get("reason"), 400),
        priority="medium",
        entity_type=_text(subject.get("entity_type"), 40) or "gtm_plan",
        entity_id=_text(subject.get("entity_id"), 80) or gtm_plan_id,
        suggested_endpoint="",  # bet 执行是人做业务动作,不给自动执行端点(无自动执行体)
        estimated_cost_cents=0,  # 现金成本在 payload.bet.cost(报价锚点非执行成本),不进预算闸
        writes_business_data=False,
        uses_llm=False,
        requires_approval=True,  # 人审红线:每条 bet 必审
        owner_staff_id=None,  # 公司级(GTM 是全局作战),仅管理层可见
        payload=payload,
        expected_gain=_text(expected.get("statement"), 300),
        risk_level=_text(bet.get("risk_level"), 10) or "medium",
        evidence_refs=[
            {"type": "gtm_plan", "id": gtm_plan_id},
            {"type": _text(subject.get("entity_type"), 40) or "gtm_plan",
             "id": _text(subject.get("entity_id"), 80) or gtm_plan_id},
        ],
        verification_plan=[
            f"预期指标:{json.dumps(expected.get('metrics') or [], ensure_ascii=False, default=str)[:400]}",
            f"复盘日 {_text(bet.get('review_at'), 20)} 到期 → 三窗(7/14/28)对答案后人工裁决(L2 verdict,无自动裁决)",
        ],
        affected_tables=[],  # 执行为人工业务动作;本条自身只写 vkpi_action_inbox 台账
    )
    # 双保险:make_suggestion 已按参数置 True,这里再断言,绝不让非人审 bet 出门。
    suggestion["requires_approval"] = True
    return suggestion


def materialize_plan(
    sku: str,
    country: str | None = None,
    budget_usd: float = 3000,
    goal: str = "exposure",
    dry_run: bool = True,
    *,
    window_days: int = 30,
    actor_staff_id: int | None = None,
) -> dict[str, Any]:
    """GTM Plan → bet 落库(dry_run 双态)。返回幂等对账明细。

    SKU 不存在 → LookupError(路由转 404);goal/budget 非法 → ValueError(路由转 422)。
    dry_run=True:零写库(只读预览 + 幂等对账);dry_run=False:persist_suggestions 落库。
    """
    from app.db.connection import get_conn, table_exists
    from app.domains.actions import inbox
    from app.domains.market_brain import gtm_bets, gtm_plan_preview

    preview = gtm_plan_preview.build_preview(
        sku=sku, country=country, budget_usd=budget_usd, goal=goal, window_days=window_days,
    )
    plan = preview.get("public_plan") or {}
    meta = preview.get("meta") or {}
    section = plan.get("action_inbox_items") or {}
    items = section.get("items") or []
    resolved_sku = _text(plan.get("sku"), 120) or _text(sku, 120)
    goal_clean = _text(goal, 20).lower() or "exposure"
    gtm_plan_id = _text(section.get("gtm_plan_id"), 40) or gtm_bets.gtm_plan_id_for(resolved_sku, goal_clean)

    suggestions: list[dict[str, Any]] = []
    skipped_incomplete: list[dict[str, Any]] = []
    for item in items:
        bet = item.get("bet") if isinstance(item, dict) else None
        if not isinstance(bet, dict):
            skipped_incomplete.append({"action": _text((item or {}).get("action"), 120), "missing": ["bet"]})
            continue
        missing = [k for k in _REQUIRED_BET_FIELDS if not bet.get(k)]
        if missing:
            skipped_incomplete.append({"bet_key": _text(bet.get("bet_key"), 120), "missing": missing})
            continue
        suggestions.append(_suggestion_for_bet(
            item=item, bet=bet, gtm_plan_id=gtm_plan_id, sku=resolved_sku, country=country,
            goal=goal_clean, budget_usd=float(budget_usd or 0), window_days=int(window_days),
            plan_generated_at=_text(meta.get("generated_at"), 40), actor_staff_id=actor_staff_id,
        ))

    keys = [s["dedupe_key"] for s in suggestions]
    table_ready = table_exists(_TABLE)
    existing_before = _existing_dedupe_keys(get_conn(), keys) if table_ready else set()
    new_keys = [k for k in keys if k not in existing_before]

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "gtm_plan_id": gtm_plan_id,
        "sku": resolved_sku,
        "goal": goal_clean,
        "country": _text(country, 8) or None,
        "budget_usd": float(budget_usd or 0),
        "window_days": int(window_days),
        "bet_contract": _text(section.get("bet_contract"), 20) or gtm_bets.BET_CONTRACT_VERSION,
        "bets_total": len(suggestions),
        "already_present": len(keys) - len(new_keys),
        "skipped_incomplete": skipped_incomplete,
        "requires_approval_all": all(s.get("requires_approval") is True for s in suggestions),
        "plan_generated_at": _text(meta.get("generated_at"), 40),
    }

    if dry_run:
        result["persisted"] = 0
        result["would_insert"] = len(new_keys)
        result["bets"] = [
            {"dedupe_key": s["dedupe_key"], "title": s["title"],
             "requires_approval": s["requires_approval"], "bet": (s.get("payload") or {}).get("bet")}
            for s in suggestions
        ]
        result["note"] = "dry_run:零写库;dry_run=false 时经 inbox.persist_suggestions 幂等落库并逐条人审。"
        return result

    if not table_ready:
        return {**result, "ok": False, "persisted": 0, "reason": "migration_141_not_applied"}

    # 真落库:B4 已验的 persist_suggestions(UPSERT 只刷新仍是 suggested 的行)。
    upserts = inbox.persist_suggestions(suggestions)
    existing_after = _existing_dedupe_keys(get_conn(), keys)
    inserted_new = len([k for k in new_keys if k in existing_after])

    # 人审红线 SQL 侧复核:本 plan 落库行 requires_approval 必须全 TRUE(SQL 判真,避 BOOLEAN 读回 int 陷阱)。
    approval_ok = True
    if keys:
        placeholders = ",".join(["?"] * len(keys))
        row = get_conn().execute(
            f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE dedupe_key IN ({placeholders}) "
            "AND requires_approval IS NOT TRUE",
            tuple(keys),
        ).fetchone()
        approval_ok = int(dict(row).get("n") or 0) == 0 if row else False

    result.update({
        "persisted": upserts,
        "inserted_new": inserted_new,
        "requires_approval_persisted_all": approval_ok,
        "note": (
            "已落 vkpi_action_inbox(status=suggested),逐条 requires_approval=True 待人审;"
            "裁决只走人工 POST(L2),无自动裁决路径;复跑同 plan 幂等不重插。"
        ),
    })
    if not approval_ok:
        # 不应发生(make_suggestion + 本模块双保险);发生即诚实报错,让人来看。
        logger.error("gtm_materialize requires_approval readback failed for plan %s", gtm_plan_id)
        result["ok"] = False
        result["reason"] = "requires_approval_readback_failed"
    return result


__all__ = ["materialize_plan", "CATEGORY"]
