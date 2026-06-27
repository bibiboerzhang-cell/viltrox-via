"""KOL 建档链 Durable Workflow(P2 落业务,串起 Apify 发现/落库/富集/记忆)。

链:联邦发现+落库 → 富集新档(Apify) → 记忆留痕。跑在 workflow_engine 上:可恢复、可观测、入事件流。
红线:步骤只编排既有 service,落新档诚实标 discovered,零触 viltrox_fit_score;商业源/富集未配置则诚实降级。
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

Step = tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]


def build_kol_onboarding_steps(query: str, staff: dict[str, Any] | None = None) -> list[Step]:
    """KOL 建档 3 步(复用 enroll / apify_enrich / agent_memory_writer)。"""

    def s_discover(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.discovery import enroll

        r = enroll.federated_discover_and_enroll(query, limit=20, staff=staff)
        return {"found": r.get("found", 0), "enrolled": r.get("enrolled", 0), "enrolled_ids": r.get("enrolled_ids", [])}

    def s_enrich(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.discovery import apify_enrich

        ids = list(state.get("enrolled_ids", []) or [])[:10]
        enriched = 0
        for kid in ids:
            if apify_enrich.enrich_kol(int(kid)).get("status") == "ok":
                enriched += 1
        return {"enrich_attempted": len(ids), "enriched": enriched}

    def s_memory(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.memory import agent_memory_writer

        agent_memory_writer.record_signal(
            action_kind="search", entity_type="discovery", entity_id=str(query), staff=staff,
            reason=f"KOL建档链:发现{state.get('found', 0)}/落库{state.get('enrolled', 0)}",
            detail={"query": query, "found": state.get("found", 0), "enrolled": state.get("enrolled", 0)},
        )
        return {"memory_recorded": True}

    return [("federated_discover", s_discover), ("enrich_new", s_enrich), ("write_memory", s_memory)]


def start_kol_onboarding(query: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """起一轮 KOL 建档:建 run → 跑三步(可恢复)。"""
    q = str(query or "").strip()
    if not q:
        return {"status": "empty_query"}
    from app.domains.platform import workflow_engine

    started = workflow_engine.start_run("kol_onboarding", input={"query": q}, entity_type="discovery", entity_id=q)
    run_id = started.get("run_id")
    if not run_id:
        return {"status": "unavailable", "detail": started}
    res = workflow_engine.run(int(run_id), build_kol_onboarding_steps(q, staff))
    return {"run_id": run_id, **res}


def resume_kol_onboarding(run_id: int, query: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """续跑失败/暂停的建档 run(从 current_step 接上)。"""
    from app.domains.platform import workflow_engine

    return workflow_engine.run(int(run_id), build_kol_onboarding_steps(str(query or ""), staff))
