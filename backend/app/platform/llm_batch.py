"""platform/llm_batch.py — Anthropic Message Batches 异步批量 LLM 提交/回收。

同步路径走 `llm_gateway.invoke`;本模块是它的「过夜批量」兄弟:对**没人等**的大批量任务
(content_fit 刷新等),攒一批 prompt 一次提交 Anthropic Message Batches——同模型
(claude-sonnet-5)、**输出与同步路径一致**,仅以延迟(SLA≤24h,通常分钟级)换 **50% 折扣**。
poller(scheduler.job_llm_batch_poll)轮询回收后,按 consumer 注册表 dispatch 落各自域表。

红线 / 安全:
- **仅批量、非实时**:绝不用于交互路径(用户等结果的不能用)。
- 零触 viltrox_fit_score / rule_v0;consumer 自行落各自域表(本模块不写业务表)。
- 成本回收进 budget_guard 台账(护栏① 不被绕过):回收时按 batch 实际用量
  `record_cost`(已含 50% Batch 折扣)。
- 本地环境 Anthropic SDK 无代理(被墙)→ **提交只在云端 scheduler 跑**;本地仅可 dry-run
  组包/落库逻辑(submit 会因网络失败返回 None,不抛、不阻断)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.core.model_pricing import PRICING_USD_PER_1M_TOKENS
from app.db.connection import get_conn
from app.platform.llm_production_anthropic_helpers import anthropic_create_kwargs

logger = get_logger("viltrox.platform.llm_batch")

# consumer 注册表:name -> fn(results_by_custom_id, request_map) -> summary dict
_CONSUMERS: dict[str, Callable[[dict[str, str], dict[str, Any]], dict[str, Any]]] = {}

# 估价(美元/百万 token)从 model_pricing 按 CLAUDE_MODEL 精确查;查不到按
# claude-sonnet-5 正式价 2/10 兜底。仅用于台账估算,Batch 实付 = ×0.5。
_FALLBACK_PRICE = {"input": 2.0, "output": 10.0}
_BATCH_DISCOUNT = 0.5


def _model_price() -> tuple[float, float]:
    row = PRICING_USD_PER_1M_TOKENS.get(str(CLAUDE_MODEL or "").strip().lower(), _FALLBACK_PRICE)
    return float(row.get("input", _FALLBACK_PRICE["input"])), float(row.get("output", _FALLBACK_PRICE["output"]))

# 安全阈:超过该小时数仍未 ended 的批次判为 expired(防永久挂账)。
_MAX_AGE_HOURS = 48


def register_consumer(name: str, fn: Callable[[dict[str, str], dict[str, Any]], dict[str, Any]]) -> None:
    """注册一个回收消费者。content_fit_batch 等模块 import 时自注册。"""
    _CONSUMERS[str(name)] = fn


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema() -> None:
    """SQLite 运行时自建同构表;Postgres 走迁移 166(IF NOT EXISTS 幂等)。"""
    try:
        conn = get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_llm_batches (
                id             BIGSERIAL   PRIMARY KEY,
                batch_id       TEXT        NOT NULL UNIQUE,
                provider       TEXT        NOT NULL DEFAULT 'anthropic',
                consumer       TEXT        NOT NULL,
                purpose        TEXT        NOT NULL DEFAULT '',
                status         TEXT        NOT NULL DEFAULT 'in_progress',
                request_count  INTEGER     NOT NULL DEFAULT 0,
                request_map    JSONB       NOT NULL DEFAULT '{}'::jsonb,
                result_summary JSONB,
                error          TEXT        NOT NULL DEFAULT '',
                submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                collected_at   TIMESTAMPTZ
            )
            """
        )
        conn.commit()
    except Exception:
        logger.debug("llm_batch.ensure_schema_skipped", exc_info=True)


