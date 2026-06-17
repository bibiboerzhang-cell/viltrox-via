"""报告深度分析(LLM)—— 把「生成报告」里拼好的全量真实数据喂给 LLM,整理成给管理层看的
经营深度分析(执行摘要 / 亮点 / 风险 / 建议 / 市场·竞品·社区洞察)。按需触发(点按钮),非定时。

红线 / 安全:
- LLM 调用前过预算闸 `check_budget("dashboard:report_analysis", est)`(硬上限,见 migration 153 seed);
  调用后 `record_cost`。超限 → 回退不调用,诚实返回 budget_blocked。
- Claude 优先(报告分析=对所给数据做推理,要稳定、不要接地噪声);Gemini 兜底。两者均自带代理。
- 同一份报告内容(hash 命中且当天)直接复用上次分析,避免重复点按钮重复花预算。
- 只写本域表 `vkpi_report_analysis`,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹 / rule_v0。
- 只读所给的 report_text(前端拼好的真实数据),不自己另查数,杜绝编造。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.market.ai_today import _parse_json  # 复用已加固的 JSON 抽取(去 ```fence / 抽 {..})

logger = get_logger(__name__)

_BUDGET_SCOPE = "dashboard:report_analysis"
_EST_COST = 0.10  # 单次估算(长输入 + ~2000 token 输出)
_MAX_INPUT_CHARS = 14000  # 报告正文上限,超出截断(护成本 + 护 token)


def _ensure_schema() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_report_analysis (
            id            BIGSERIAL PRIMARY KEY,
            content_hash  TEXT        NOT NULL,
            period        TEXT,
            language      TEXT,
            analysis_json TEXT        NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vkpi_report_analysis_hash "
        "ON vkpi_report_analysis (content_hash, created_at DESC)"
    )
    conn.commit()


def _hash(period: str, language: str, report_text: str) -> str:
    h = hashlib.sha256()
    h.update(f"{period}|{language}|{report_text}".encode("utf-8"))
    return h.hexdigest()[:24]


def _generate(prompt: str) -> tuple[str, str]:
    """Claude 优先(报告分析要稳、对所给数据推理);失败回退 Gemini。返回 (文本, 模型标签)。"""
    # 1) Claude —— 纯推理,无接地噪声,给老板看的报告要稳定可复现。
    try:
        from app.services.ai.clients.claude_client import get_claude_client
        from app.services.ai.retry import call_ai_with_retry

        client = get_claude_client()
        resp = call_ai_with_retry(
            "dashboard.report_analysis",
            lambda: client.messages.create(
                model=CLAUDE_MODEL, max_tokens=2500, messages=[{"role": "user", "content": prompt}]
            ),
        )
        text = (resp.content[0].text or "").strip() if resp and getattr(resp, "content", None) else ""
        if text:
            return text, f"claude:{CLAUDE_MODEL}"
    except Exception:
        logger.warning("report_analysis.claude_failed_fallback_gemini", exc_info=True)
    # 2) Gemini 兜底(无 google_search,纯推理)。
    try:
        import app.core.config  # noqa: F401  触发 .env 加载
        import app.services.ai.clients.gemini_client as gc

        from app.core.config import GEMINI_MODEL

        client = getattr(gc, "gemini_client", None)
        if client is not None:
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                return text, f"gemini:{GEMINI_MODEL}"
    except Exception:
        logger.warning("report_analysis.gemini_failed", exc_info=True)
    return "", ""


def _build_prompt(report_text: str, period: str, language: str) -> str:
    lang_line = "用中文输出。" if language != "en" else "Output in English."
    period_label = ("月报" if period == "monthly" else "周报") if language != "en" else (
        "monthly report" if period == "monthly" else "weekly report"
    )
    return (
        f"你是 Viltrox(唯卓仕)的营销分析负责人。下面是这份{period_label}的全部真实数据,请整理成给\n"
        "管理层看的【经营深度分析】。要求:\n"
        "- 只基于下面提供的数据做分析,绝不编造数字;数据中标注「待接入/pending/—」的缺口不要硬说有结论,\n"
        "  而是点明它是待补的缺口。\n"
        "- 结论要有据:尽量引用数据里的具体数字、平台、KOL/项目名、活动名。\n"
        "- 如数据里含市场信号 / 竞品动态 / Reddit/社区讨论,请在「市场洞察」里结合分析它对 Viltrox 的影响\n"
        "  与可借势点(产品关联度优先:大光圈定焦、电影镜、轻量广角等)。\n"
        f"- {lang_line}\n\n"
        "报告数据:\n"
        '"""\n'
        f"{report_text[:_MAX_INPUT_CHARS]}\n"
        '"""\n\n'
        "严格只输出 JSON(不要任何多余文字 / 不要 markdown 代码块):\n"
        "{\n"
        '  "executive_summary": "3-4 句执行摘要,点出本期整体表现与最重要的一两件事",\n'
        '  "highlights": ["关键亮点(引用具体数字)", "...3-5 条"],\n'
        '  "risks": ["风险 / 问题 / 待补缺口", "...3-5 条"],\n'
        '  "recommendations": ["可执行的行动建议(谁/做什么)", "...3-5 条"],\n'
        '  "market_insights": ["市场/竞品/社区(含 Reddit)洞察 → 对 Viltrox 的影响与借势", "...2-4 条"]\n'
        "}\n"
    )


