"""每日刷新 market 外部信号(竞品新品 + Reddit/Google News 热度)。

合规 / 红线:
- 仅调 `build_external_signal_smoke(execute_http_fetch=True)`:allowlisted 源、有界(limit_per_source)、
  短超时、**零 LLM**。写一份 artifact 到 runtime/ops 供 Signals & Alerts 直接展示。
- DB 写默认关:仅当 env VKPI_EXTERNAL_SIGNAL_AUTOWRITE_ENABLED 显式开启,才把有信号项
  幂等落 vkpi_market_sources/vkpi_market_mentions(raw 层,metadata 带 review_status=
  raw_unreviewed;2026-07-19 挂账刀④——此前抓取活着但零入库,rss/google_news 断供 56 天)。
- **竞品入库仍走人工审核闸**(本 job 绝不 promote / 不写竞品信号表)——保留全球最严合规设计。
- 不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domains.market import external_signal_smoke

logger = get_logger(__name__)

AUTOWRITE_ENV = "VKPI_EXTERNAL_SIGNAL_AUTOWRITE_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def persist_external_signals(report: dict[str, Any], conn: Any = None) -> dict[str, int]:
    """外部信号幂等落库(listening_executors._persist_listening_posts 同款纪律):

    只落 business_signal 项;platform+source_ref 已存在即跳过(同批自去重);
    RETURNING id 链 source→mention;整批 commit,异常整批 rollback。
    sentiment 留空由 mention 情感批注 job 统一补;score=关键词命中数归一(0-1)。
    """
    if conn is None:
        from app.db.connection import get_conn

        conn = get_conn()
    items = [it for it in (report.get("items") or []) if it.get("business_signal")]
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_sources = 0
    new_mentions = 0
    skipped = 0
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        provider = str(it.get("provider") or "").strip().lower()
        platform = "google_news" if provider == "google_news" else (provider or "rss")
        by_platform.setdefault(platform, []).append(it)
    try:
        for platform, plat_items in by_platform.items():
            refs = [str(it.get("source_uid") or it.get("source_url") or "") for it in plat_items]
            refs = [r for r in refs if r]
            existing: set[str] = set()
            if refs:
                placeholders = ",".join("?" for _ in refs)
                rows = conn.execute(
                    "SELECT source_ref FROM vkpi_market_sources "
                    f"WHERE LOWER(COALESCE(platform,''))=? AND source_ref IN ({placeholders})",
                    tuple([platform, *refs]),
                ).fetchall()
                existing = {str(dict(row).get("source_ref") or "") for row in rows}
            for it in plat_items:
                ref = str(it.get("source_uid") or it.get("source_url") or "")
                if not ref or ref in existing:
                    skipped += 1
                    continue
                existing.add(ref)
                cursor = conn.execute(
                    """
                    INSERT INTO vkpi_market_sources
                        (run_id, source_type, platform, source_ref, source_url, title, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        None,
                        str(it.get("source_type") or "external_signal")[:80],
                        platform,
                        ref,
                        str(it.get("source_url") or "")[:1000],
                        str(it.get("title") or "")[:500],
                        json.dumps(
                            {
                                "source_key": it.get("source_key"),
                                "feed_url": it.get("feed_url"),
                                "query": it.get("query"),
                                "purpose": it.get("purpose"),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                        now,
                    ),
                )
                row = cursor.fetchone()
                source_id = int(dict(row)["id"]) if row else None
                new_sources += 1
                title = str(it.get("title") or "").strip()
                summary = str(it.get("summary") or "").strip()
                mention_text = " · ".join(part for part in (title, summary) if part)[:1000]
                try:
                    from app.domains.market.competitor_brain_helpers import _find_competitor_terms

                    competitor_csv = ",".join(_find_competitor_terms(mention_text))
                except Exception:
                    competitor_csv = ""
                hits = it.get("keyword_hits") or []
                score = min(1.0, 0.2 * len(hits)) if hits else 0.2
                conn.execute(
                    """
                    INSERT INTO vkpi_market_mentions
                        (run_id, source_id, platform, handle, mention_text, product_sku,
                         competitor_product, sentiment, score, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        None,
                        source_id,
                        platform,
                        "",
                        mention_text,
                        "",
                        competitor_csv,
                        "",
                        score,
                        json.dumps(
                            {
                                "review_status": "raw_unreviewed",
                                "provider": it.get("provider"),
                                "published_at": it.get("published_at"),
                                "keyword_hits": hits,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                        now,
                    ),
                )
                new_mentions += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"new_sources": new_sources, "new_mentions": new_mentions, "skipped_existing": skipped}


def refresh_external_signals(ops_dir: str = "runtime/ops", limit_per_source: int = 4) -> dict[str, Any]:
    """抓一次外部信号 → 写 *-market-external-signal-smoke-v0.json(cards 端点 glob 读最新)。"""
    report = external_signal_smoke.build_external_signal_smoke(
        execute_http_fetch=True,
        limit_per_source=max(1, min(int(limit_per_source or 4), 10)),
        timeout_seconds=8,
    )
    if report.get("llm_calls") or report.get("write_db"):
        # 防御:本路径绝不应触发 LLM / 上游 DB 写;若上游变了,拒绝写出并告警。
        logger.error("market_signal_refresh.unexpected_side_effects", extra={"report_keys": list(report.keys())})
        return {"status": "blocked_side_effects"}

    out = Path(ops_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{stamp}-market-external-signal-smoke-v0.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    summary = report.get("summary") or {}
    result = {
        "status": "ok",
        "items_loaded": summary.get("items_loaded"),
        "sources_fetched": summary.get("sources_fetched"),
        "business_signal_items": summary.get("business_signal_items"),
        "artifact": path.name,
    }
    # 闸控入库(默认关):artifact 主路不变,开闸后有信号项直落 raw mentions。
    if str(os.environ.get(AUTOWRITE_ENV) or "").strip().lower() in _TRUTHY:
        try:
            persisted = persist_external_signals(report)
            result.update(persisted)
        except Exception as exc:
            logger.warning("market_signal_refresh.autowrite_failed | error=%s", exc)
            result["autowrite_error"] = str(exc)[:200]
    logger.info("market_signal_refresh.done", extra=result)
    return result
