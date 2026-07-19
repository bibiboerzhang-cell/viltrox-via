"""domains/market/mention_sentiment_annotate.py — mentions 情感批注(打包,默认 dry-run)。

背景(2026-07-19 挂账刀③):vkpi_market_mentions.sentiment 建列以来 267/267 全空串——
两条写入链(listening_executors._persist_listening_posts 与 signal_write_package)都写死 ""
占位,富化环节从未接线。本模块是 sentiment_annotate(vkpi_comments 版,已评审)的
mentions 兄弟:同一套打包 prompt/校验/解析/停跑纪律,只换取数与落库两端——
取 mention_text、直写 mentions 自带的 sentiment 列(label 全词),score/aspects/model
进 metadata_json(不动表结构)。

安全护栏(与参照版完全一致):
- dry_run=True 默认:只报 pending/计划调用数/预估成本,零 LLM 零落库。
- 单 run 硬上限沿用 VKPI_SENTIMENT_ANNOTATE_MAX_PER_RUN(默认 200);267 行全量 <$0.01。
- 走 llm_production.generate_json + 同一 model_registry 任务绑定(vkpi_sentiment_annotate);
  预算闸拦/无 key/绑定错配 → halt 停跑,绝不写假中性占坑。
- 幂等:UPDATE 带 sentiment 空守卫,只填空不覆盖;解析不出的行留空下轮重进队列。
- 红线:零触 viltrox_fit_score / rule_v0;additive,不改表结构。
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.coerce import _loads
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.market.sentiment_annotate import (
    LABEL_MAP,
    OUTPUT_TOKENS_PER_COMMENT,
    OUTPUT_TOKENS_SLACK,
    PROMPT_VERSION,
    TASK_BINDING,
    _chunked,
    _est_tokens,
    _estimate_cost_usd,
    _failure_code,
    _is_model_output_failure,
    _sentiment_binding,
    build_packed_prompt,
    parse_batch_response,
    _valid_batch_payload,
    pack_size,
    run_hard_cap,
)
from app.platform import llm_production

logger = get_logger("viltrox.domains.market.mention_sentiment_annotate")

PURPOSE = "vkpi_mention_sentiment"  # 独立 cost_scope(cron:vkpi_mention_sentiment),绑定与参照版共用

_PENDING_WHERE = (
    "(sentiment IS NULL OR TRIM(sentiment) = '') "
    "AND mention_text IS NOT NULL AND TRIM(mention_text) <> ''"
)


def _count_pending(conn: Any) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM vkpi_market_mentions WHERE {_PENDING_WHERE}").fetchone()
    return int(dict(row).get("n") or 0) if row else 0


def _select_pending(conn: Any, limit: int) -> list[dict[str, Any]]:
    # 随机序取批,同参照版:毒包不永远排头,好包多轮重跑自然排空。
    # mention_text 取别名 comment_text,让 build_packed_prompt/parse 原样复用。
    rows = conn.execute(
        f"""
        SELECT id, mention_text AS comment_text, metadata_json
        FROM vkpi_market_mentions
        WHERE {_PENDING_WHERE}
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in (rows or [])]


def _persist_mention(
    conn: Any,
    mention_id: int,
    entry: dict[str, Any],
    *,
    current_metadata: Any,
    llm_provider: str,
    llm_model: str,
) -> None:
    label = LABEL_MAP.get(str(entry.get("label") or "").strip().lower()) or "neutral"
    meta = _loads(current_metadata, {})
    if not isinstance(meta, dict):
        meta = {}
    meta.update(
        {
            "sentiment_score": float(entry.get("score") or 0.0),
            "sentiment_aspects": [str(a) for a in (entry.get("aspects") or [])][:3],
            "sentiment_model": f"{llm_provider}/{llm_model}",
            "sentiment_prompt_version": PROMPT_VERSION,
        }
    )
    conn.execute(
        """
        UPDATE vkpi_market_mentions
        SET sentiment = ?, metadata_json = ?
        WHERE id = ? AND (sentiment IS NULL OR TRIM(sentiment) = '')
        """,
        (label, json.dumps(meta, ensure_ascii=False), int(mention_id)),
    )