def _normalize(content: dict[str, Any]) -> dict[str, Any]:
    def _strs(key: str, cap: int) -> list[str]:
        v = content.get(key)
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()][:cap]

    return {
        "executive_summary": str(content.get("executive_summary") or "").strip(),
        "highlights": _strs("highlights", 6),
        "risks": _strs("risks", 6),
        "recommendations": _strs("recommendations", 6),
        "market_insights": _strs("market_insights", 5),
        "generated_at": datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


def analyze(report_text: str, period: str = "monthly", language: str = "zh") -> dict[str, Any]:
    """按需:命中缓存(当天同内容)直接复用;否则预算闸 → LLM 分析 → 存库。"""
    report_text = str(report_text or "").strip()
    if len(report_text) < 40:
        return {"available": False, "reason": "report_too_short"}

    _ensure_schema()
    chash = _hash(period, language, report_text)

    # 1) 当天同内容缓存命中 → 直接返回(省预算 + 秒回)。
    try:
        row = get_conn().execute(
            "SELECT analysis_json, model FROM vkpi_report_analysis "
            "WHERE content_hash = ? AND created_at > now() - INTERVAL '1 day' "
            "ORDER BY created_at DESC LIMIT 1",
            (chash,),
        ).fetchone()
        if row:
            d = dict(row)
            return {
                "available": True,
                "cached": True,
                "model": d.get("model"),
                "analysis": json.loads(d.get("analysis_json") or "{}"),
            }
    except Exception:
        logger.debug("report_analysis.cache_read_failed", exc_info=True)

    # 2) 预算闸(硬上限)。
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("report_analysis.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {"available": False, "reason": "budget_blocked"}

    # 3) LLM 分析。
    raw, model_used = _generate(_build_prompt(report_text, period, language))
    content = _parse_json(raw)
    analysis = _normalize(content)
    if not analysis["executive_summary"] and not analysis["highlights"]:
        logger.warning("report_analysis.parse_empty")
        return {"available": False, "reason": "analysis_unavailable"}

    try:
        budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
    except Exception:
        logger.debug("report_analysis.record_cost_failed", exc_info=True)

    # 4) 存库(缓存 + 留痕)。
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO vkpi_report_analysis (content_hash, period, language, analysis_json, model) "
            "VALUES (?, ?, ?, ?, ?)",
            (chash, period, language, json.dumps(analysis, ensure_ascii=False), str(model_used or "")),
        )
        conn.commit()
    except Exception:
        logger.debug("report_analysis.store_failed", exc_info=True)

    logger.info(
        "report_analysis.generated",
        extra={"highlights": len(analysis["highlights"]), "model": model_used},
    )
    return {"available": True, "cached": False, "model": model_used, "analysis": analysis}
