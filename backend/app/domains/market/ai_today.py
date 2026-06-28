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
        if isinstance(obj, dict):
            return obj
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    # 兜底:Gemini 接地输出常带引用/前后说明文字 → 抽取第一个 { 到最后一个 } 再解析。
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
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


def _generate(prompt: str) -> tuple[str, str]:
    """优先 Gemini + Google 搜索接地(拉真·当下热点/赛事);失败回退 Claude。返回 (文本, 模型标签)。"""
    # 1) Gemini + Google 搜索接地 —— 真·当下热点的关键。Gemini 能联网,首选它拿真数据。
    #    对 503/429 等"临时高负载"退避重试;浮动 gemini-flash-latest 持续过载时退到钉死的稳定版
    #    (避开浮动别名撞上的忙端点),尽量拿真·联网搜索结果,少落 Claude 凭记忆猜。
    try:
        import time

        import app.core.config  # noqa: F401  触发 .env 加载(GOOGLE/GEMINI key)
        import app.services.ai.clients.gemini_client as gc
        from google.genai import types

        from app.core.config import GEMINI_MODEL

        client = getattr(gc, "gemini_client", None)
        if client is not None:
            cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
            candidates: list[str] = []
            for m in (GEMINI_MODEL, "gemini-2.5-flash"):  # 首选配置(可能 latest)→ 钉死稳定版
                if m and m not in candidates:
                    candidates.append(m)
            for model_name in candidates:
                for attempt in range(3):  # 每模型最多 3 次(0/1/2)
                    try:
                        resp = client.models.generate_content(model=model_name, contents=prompt, config=cfg)
                        text = (getattr(resp, "text", "") or "").strip()
                        if text and _parse_json(text):
                            return text, f"gemini:{model_name}+google_search"
                        break  # 有响应但不可解析 → 重试/换模型也救不了,跳出落 Claude
                    except Exception as exc:
                        msg = str(exc).lower()
                        transient = any(
                            k in msg
                            for k in ("503", "unavailable", "429", "resource_exhausted", "overload", "high demand", "timeout", "deadline")
                        )
                        if attempt < 2 and transient:
                            time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s 退避,给 Google 缓过来
                            continue
                        if transient:
                            break  # 该模型重试用尽 → 换下一个候选模型
                        raise  # 非临时错(key 无效等)→ 直接落 Claude
            logger.warning("ai_today.gemini_unavailable_after_retries_fallback_claude")
    except Exception:
        logger.warning("ai_today.gemini_failed_fallback_claude", exc_info=True)
    # 2) Claude 兜底(无接地,凭模型知识)。
    try:
        from app.services.ai.clients.claude_client import get_claude_client
        from app.services.ai.retry import call_ai_with_retry

        client = get_claude_client()
        resp = call_ai_with_retry(
            "ai_today.hot",
            lambda: client.messages.create(
                # 900 太小:中文 JSON(headline+3方案+3热点)易超 → 截断 → 解析空 → 卡片不刷。抬到 2048。
                model=CLAUDE_MODEL, max_tokens=2048, messages=[{"role": "user", "content": prompt}]
            ),
        )
        text = (resp.content[0].text or "").strip() if resp and getattr(resp, "content", None) else ""
        return text, f"claude:{CLAUDE_MODEL}"
    except Exception:
        logger.warning("ai_today.claude_failed", exc_info=True)
        return "", ""


def generate_ai_today_hot() -> dict[str, Any]:
    """每早一次:预算闸 → Gemini(Google 接地)生成 → 存库。返回状态。"""
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("ai_today.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {"status": "budget_blocked"}
    hot = _read_hot_brands()
    hot_line = ("当前竞品/行业热点信号:" + "、".join(hot)) if hot else f"行业主要竞品:{_COMPETITORS_FALLBACK}"
    today_label = datetime.now(tz=timezone.utc).strftime("%Y年%m月%d日")
    prompt = (
        f"【重要·今天的真实日期是 {today_label}】请**严格按此日期**判断「当下/最近」热点;绝不要把往年(如 2025 年)\n"
        f"的赛事/发布当成正在进行的当下事件。若你无法实时联网搜索,就基于这个真实日期给出贴合当前季节的通用拍摄\n"
        f"方向,**不要编造你无法确认的、具体「正在进行」的赛事或新品发布**(宁可笼统也不要错报时间)。\n"
        f"你是 Viltrox(唯卓仕)面向【海外/国际市场】的内容策划。Viltrox 主销欧美/全球,目标受众是\n"
        f"海外摄影/视频创作者。{hot_line}。\n"
        "请先用 Google 搜索查清【当下海外·国际(非中国大陆)摄影/影像圈正在火的真实热点】:国际摄影/\n"
        "影视赛事(如 LensCulture、Sony World Photography Awards、IPA 等)、Instagram/YouTube/Reddit/TikTok\n"
        "上正流行的拍摄玩法/风格、海外创作者热议的话题。**务必只取海外/英文圈内容,绝不要小红书/抖音/微博\n"
        "等中国大陆平台的热点。** 基于搜索到的真实近况,不要编。\n"
        "**关键:热点不是越火越好,要按【与 Viltrox 产品的关联度】筛选+排序** —— Viltrox 主打大光圈定焦\n"
        "(如 AF 27/35/56/85mm F1.x、135mm F1.8 LAB 旗舰)、变形宽荧幕电影镜、轻量广角等。优先选能直接\n"
        "借势到这些镜头/拍法的热点(如弱光人像、电影感Vlog、复古街拍);每条 hot_topic 都要能落到一类\n"
        "我们能借势的镜头或拍法,纯无关的热点(如纯无人机竞速)不要。\n"
        "再据此生成今天的内容建议,具体可执行、贴合海外创作者口味、紧扣真实当下热点。\n"
        "严格只输出 JSON(不要多余文字):\n"
        '{\n'
        '  "headline": "一句今日重点决策(中文,<=28字)",\n'
        '  "shooting_plans": ["拍摄方案1:场景+用哪类镜头+卖点(面向海外创作者)", "方案2", "方案3"],\n'
        '  "hot_topics": ["真实海外当下热点/国际赛事/流行玩法1(带时间或来源)", "热点2", "热点3"]\n'
        '}\n'
    )
    raw, model_used = _generate(prompt)
    content = _parse_json(raw)
    if not content.get("headline") and not content.get("shooting_plans"):
        logger.warning("ai_today.parse_empty")
        return {"status": "parse_empty"}

    try:
        budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
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
        (json.dumps(payload, ensure_ascii=False), str(model_used or "")),
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
