"""每日刷新 market 外部信号(竞品新品 + Reddit/Google News 热度)。

合规 / 红线:
- 仅调 `build_external_signal_smoke(execute_http_fetch=True)`:allowlisted 源、有界(limit_per_source)、
  短超时、**零 LLM、零 DB 写**。写一份 artifact 到 runtime/ops 供 Signals & Alerts 直接展示。
- **竞品入库仍走人工审核闸**(本 job 绝不 promote / 不写竞品信号表)——保留全球最严合规设计。
- 不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domains.market import external_signal_smoke

logger = get_logger(__name__)


def refresh_external_signals(ops_dir: str = "runtime/ops", limit_per_source: int = 4) -> dict[str, Any]:
    """抓一次外部信号 → 写 *-market-external-signal-smoke-v0.json(cards 端点 glob 读最新)。"""
    report = external_signal_smoke.build_external_signal_smoke(
        execute_http_fetch=True,
        limit_per_source=max(1, min(int(limit_per_source or 4), 10)),
        timeout_seconds=8,
    )
    if report.get("llm_calls") or report.get("write_db"):
        # 防御:本路径绝不应触发 LLM / DB 写;若上游变了,拒绝写出并告警。
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
    logger.info("market_signal_refresh.done", extra=result)
    return result
