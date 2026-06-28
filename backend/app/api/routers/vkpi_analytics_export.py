"""N6 数据问数 · 只读分析问答路由(加性新文件)。

暴露白名单意图问数能力:
  - POST /api/admin/vkpi/analytics/ask  {question, range?, source?}
        -> {intent, columns, rows, sql_explain}
  - GET  /api/admin/vkpi/analytics/intents
        -> 可问问题清单(供前端下拉 / 帮助)

全部走 require_tab("vkpi", "admin") 权限。所有查询都委派给 query_planner,
后者保证:只读 SELECT、仅白名单表、用户文本绝不进 SQL、参数化下推。本路由
不直接拼 SQL,也不动 fit_score。

【不改 main.py】注册行见任务回报 register_lines。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.domains.analytics import query_planner


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-analytics-export"])


@router.get("/analytics/intents")
def analytics_intents(staff=Depends(require_tab("vkpi", "admin"))) -> dict[str, Any]:
    """列出可问的白名单问题及其参数说明。"""
    return {"intents": query_planner.list_intents()}


@router.post("/analytics/ask")
def analytics_ask(
    payload: dict[str, Any] = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict[str, Any]:
    """把自然语言问题(或显式 intent)映射到白名单查询并执行,回传结构化结果。

    请求体:
      - question: 自然语言问题(可选,与 intent 二选一)
      - intent:   显式意图键(可选,必须在白名单内)
      - range:    区间天数(可选,默认 30,夹取 1..365)
      - source:   地区/来源短码(可选,仅部分意图使用)
    """
    question = payload.get("question")
    intent = payload.get("intent")
    if not question and not intent:
        raise HTTPException(status_code=400, detail="question or intent is required")

    range_days = payload.get("range", query_planner.DEFAULT_RANGE_DAYS)
    source = payload.get("source")

    try:
        result = query_planner.run(
            get_conn(),
            question=str(question) if question is not None else None,
            intent=str(intent) if intent is not None else None,
            range_days=range_days,
            source=source,
        )
    except ValueError as exc:
        # query_planner 的安全断言失败 -> 400(绝不把内部异常细节外泄成 500)。
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result.get("intent") is None:
        # 未命中任何白名单意图:200 友好回复 + 可问清单,便于前端引导。
        return {
            "intent": None,
            "columns": [],
            "rows": [],
            "sql_explain": "",
            "message": "no matching whitelisted intent",
            "available_intents": result.get("available_intents", []),
        }
    return result