def annotate_mentions_batch(
    batch_size: int = 200,
    *,
    dry_run: bool = True,
    conn: Any = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """mentions 情感批注一轮。控制流照抄 sentiment_annotate.annotate_batch。"""
    conn = conn if conn is not None else get_conn()
    cap = run_hard_cap()
    take = max(1, min(int(batch_size or 200), cap))
    pack = pack_size()

    pending_total = _count_pending(conn)
    rows = _select_pending(conn, take)
    meta_by_id = {int(r["id"]): r.get("metadata_json") for r in rows}
    chunks = _chunked(rows, pack)
    prompts = [build_packed_prompt(c) for c in chunks]

    est_in = sum(_est_tokens(p) for p in prompts)
    est_out = len(rows) * OUTPUT_TOKENS_PER_COMMENT + len(chunks) * OUTPUT_TOKENS_SLACK
    try:
        provider_pref, model_pref = _sentiment_binding()
        binding_error = ""
    except ValueError as exc:
        provider_pref = "google"
        model_pref = ""
        binding_error = str(exc)
    summary: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "live",
        "prompt_version": PROMPT_VERSION,
        "preferred_provider": provider_pref,
        "preferred_model": model_pref,
        "binding_error": binding_error,
        "pending_total": pending_total,
        "selected": len(rows),
        "llm_calls_planned": len(chunks),
        "pack_size": pack,
        "hard_cap": cap,
        "estimated_input_tokens": est_in,
        "estimated_output_tokens": est_out,
        "estimated_cost_usd": round(_estimate_cost_usd(est_in, est_out, provider_pref), 6),
    }
    if dry_run:
        summary["note"] = "dry_run:未调用 LLM、未写库。dry_run=False 才真跑。"
        return summary

    annotated = 0
    skipped_unparsed = 0
    halted_reason = ""
    providers_used: set[str] = set()
    aspect_counts: dict[str, int] = {}

    for attempt_index, (chunk, prompt) in enumerate(zip(chunks, prompts), start=1):
        expected = {int(it["id"]) for it in chunk}
        max_out = min(4000, len(chunk) * OUTPUT_TOKENS_PER_COMMENT + OUTPUT_TOKENS_SLACK)
        response: dict[str, Any] = {}
        accepted = False
        for retry_index in range(2):  # 仅模型输出层失败原地重试一次;结构性失败不重试
            try:
                response = llm_production.generate_json(
                    prompt,
                    provider=provider_pref,
                    model=model_pref,
                    purpose=PURPOSE,
                    max_output_tokens=max_out,
                    triggered_by="scheduler:vkpi_market_mention_sentiment",
                    staff=staff,
                    required_keys=("items",),
                    validator=lambda value, ids=expected: _valid_batch_payload(value, ids),
                    metadata={
                        "task_binding": TASK_BINDING,
                        "surface": "market_sentiment",
                        "pipeline": "mention_sentiment_annotate_v1",
                        "pack": len(chunk),
                        "phase": "analysis",
                        "subphase": "mention_sentiment_annotation",
                        "attempt_index": attempt_index + retry_index,
                        "binding_retry": retry_index,
                        "model_level_fallback": False,
                        "total": len(chunks),
                        "target_label": f"mentions {min(expected)}-{max(expected)}",
                    },
                )
            except Exception as exc:  # strict AI-off/readiness failure; 主链不受影响
                response = {
                    "status": "failed",
                    "reason": str(exc)[:120] or type(exc).__name__,
                    "provider": "rule_v0",
                    "model": "rule_v0",
                    "json": None,
                }
            status = str(response.get("status") or "")
            accepted = (
                status == "success"
                and str(response.get("provider") or "").strip().lower() == provider_pref
                and str(response.get("model") or "").strip() == model_pref
                and _valid_batch_payload(response.get("json"), expected)
            )
            if accepted:
                break
            if retry_index == 0 and _is_model_output_failure(response):
                logger.info(
                    "mention_sentiment.binding_retry",
                    extra={"reason": _failure_code(response), "attempt_index": attempt_index},
                )
                continue
            break
        if not accepted:
            halted_reason = _failure_code(response)
            logger.warning(
                "mention_sentiment.halted",
                extra={"reason": halted_reason, "annotated_so_far": annotated},
            )
            break
        payload = response.get("json") or {}
        parsed = parse_batch_response(json.dumps(payload.get("items") or [], ensure_ascii=False), expected)
        providers_used.add(str(response.get("provider") or "unknown"))
        for mid in sorted(expected):
            entry = parsed.get(mid)
            if not entry:
                skipped_unparsed += 1  # sentiment 留空,下轮自动重进队列
                continue
            _persist_mention(
                conn,
                mid,
                entry,
                current_metadata=meta_by_id.get(mid),
                llm_provider=str(response.get("provider") or "unknown"),
                llm_model=str(response.get("model") or "unknown"),
            )
            annotated += 1
            for a in entry.get("aspects") or []:
                aspect_counts[a] = aspect_counts.get(a, 0) + 1

    if hasattr(conn, "commit"):
        conn.commit()

    summary.update(
        {
            "annotated": annotated,
            "skipped_unparsed": skipped_unparsed,
            "halted_reason": halted_reason,
            "providers_used": sorted(providers_used),
            "aspects_top": dict(sorted(aspect_counts.items(), key=lambda kv: -kv[1])[:20]),
        }
    )
    return summary
