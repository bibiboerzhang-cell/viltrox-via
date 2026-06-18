"""竞品新品雷达 —— 每早用 Gemini + Google 搜索接地,实时查海外竞品(Sony/Sigma/Tamron/DJI…)
新镜头/相机发布 + 对 Viltrox 的机会/威胁,接进 Signals & Alerts。

红线 / 安全:
- 复用 ai_today 的 `_generate`(Gemini 接地优先 + Claude 兜底)+ `_parse_json`。
- 过预算闸 `check_budget("cron:competitor_radar", est)`(硬上限,见 migration 152)。
- 只写本表 `vkpi_competitor_radar`,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
- 海外焦点;一天一次小调用。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.market.ai_today import _generate, _parse_json

logger = get_logger(__name__)

_BUDGET_SCOPE = "cron:competitor_radar"
_EST_COST = 0.05


def _ensure_schema() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_competitor_radar (
            snapshot_date DATE PRIMARY KEY,
            content_json  TEXT NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


def generate_competitor_radar() -> dict[str, Any]:
    """每早一次:预算闸 → Gemini(Google 接地)查竞品新品 → 存库。"""
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("competitor_radar.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {"status": "budget_blocked"}

    today_label = datetime.now(tz=timezone.utc).strftime("%Y年%m月%d日")
    prompt = (
        f"【重要·今天的真实日期是 {today_label}】请严格按此日期判断「近期/最近」,绝不要把往年旧发布当成当下新动态;\n"
        f"无法实时联网就别编造具体的「刚发布」事件(宁可笼统也不错报时间)。\n"
        "你是 Viltrox(唯卓仕,海外镜头/相机配件品牌)的竞品情报分析师。\n"
        "请用 Google 搜索查清【近期(过去 1–2 周到今天)海外·国际相机/镜头行业的真实竞品动态】:\n"
        "Sony / Sigma / Tamron / Nikon / Canon / Fujifilm / DJI / Panasonic 等的新镜头/相机发布、\n"
        "重大产品新闻、价格变动。**只取海外/英文圈真实信息,不要中国大陆平台传闻,务必基于搜索、不要编。**\n"
        "为 Viltrox 输出 JSON(只输出 JSON,不要多余文字):\n"
        '{\n'
        '  "items": [\n'
        '    {"brand": "竞品名", "title": "新品/动态(带时间)", "summary": "一句话说明", '
        '"impact": "对 Viltrox 的机会或威胁(一句)"}\n'
        '  ]\n'
        '}\n'
        "items 取 3–5 条,按重要性排序。"
    )
    raw, model_used = _generate(prompt)
    content = _parse_json(raw)
    items = content.get("items") if isinstance(content, dict) else None
    if not isinstance(items, list) or not items:
        logger.warning("competitor_radar.parse_empty")
        return {"status": "parse_empty"}

    try:
        budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
    except Exception:
        logger.debug("competitor_radar.record_cost_failed", exc_info=True)

    clean = []
    for it in items[:6]:
        d = it if isinstance(it, dict) else {}
        clean.append(
            {
                "brand": str(d.get("brand") or "")[:40],
                "title": str(d.get("title") or "")[:160],
                "summary": str(d.get("summary") or "")[:240],
                "impact": str(d.get("impact") or "")[:240],
            }
        )
    payload = {
        "items": clean,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    _ensure_schema()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_competitor_radar (snapshot_date, content_json, model)
        VALUES (CURRENT_DATE, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE
          SET content_json = excluded.content_json, model = excluded.model, created_at = now()
        """,
        (json.dumps(payload, ensure_ascii=False), str(model_used or "")),
    )
    conn.commit()
    logger.info("competitor_radar.generated", extra={"items": len(clean), "model": model_used})
    return {"status": "ok", "items": len(clean)}


def get_competitor_radar() -> dict[str, Any]:
    """读最新竞品雷达(只读;无则诚实空)。"""
    try:
        _ensure_schema()
        row = get_conn().execute(
            "SELECT content_json, model FROM vkpi_competitor_radar ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"available": False, "reason": "not_generated_yet"}
        d = dict(row)
        return {"available": True, "model": d.get("model"), "content": json.loads(d.get("content_json") or "{}")}
    except Exception:
        logger.debug("competitor_radar.get_failed", exc_info=True)
        return {"available": False, "reason": "read_error"}
