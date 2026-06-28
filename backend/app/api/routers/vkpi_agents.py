"""路线1 · Agent 编排 API(PLAN-ONLY)。

POST /api/admin/vkpi/agents/plan      — 一句话目标 → 分步计划(只产计划不执行)。
GET  /api/admin/vkpi/agents/tools     — 工具白名单 manifest。
GET  /api/admin/vkpi/agents/plan/{id} — 读单条计划留痕。
红线:编排器 PLAN-ONLY;写库/烧 LLM 步骤走 Action Inbox 人审;零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.domains.agents import orchestrator, tool_registry

router = APIRouter(prefix="/api/admin/vkpi/agents", tags=["vkpi-agents"])


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN 读回是 int 1/0(非 Python True),`is True` 全 falsy;统一容错判真。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "t", "yes", "y")
    return bool(value)


@router.post("/plan")
def plan(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """一句话目标 → 分步计划(PLAN-ONLY,不执行)。body: {goal, context?}。"""
    goal = str((body or {}).get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal required")
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    return orchestrator.plan_goal(goal, context=context, staff=staff)


@router.get("/tools")
def tools(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """工具白名单 manifest。"""
    return {"tools": tool_registry.list_tools(), "count": len(tool_registry.TOOL_REGISTRY)}


@router.get("/plan/{plan_id}")
def read_plan(
    plan_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """读单条计划留痕。"""
    item = orchestrator.get_plan(int(plan_id), staff=staff)
    if item is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return item


@router.get("/recall")
def unified_recall(
    q: str,
    limit: int = 10,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """B3 · 一句话跨信号召回:相关 KOL/视频/项目/活动(词法兜底,向量后端就绪自动升级)。"""
    from app.domains.intelligence import semantic_recall

    return semantic_recall.unified_recall(q, limit=int(limit), staff=staff)


@router.get("/tenant/current")
def tenant_current(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """轴B · 当前租户(默认 Viltrox=org1;给顶栏/health 显示)。"""
    from app.domains.platform import tenancy

    return tenancy.current_tenant(staff)


@router.get("/organizations")
def organizations_list(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """轴B · 租户清单。"""
    from app.domains.platform import tenancy

    return {"organizations": tenancy.list_organizations()}


@router.post("/evals/run")
def evals_run(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """P4 · 跑业务评测套件(推荐不碰fit/召回/权重有界/事件总线/预测诚实),持久化结果。"""
    from app.domains.platform import evals

    return evals.run_builtin_suite()


@router.get("/marketing-brain/scorecard")
def marketing_brain_scorecard(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """问题1 · AI Marketing Brain 90+ 评分卡:证据/流程/推荐/学习/市场/evals 六维只读评估。"""
    from app.domains.intelligence import marketing_brain_scorecard as scorecard

    return scorecard.build_marketing_brain_scorecard(staff)


@router.get("/data-catalog")
def data_catalog(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """Data Catalog:每个指标自报来源/真假(real|awaiting_source)/新鲜度——数字可追溯真假。"""
    from app.domains.lineage import catalog

    return catalog.build_data_catalog()


@router.post("/bets")
def bet_create(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))) -> dict[str, Any]:
    """cut5 · Bet Ledger:下一注概率押注。body:{hypothesis(必填), probability?, expected_gain_cents?, cost_cents?, risk_level?, evidence?, review_at?, source_action_inbox_id?}。"""
    from app.domains.market import bet_ledger

    b = body or {}
    return bet_ledger.create_bet(
        hypothesis=str(b.get("hypothesis") or ""), probability=b.get("probability"),
        expected_gain_cents=int(b.get("expected_gain_cents") or 0), cost_cents=int(b.get("cost_cents") or 0),
        risk_level=str(b.get("risk_level") or "med"), evidence=b.get("evidence") if isinstance(b.get("evidence"), dict) else {},
        review_at=b.get("review_at"), source_action_inbox_id=b.get("source_action_inbox_id"), staff=staff)


@router.post("/bets/{action_id}/approve")
def bet_approve_from_inbox(
    action_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """闭环C 收尾 · approve→真下注:把一条 inbox 'bet' 草案 approve 后真落 bet_ledger.create_bet。

    双闸红线:
    - 仅 category=='bet' 且 requires_approval 的草案可走此路;先 approve_action(走人审状态机),
      approved 成功后才据 bet_producer 埋好的 payload 契约真 create_bet。
    - 绝不自动烧钱:cost_cents 强制 0、expected_gain_cents 强制 0(押注是判断,不烧 LLM/Apify)。
    - 绝不触 viltrox_fit_score:只写 vkpi_bet_ledger,不碰任何 fit 路径。
    body(可选):{reason?}。
    """
    from app.domains.actions import inbox
    from app.domains.market import bet_ledger

    item = inbox.get_action(int(action_id), staff)
    if item is None:
        raise HTTPException(status_code=404, detail="action not found or out of scope")
    if str(item.get("category") or "") != "bet":
        raise HTTPException(status_code=400, detail="not a bet draft")
    if not _truthy(item.get("requires_approval")):
        raise HTTPException(status_code=400, detail="bet draft is not approval-gated")

    payload = item.get("payload_json")
    payload = payload if isinstance(payload, dict) else {}
    hypothesis = str(payload.get("hypothesis") or item.get("detail") or "").strip()
    if not hypothesis:
        raise HTTPException(status_code=400, detail="bet draft missing hypothesis")

    reason = str((body or {}).get("reason") or "").strip()
    approved = inbox.approve_action(int(action_id), staff, reason=reason)
    if not (isinstance(approved, dict) and approved.get("ok")):
        # 仅 approved 才下注:状态机拒绝(非 suggested/已审/越权)→ 据实回 409。
        raise HTTPException(status_code=409, detail={"approve_failed": approved})

    # verification_date / review_at:bet_producer 已埋复盘日;create_bet 用 review_at(ISO datetime)。
    review_at = payload.get("review_at") or payload.get("verification_date")
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    bet = bet_ledger.create_bet(
        hypothesis=hypothesis,
        probability=payload.get("probability"),
        expected_gain_cents=0,          # 双闸:绝不自动烧钱/许诺收益
        cost_cents=0,                   # 双闸:绝不自动烧钱
        risk_level=str(payload.get("risk_level") or "med"),
        evidence=evidence,
        review_at=review_at,
        source_action_inbox_id=int(action_id),
        staff=staff,
    )
    return {
        "status": "ok",
        "action_id": int(action_id),
        "approved": approved,
        "bet": bet,
        "verification_date": payload.get("verification_date"),
    }


@router.get("/bets")
def bet_list(outcome: str = "", limit: int = 50, staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """cut5 · 押注清单 + 命中率(已结算里 won 占比)。"""
    from app.domains.market import bet_ledger

    return bet_ledger.list_bets(outcome=outcome, limit=int(limit), staff=staff)


@router.post("/bets/{bet_id}/resolve")
def bet_resolve(bet_id: int, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))) -> dict[str, Any]:
    """cut5 · 到期复盘:结算押注 won/lost/void + 写 lesson。body:{outcome(必填), realized_gain_cents?, lesson?}。"""
    from app.domains.market import bet_ledger

    b = body or {}
    return bet_ledger.resolve_bet(int(bet_id), str(b.get("outcome") or ""),
                                  realized_gain_cents=b.get("realized_gain_cents"), lesson=str(b.get("lesson") or ""), staff=staff)


@router.get("/marketing-brain/daily")
def marketing_brain_daily(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """cut1 · Market Brain v1 日报:每日合成产品热/上升渠道/竞品动/机会窗/今日建议(只读)。"""
    from app.domains.market import market_brain

    return market_brain.build_daily_brief(staff)


@router.post("/marketing-brain/refresh")
def marketing_brain_refresh(staff=Depends(require_tab("vkpi", "write"))) -> dict[str, Any]:
    """cut1 · 活体化:治理过期信号 + 重出日报(运营手动刷;调度器每日自动跑)。"""
    from app.domains.market import market_brain

    expired = market_brain.mark_expired_signals()
    brief = market_brain.build_daily_brief(staff)
    return {"status": "ok", "expired_swept": expired, "brief": brief}


@router.post("/cycle/run")
def agent_cycle_run(staff=Depends(require_tab("vkpi", "write"))) -> dict[str, Any]:
    """阶段②·Agent 建议链 Durable Workflow:生成今日建议→汇总→留痕(可恢复;执行仍需人审)。"""
    from app.domains.actions import agent_cycle_workflow

    return agent_cycle_workflow.start_agent_cycle(staff)


@router.get("/workflow/{run_id}")
def workflow_run(run_id: int, staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """P2 · Durable Workflow:读一个 run 的状态 + 各 step(可观测每步为什么/到哪了)。"""
    from app.domains.platform import workflow_engine

    return workflow_engine.get_run(int(run_id))


@router.get("/event-ledger")
def event_ledger_recent(
    event_type: str = "",
    entity_type: str = "",
    entity_id: str = "",
    limit: int = 50,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """P1 · 统一事件总线:近期事件流 / 某实体事件流(可追溯每一步)。"""
    from app.domains.platform import event_ledger

    if entity_type and entity_id:
        return {"available": True, "items": event_ledger.by_entity(entity_type, entity_id, limit=int(limit))}
    return event_ledger.recent(limit=int(limit), event_type=str(event_type))


@router.get("/token-broker/status")
def token_broker_status(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """I1 · API Token Broker 状态:各 provider 可用/冷却/耗尽计数(只读;表内零密钥)。"""
    from app.domains.platform import token_broker

    return token_broker.broker_status()


@router.get("/worker-lease/status")
def worker_lease_status(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """I2 · Worker Lease 状态:leased/completed/expired/released 计数(只读)。"""
    from app.domains.platform import worker_lease

    return worker_lease.lease_status()


@router.get("/classify-input")
def classify_input(q: str, staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """K1 · 统一智能输入分类:URL/账号/产品需求/自然语言 → kind + 建议路由(零 LLM/DB)。"""
    from app.domains.intelligence import unified_input

    return unified_input.classify_input(q)


@router.post("/plan/{plan_id}/materialize")
def materialize_plan(plan_id: int, staff=Depends(require_tab("vkpi", "write"))) -> dict[str, Any]:
    """H5 · plan→action:把计划步骤物化成 Action Inbox 可审批项(零自动执行,人审后才跑)。"""
    return orchestrator.materialize_plan_to_inbox(int(plan_id), staff=staff)


@router.get("/capabilities")
def capabilities(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """Agent-OS 能力清单(自描述,前端发现入口):系统能干什么 + 端点 + 数据就绪度。"""
    from app.domains.agents import capabilities as caps

    return caps.get_capabilities(staff)


@router.get("/learning-status")
def learning_status(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """学习闭环状态:动作沉淀 + 反馈 + 推荐漏斗 + 成熟度(只读,看"系统在学什么")。"""
    from app.domains.access import scope
    from app.domains.memory import learning_signals

    if not scope.can_view_all(staff):  # A2 防越权:系统级学习视图仅管理层
        raise HTTPException(status_code=403, detail="仅管理层可见")
    return learning_signals.get_learning_status(staff)


@router.get("/workspace-digest")
def workspace_digest(
    action_limit: int = 5,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """里程碑1 · 运营每日 digest:今日建议 + 主链路就绪 + 组合 ROI + 最近执行(只读聚合)。"""
    from app.domains.dashboard import workspace_digest as wd

    return wd.get_workspace_digest(staff, action_limit=int(action_limit))


@router.get("/kol/{kol_pool_id}/provenance")
def kol_provenance(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """路线3 · 系统"为什么记住这个 KOL"(视频/深析/项目/ROI/漏斗 provenance,只读)。

    让 Agent 建议能引用来源(来自哪条视频 / 哪个项目),而非只看实时 query。零触 viltrox_fit_score。
    """
    from app.domains.memory import provenance

    return provenance.get_kol_provenance(int(kol_pool_id), staff=staff)