def _batches_api() -> Any | None:
    """拿 anthropic SDK 的 messages.batches(本地无代理会拿到 client 但调用时网络失败)。"""
    try:
        from app.services.ai.clients.claude_client import get_claude_client

        client = get_claude_client()
        if client is None:
            return None
        batches = getattr(getattr(client, "messages", None), "batches", None)
        return batches
    except Exception:
        logger.warning("llm_batch.client_unavailable", exc_info=True)
        return None


# ── 提交 ───────────────────────────────────────────────────────────


def submit_anthropic_batch(
    items: list[dict[str, Any]],
    *,
    consumer: str,
    purpose: str = "",
    cost_scope: str = "",
) -> str | None:
    """提交一批 prompt 为 Anthropic Message Batch。

    items: [{custom_id:str, prompt:str, max_output_tokens:int, meta:dict}]
    成功落 vkpi_llm_batches(status='in_progress')并返回 batch_id;失败返回 None(不抛)。
    """
    safe_items = [it for it in (items or []) if str(it.get("prompt") or "").strip() and it.get("custom_id")]
    if not safe_items:
        return None
    ensure_schema()
    if consumer not in _CONSUMERS:
        logger.warning("llm_batch.unknown_consumer", extra={"consumer": consumer})
        # 仍允许提交?不——没有 consumer 回收等于烧钱不落库,拒绝。
        return None

    batches = _batches_api()
    if batches is None:
        logger.warning("llm_batch.submit_skipped_no_client", extra={"consumer": consumer, "n": len(safe_items)})
        return None

    # params 与同步路径(llm_production 严格边界)同一份思考策略(默认 thinking
    # disabled;env 切 adaptive),保证 batch 输出 == 同步输出的模块契约。
    requests = [
        {
            "custom_id": str(it["custom_id"]),
            "params": anthropic_create_kwargs(
                CLAUDE_MODEL,
                max(1, min(4096, int(it.get("max_output_tokens") or 1024))),
                [{"role": "user", "content": str(it["prompt"])}],
            ),
        }
        for it in safe_items
    ]
    try:
        batch = batches.create(requests=requests)
    except Exception:
        logger.warning("llm_batch.submit_failed", extra={"consumer": consumer, "n": len(safe_items)}, exc_info=True)
        return None

    batch_id = str(getattr(batch, "id", "") or "")
    if not batch_id:
        logger.warning("llm_batch.submit_no_id", extra={"consumer": consumer})
        return None

    request_map = {str(it["custom_id"]): (it.get("meta") or {}) for it in safe_items}
    try:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO vkpi_llm_batches
                (batch_id, provider, consumer, purpose, status, request_count, request_map, submitted_at)
            VALUES (?, 'anthropic', ?, ?, 'in_progress', ?, ?::jsonb, ?)
            """,
            (
                batch_id,
                str(consumer),
                str(cost_scope or purpose or ""),
                len(safe_items),
                json.dumps(request_map, ensure_ascii=False, default=str),
                _utcnow(),
            ),
        )
        conn.commit()
    except Exception:
        logger.warning("llm_batch.persist_failed", extra={"batch_id": batch_id}, exc_info=True)
        # 已提交但没落库 → poller 不会回收。记日志便于人工对账(批次仍会在 Anthropic 侧完成)。
    logger.info("llm_batch.submitted", extra={"batch_id": batch_id, "consumer": consumer, "n": len(safe_items)})
    return batch_id


# ── 回收 ───────────────────────────────────────────────────────────


def _extract_text(message: Any) -> str:
    content = getattr(message, "content", None) or []
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "") or ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts).strip()


def _record_cost(cost_scope: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = _model_price()
    cost_usd = (tokens_in / 1_000_000.0 * price_in + tokens_out / 1_000_000.0 * price_out) * _BATCH_DISCOUNT
    if cost_scope and cost_usd > 0:
        try:
            from app.domains.costs import budget_guard

            budget_guard.record_cost(
                scope=cost_scope, cost_usd=round(cost_usd, 6),
                tokens_in=tokens_in, tokens_out=tokens_out, ai_provider="anthropic",
            )
        except Exception:
            logger.debug("llm_batch.record_cost_failed", exc_info=True)
    return round(cost_usd, 6)


def _hours_since(value: Any) -> float:
    try:
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:
        return 0.0


def poll_pending_batches(*, max_batches: int = 20) -> dict[str, Any]:
    """轮询所有 in_progress 批次:ended→回收 dispatch 落库;超龄→标 expired。"""
    ensure_schema()
    batches = _batches_api()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT batch_id, consumer, purpose, request_map, submitted_at "
            "FROM vkpi_llm_batches WHERE status='in_progress' ORDER BY submitted_at LIMIT ?",
            (int(max_batches),),
        ).fetchall()
    except Exception:
        logger.debug("llm_batch.poll_select_failed", exc_info=True)
        return {"polled": 0, "collected": 0}

    polled = 0
    collected = 0
    for row in rows or []:
        polled += 1
        d = dict(row)
        bid = str(d.get("batch_id") or "")
        consumer = str(d.get("consumer") or "")
        if batches is None:
            continue
        try:
            meta = batches.retrieve(bid)
            status = str(getattr(meta, "processing_status", "") or "")
            if status != "ended":
                if _hours_since(d.get("submitted_at")) > _MAX_AGE_HOURS:
                    _mark(conn, bid, "expired", error=f"not ended after {_MAX_AGE_HOURS}h")
                continue

            results_by_id: dict[str, str] = {}
            tok_in = tok_out = 0
            errored = 0
            for entry in batches.results(bid):
                cid = str(getattr(entry, "custom_id", "") or "")
                res = getattr(entry, "result", None)
                rtype = str(getattr(res, "type", "") or "")
                if rtype == "succeeded":
                    msg = getattr(res, "message", None)
                    text = _extract_text(msg)
                    if cid and text:
                        results_by_id[cid] = text
                    usage = getattr(msg, "usage", None)
                    tok_in += int(getattr(usage, "input_tokens", 0) or 0)
                    tok_out += int(getattr(usage, "output_tokens", 0) or 0)
                else:
                    errored += 1

            request_map = _loads_map(d.get("request_map"))
            consumer_fn = _CONSUMERS.get(consumer)
            summary: dict[str, Any] = {"dispatched": False}
            if consumer_fn and results_by_id:
                try:
                    summary = consumer_fn(results_by_id, request_map) or {}
                    summary["dispatched"] = True
                except Exception:
                    logger.warning("llm_batch.consumer_failed", extra={"batch_id": bid, "consumer": consumer}, exc_info=True)
                    summary = {"dispatched": False, "error": "consumer_exception"}
            cost_usd = _record_cost(str(d.get("purpose") or ""), tok_in, tok_out)
            summary.update({"errored_entries": errored, "tokens_in": tok_in, "tokens_out": tok_out, "cost_usd": cost_usd})
            _mark(conn, bid, "collected", summary=summary)
            collected += 1
            logger.info("llm_batch.collected", extra={"batch_id": bid, "consumer": consumer, **{k: summary.get(k) for k in ("written", "failed", "cost_usd")}})
        except Exception:
            logger.warning("llm_batch.poll_entry_failed", extra={"batch_id": bid}, exc_info=True)
    return {"polled": polled, "collected": collected}


def _loads_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        out = json.loads(str(value or "{}"))
        return out if isinstance(out, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return {}


def _mark(conn: Any, batch_id: str, status: str, *, summary: dict[str, Any] | None = None, error: str = "") -> None:
    try:
        conn.execute(
            "UPDATE vkpi_llm_batches SET status=?, result_summary=?::jsonb, error=?, collected_at=? WHERE batch_id=?",
            (status, json.dumps(summary or {}, ensure_ascii=False, default=str), str(error or ""), _utcnow(), batch_id),
        )
        conn.commit()
    except Exception:
        logger.debug("llm_batch.mark_failed", extra={"batch_id": batch_id, "status": status}, exc_info=True)
