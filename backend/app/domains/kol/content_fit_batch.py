"""domains/kol/content_fit_batch.py — content_fit_v1 的「过夜批量」兄弟。

复用 content_fit_analysis 的采集+组 prompt+落库内部件:
- build 阶段(submit job):攒 batch item(custom_id=kol_pool_id,prompt,紧凑 meta)。
- collect 阶段(poller 回调 consume):解析 LLM 文本→拼与同步路径**完全同构**的 result 信封→
  `_write_cache` 写同一 `vkpi_analysis_cache` 的 content_fit_v1 行(ON CONFLICT 升级,幂等)。

与交互入口 `analyze_content_fit` 同构、互不干扰:
- **不碰串行 worker**(additive 新调度任务直接组包+落库,绕过 apify_jobs 队列)。
- **零触 viltrox_fit_score / rule_v0**;只写本域 content_fit_v1 命名空间。
- 无视频证据者 build_item 返 None(跳过,不烧 batch、不杜撰)。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import content_fit_analysis as cfa
from app.platform import llm_batch

logger = get_logger("viltrox.domains.kol.content_fit_batch")

CONSUMER = "content_fit_v1"


# ── build:攒 batch item ────────────────────────────────────────────


def build_item(
    conn: Any,
    kol_pool_id: int,
    *,
    product_sku: str | None = None,
    product_persona: str | None = None,
) -> dict[str, Any] | None:
    """组一个 content_fit batch item;无 KOL 或无视频证据 → None(跳过)。"""
    kid = int(kol_pool_id)
    kol = cfa._kol_row(conn, kid)
    if not kol:
        return None
    videos = cfa._video_analyses(conn, kid)
    if not videos:
        return None  # 诚实:无 ready 视频证据,不烧 batch
    comments = cfa._fan_comments(conn, kid)
    dimensions = cfa._dimensions_11(kid)
    normalized_product_sku = cfa.normalize_product_sku(product_sku)
    derive_method = cfa.content_fit_derive_method(normalized_product_sku)
    product = cfa._resolve_product(normalized_product_sku, product_persona)
    prompt = cfa._build_prompt(kol, product, videos, comments, dimensions)
    # Anthropic batch custom_id must be unique inside one batch and only uses a
    # conservative character set.  The scoped derive suffix is already a
    # bounded hex digest, so reuse it instead of placing a raw SKU in the ID.
    custom_id = str(kid)
    if normalized_product_sku:
        custom_id = f"{kid}-{derive_method.rsplit(':', 1)[-1]}"
    return {
        "custom_id": custom_id,
        "prompt": prompt,
        "max_output_tokens": cfa.MAX_OUTPUT_TOKENS,
        "meta": {
            "kol_pool_id": kid,
            "video_count": len(videos),
            "comment_count": len(comments),
            "has_dimensions_11": bool(dimensions),
            "video_evidence_ids": [v.get("evidence_id") for v in videos],
            "product": product,
            "product_sku": normalized_product_sku or None,
            "derive_method": derive_method,
        },
    }


def select_kols_needing_refresh(conn: Any, *, limit: int = 150) -> list[int]:
    """有 ready 视频深析、但缺 content_fit_v1 ready cache 的 KOL(优先补缺口)。"""
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT e.kol_pool_id AS kid
            FROM vkpi_analysis_cache v
            JOIN vkpi_kol_video_evidence e ON e.id = CAST(v.target_id AS BIGINT)
            WHERE v.target_type='video' AND v.derive_method=? AND v.status='ready'
              AND e.kol_pool_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM vkpi_analysis_cache c
                  WHERE c.target_type='kol' AND c.derive_method=?
                    AND c.target_id = e.kol_pool_id::TEXT AND c.status='ready'
              )
            ORDER BY e.kol_pool_id
            LIMIT ?
            """,
            (cfa.VIDEO_DERIVE_METHOD, cfa.DERIVE_METHOD, int(limit)),
        ).fetchall()
        return [int(dict(r)["kid"]) for r in (rows or []) if dict(r).get("kid") is not None]
    except Exception:
        logger.warning("content_fit_batch.select_failed", exc_info=True)
        return []


# ── collect:poller 回调,落库 ───────────────────────────────────────


def consume(results_by_id: dict[str, str], request_map: dict[str, Any]) -> dict[str, Any]:
    """把 batch 回来的文本拼成同构 result 信封并 _write_cache。与 analyze_content_fit 一致。"""
    conn = get_conn()
    written = 0
    failed = 0
    for cid, text in (results_by_id or {}).items():
        meta = (request_map or {}).get(cid) or {}
        try:
            kid = int(meta.get("kol_pool_id") or str(cid).split("-", 1)[0])
            parsed = cfa._parse_llm_json(text)
            if not parsed:
                failed += 1
                continue
            result = {
                "schema_version": cfa.DERIVE_METHOD,
                "verdict_status": "ready",
                "creator_type": cfa._text(parsed.get("creator_type"), 300),
                "content_summary": cfa._text(parsed.get("content_summary"), 1200),
                "audience_signal": cfa._text(parsed.get("audience_signal"), 1200),
                "fit_verdict": cfa._text(parsed.get("fit_verdict"), 40),
                "fit_reasons": [cfa._text(x, 400) for x in (parsed.get("fit_reasons") or []) if str(x).strip()][:8],
                "confidence": cfa._coerce_confidence(parsed.get("confidence")),
                "evidence_basis": {
                    "video_count": int(meta.get("video_count") or 0),
                    "comment_count": int(meta.get("comment_count") or 0),
                    "has_dimensions_11": bool(meta.get("has_dimensions_11")),
                    "video_evidence_ids": meta.get("video_evidence_ids") or [],
                },
                "product_context": meta.get("product") or {},
                "provenance": {
                    "model": "anthropic-batch",
                    "provider": "anthropic",
                    "fallback_used": False,
                    "generated_at": cfa._utcnow(),
                    "via": "batch",
                },
            }
            cfa._write_cache(
                conn,
                kid,
                result,
                model="anthropic-batch",
                cost_usd=0.0,
                triggered_by_user_id=None,
                product_sku=meta.get("product_sku"),
            )
            written += 1
        except Exception:
            logger.warning("content_fit_batch.consume_entry_failed", extra={"custom_id": cid}, exc_info=True)
            failed += 1
    return {"written": written, "failed": failed, "total": len(results_by_id or {})}


# import 时自注册回收消费者(poller 在 dispatch 前 import 本模块以触发注册)。
llm_batch.register_consumer(CONSUMER, consume)
