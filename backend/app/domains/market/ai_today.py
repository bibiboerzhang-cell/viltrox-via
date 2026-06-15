"""AI Today 今日热点 —— 每早 8点(中国时区)用 LLM 据真实行业热点生成「拍摄方案 + 话题 + 重点决策」。

红线 / 安全:
- LLM 调用前过预算闸 `check_budget("cron:ai_today_hot", est)`(硬上限,见 migration 150 seed)。
- claude client 自带代理(本网络直连被墙,走 HTTPS_PROXY);一天一次小调用。
- 只写本域表 `vkpi_ai_today_hot`,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
- 读不到 live 热点时回退竞品上下文,LLM 照样产出可用内容(诚实,不空着)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard

logger = get_logger(__name__)

_BUDGET_SCOPE = "cron:ai_today_hot"
_EST_COST = 0.05  # 单次估算成本(short prompt + ~900 token out)
_COMPETITORS_FALLBACK = "Sony、Sigma、Tamron、DJI、INSTA360、PROFOTO、Godox、尼康、佳能"


def _read_hot_brands(ops_dir: str = "runtime/ops", limit: int = 6) -> list[str]:
    """best-effort 读最新 market 信号里的热点品牌喂 LLM;任何异常回退 [],绝不抛。"""
    try:
        root = Path(ops_dir)
        files = sorted(
            [p for p in root.glob("*market*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for f in files[:3]:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            hot = summary.get("hot_brands") or data.get("hot_brands") or []
            out: list[str] = []
            for h in hot[:limit]:
                name = h.get("brand") if isinstance(h, dict) else str(h)
                if name:
                    out.append(str(name))
            if out:
                return out
        return []
    except Exception:
        logger.debug("ai_today.read_hot_brands_failed", exc_info=True)
        return []


def _parse_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        obj = json.loads(text.strip())
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _ensure_schema() -> None:
    get_conn().execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_ai_today_hot (
            snapshot_date DATE PRIMARY KEY,
            content_json  TEXT NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    get_conn().commit()


def generate_ai_today_hot() -> dict[str, Any]:
    """每早一次:预算闸 → LLM 生成 → 存库。返回状态。"""
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("ai_today.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {"status": "budget_blocked"}
    try:
        from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE, get_claude_client
        from app.services.ai.retry import call_ai_with_retry
    except Exception:
        return {"status": "llm_unavailable"}
    if not ANTHROPIC_AVAILABLE:
        return {"status": "llm_unavailable"}

    hot = _read_hot_brands()
    hot_line = ("当前竞品/行业热点信号:" + "、".join(hot)) if hot else f"行业主要竞品:{_COMPETITORS_FALLBACK}"
    prompt = (
        f"你是 Viltrox(唯卓仕,影像镜头/配件品牌)的内容策划。{hot_line}。\n"
        "请生成今天的内容建议,务必具体、可直接执行、贴合摄影/视频创作者口味。\n"
        "严格只输出 JSON(不要多余文字):\n"
        '{\n'
        '  "headline": "一句今日重点决策(中文,<=28字)",\n'
        '  "shooting_plans": ["拍摄方案1:场景+用哪类镜头+卖点", "方案2", "方案3"],\n'
        '  "hot_topics": ["创作者最近喜欢的话题1", "话题2", "话题3"]\n'
        '}\n'
    )
    client = get_claude_client()
    resp = call_ai_with_retry(
        "ai_today.hot",
        lambda: client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        ),
    )
    raw = resp.content[0].text.strip() if resp and getattr(resp, "content", None) else ""
    content = _parse_json(raw)
    if not content.get("headline") and not content.get("shooting_plans"):
        logger.warning("ai_today.parse_empty")
        return {"status": "parse_empty"}

    try:
        budget_guard.record_cost(_BUDGET_SCOPE, _EST_COST)
    except Exception:
        logger.debug("ai_today.record_cost_failed", exc_info=True)

    _ensure_schema()
    payload = {
        "headline": str(content.get("headline") or ""),
        "shooting_plans": [str(x) for x in (content.get("shooting_plans") or [])][:5],
        "hot_topics": [str(x) for x in (content.get("hot_topics") or [])][:5],
        "hot_brands": hot,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_ai_today_hot (snapshot_date, content_json, model)
        VALUES (CURRENT_DATE, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE
          SET content_json = excluded.content_json, model = excluded.model, created_at = now()
        """,
        (json.dumps(payload, ensure_ascii=False), str(CLAUDE_MODEL)),
    )
    conn.commit()
    logger.info("ai_today.generated", extra={"plans": len(payload["shooting_plans"]), "brands": len(hot)})
    return {"status": "ok", "shooting_plans": len(payload["shooting_plans"])}


def get_ai_today_hot() -> dict[str, Any]:
    """读最新一天的 AI Today 热点内容(只读;无则诚实空)。"""
    try:
        _ensure_schema()
        row = get_conn().execute(
            "SELECT content_json, model, created_at FROM vkpi_ai_today_hot "
            "ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"available": False, "reason": "not_generated_yet"}
        d = dict(row)
        content = json.loads(d.get("content_json") or "{}")
        return {"available": True, "model": d.get("model"), "content": content}
    except Exception:
        logger.debug("ai_today.get_failed", exc_info=True)
        return {"available": False, "reason": "read_error"}
