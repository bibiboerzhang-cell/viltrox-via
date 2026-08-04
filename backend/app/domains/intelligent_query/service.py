"""Ask & Find v2 deterministic query orchestration."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.intelligent_query.contracts import (
    QueryScopeDenied,
    QueryValidationError,
    empty_response,
    normalize_request,
)
from app.domains.intelligent_query.handlers import HANDLERS
from app.domains.intelligent_query.intent import resolve_intent
from app.domains.intelligent_query.repository import actual_scope_context


logger = get_logger(__name__)


def _clarification(
    request: Any,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    scope_context = actual_scope_context(request, staff)
    response = empty_response(request, intent="unknown", scope=scope_context)
    is_en = request.locale == "en-US"
    response.update(
        {
            "status": "needs_clarification",
            "answer": (
                "I can currently answer KOL counts, count KOLs by video topic, search visible projects, or summarize the exact seven-day Viltrox market sample. Please make the target explicit."
                if is_en
                else "目前可以查询 KOL 数量、按视频主题统计 KOL、搜索当前账号可见的项目，或总结严格七天的 Viltrox 市场样本。请补充要查的对象。"
            ),
            "degraded_reason": "intent_not_resolved",
            "missing_fields": [
                {
                    "field": "intent",
                    "reason": (
                        "the request did not match a supported deterministic intent"
                        if is_en
                        else "问题未匹配当前支持的确定性查询意图"
                    ),
                    "impact": (
                        "no database query or LLM call was executed"
                        if is_en
                        else "本次未执行数据库查询或 LLM 调用"
                    ),
                }
            ],
            "actions": [
                {
                    "type": "suggest_query",
                    "label": "How many KOLs are in the pool?" if is_en else "目前 KOL 数量是多少？",
                    "params": {"query": "How many KOLs are in the pool?" if is_en else "目前 KOL 数量是多少？"},
                    "requires_approval": False,
                },
                {
                    "type": "suggest_query",
                    "label": "KOLs with 26mm EVO videos" if is_en else "多少 KOL 做过 26mm EVO 视频？",
                    "params": {"query": "How many KOLs reviewed 26mm EVO?" if is_en else "多少 KOL 做过 26mm EVO 视频？"},
                    "requires_approval": False,
                },
            ],
        }
    )
    response["coverage"].update(
        {
            "status": "unknown",
            "matched_entities": 0,
            "evidence_count": 0,
            "notes": [
                "Unknown intent fails closed without querying broad data or invoking a model."
                if is_en
                else "未知意图按安全策略关闭，不查询宽范围数据，也不调用模型。"
            ],
        }
    )
    return response


def execute_query(
    payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    conn: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one evidence query without writes, providers, workers or LLMs.

    Request validation and staff-scope resolution happen before the first DB
    statement.  Query handlers only expose figures produced by SQL/Python
    deterministic aggregation; an LLM is never allowed to calculate numbers.
    """
    started = time.perf_counter()
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    request = normalize_request(payload, now=now_utc)
    # Resolve requested scope before deciding whether a broad data handler may
    # run.  This is deliberately ahead of get_conn() and all SQL.
    actual_scope_context(request, staff)
    intent = resolve_intent(request.query, request.filters)
    if intent == "unknown":
        response = _clarification(request, staff)
    else:
        if conn is None:
            from app.db.connection import get_conn

            conn = get_conn()
        handler = HANDLERS[intent]
        try:
            response = handler(conn, request, staff, now=now_utc)
        except (QueryValidationError, QueryScopeDenied):
            raise
        except Exception as exc:  # noqa: BLE001 - stable error contract, no internal detail leak
            logger.exception(
                "intelligent_query.failed intent=%s request_id=%s error_type=%s",
                intent,
                request.request_id,
                type(exc).__name__,
            )
            response = empty_response(
                request,
                intent=intent,
                scope=actual_scope_context(request, staff),
            )
            response.update(
                {
                    "status": "error",
                    "answer": (
                        "The evidence query is temporarily unavailable; no result was inferred."
                        if request.locale == "en-US"
                        else "证据查询暂时不可用，本次没有把故障推断成零结果。"
                    ),
                    "degraded_reason": "query_execution_failed",
                }
            )
            response["coverage"].update(
                status="unknown",
                notes=[
                    "Query failed; zero was not substituted for an unavailable source."
                    if request.locale == "en-US"
                    else "查询失败；未将不可用数据源错误替换成零结果。"
                ],
            )
            response["missing_fields"] = [
                {
                    "field": "query_result",
                    "reason": (
                        "the deterministic evidence query failed"
                        if request.locale == "en-US"
                        else "确定性证据查询执行失败"
                    ),
                    "impact": (
                        "facts are unavailable and must not be treated as zero"
                        if request.locale == "en-US"
                        else "事实数据不可用，不能按零结果理解"
                    ),
                }
            ]
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
    response["trace"]["took_ms"] = elapsed_ms
    # Top-level and trace IDs intentionally match for frontend evidence cards
    # and support receipts.
    response["request_id"] = request.request_id
    response["trace"]["request_id"] = request.request_id
    return response


__all__ = ["QueryScopeDenied", "QueryValidationError", "execute_query"]
