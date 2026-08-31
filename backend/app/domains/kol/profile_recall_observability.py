"""召回的秤与台账登记点(只加观测,绝不改召回行为)。

从 :mod:`profile_recall_orchestration` 拆出来,原因有二:编排文件已经贴着
800 行软棘轮,而这里的东西本来就自成一件事 —— 「这次召回每段花了多久」
和「这次查询向量登记了没有」都不参与任何选人决策。

两条硬约束,改这个文件的人先读:

* **计时零成本**:全部只读 ``perf_counter``,不加 DB 往返、不加查询、
  不改任何返回给用户的名次或人选。
* **观测不许反噬**:登记台账的每条路径都吞异常。台账坏掉时,一次本来能
  出结果的搜索必须照常出结果。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol.profile_recall_orchestration_contract import RecallRequest

# vkpi_llm_calls.purpose:embedding 调用此前一行都没有,「查询向量花了多少 /
# 多久」只能靠预算台账的聚合数反推。用途名固定在这里,查询侧才有稳定抓手。
EMBEDDING_CALL_PURPOSE = "vkpi_kol_recall_query_embedding"


def elapsed_ms(started: float, deps: Any) -> float:
    """perf_counter 差值转毫秒。纯内存读时钟,零 DB 往返。"""
    return round((deps.perf_counter() - started) * 1000.0, 3)


def register_embedding_call(
    *,
    query_text: str,
    embedding_meta: Any,
    latency_ms: float,
    status: str,
    failure_reason: str = "",
    deps: Any,
) -> None:
    """把这次查询向量登进 ``vkpi_llm_calls``。

    ``deps._embed_query`` 内部已经 ``record_cost`` 过一笔预算账,所以这里走
    ``record_embedding_call``(恒 ``cost_tag=None``)只补一行调用记录,
    一分钱都不会被算第二遍。
    """
    try:
        from app.platform import llm_gateway

        meta = embedding_meta if isinstance(embedding_meta, dict) else {}
        llm_gateway.record_embedding_call(
            provider="openai",
            model=str(
                meta.get("embedding_model") or getattr(deps, "EMBEDDING_MODEL", "") or ""
            ),
            purpose=EMBEDDING_CALL_PURPOSE,
            latency_ms=latency_ms,
            input_tokens=int(meta.get("query_embedding_tokens") or 0),
            cost_usd=float(meta.get("query_embedding_cost_usd_estimate") or 0.0),
            status=status,
            prompt=str(query_text or ""),
            metadata={
                "lane": "kol_profile_recall",
                "embedding_transport": str(meta.get("embedding_transport") or ""),
                **({"failure_reason": failure_reason} if failure_reason else {}),
            },
        )
    except Exception:  # noqa: BLE001 - 观测绝不反过来炸召回
        deps.logger.warning("recall embedding call ledger failed", exc_info=True)


def recall_stage_timing(
    request: RecallRequest,
    context: dict[str, Any],
    retrieval: dict[str, Any],
    hydration: dict[str, Any],
    ranking: dict[str, Any],
    deps: Any,
) -> dict[str, float]:
    """一次召回的分段秤,**每条车道都出**,不再只挂在 smart-local 合同里。

    此前这段计时写在 ``_finalize_smart_local`` 里,而那个函数在
    ``local_qualification is None``(非 smart-local 车道)时提前 return ——
    整条腿零计时。现在由 ``run_recall_pipeline`` 统一算一次,两个消费方
    (诊断块 + smart-local 合同)读同一份数,不会互相漂。

    检索段的拆分键(``retrieve_*``)一律是数字且平铺,两个持久化投影
    (会话摘要的 stage_timing 只收 float、诊断块原样透传)都能原样接住。
    """
    completed_at = deps.perf_counter()
    timing: dict[str, float] = {
        "resolve_query_ms": round(
            (context["resolved_at"] - request.recall_started) * 1000.0, 3
        ),
        "retrieve_ms": round(
            (retrieval["retrieved_at"] - context["resolved_at"]) * 1000.0, 3
        ),
        "load_evidence_ms": round(
            (hydration["evidence_loaded_at"] - retrieval["retrieved_at"]) * 1000.0, 3
        ),
        "evidence_gate_ms": round(
            (ranking["gated_at"] - hydration["evidence_loaded_at"]) * 1000.0, 3
        ),
        "rank_and_select_ms": round(
            (completed_at - ranking["gated_at"]) * 1000.0, 3
        ),
        "total_ms": round((completed_at - request.recall_started) * 1000.0, 3),
    }
    breakdown = retrieval.get("stage_timing")
    if isinstance(breakdown, dict):
        for key, value in breakdown.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                timing[str(key)] = float(value)
    return timing
