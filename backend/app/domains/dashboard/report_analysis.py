"""报告深度分析(LLM)—— 把「生成报告」里拼好的全量真实数据喂给 LLM,整理成给管理层看的
经营深度分析(执行摘要 / 亮点 / 风险 / 建议 / 市场·竞品·社区洞察)。按需触发(点按钮),非定时。

红线 / 安全:
- LLM 调用前过本域预算闸;每个精确模型调用再由 llm_production 原子预留/结算。
  超限或未有运行证据 → 回退不调用,诚实返回 budget_blocked/analysis_unavailable。
- Claude 优先、Gemini 仅由本调用方显式兜底;单次调用禁止静默跨模型 fallback。
- 同一份报告内容(hash 命中且当天)直接复用上次分析,避免重复点按钮重复花预算。
- 只写本域表 `vkpi_report_analysis`,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹 / rule_v0。
- 只读所给的 report_text(前端拼好的真实数据),不自己另查数,杜绝编造。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import CLAUDE_MODEL, GEMINI_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.market.ai_today import _parse_json  # 复用已加固的 JSON 抽取(去 ```fence / 抽 {..})
from app.platform import llm_production

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
    """Run two explicit exact-model attempts through the production boundary."""

    def usable_payload(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        normalized = _normalize(value)
        return bool(normalized["executive_summary"] or normalized["highlights"])

    candidates = (
        ("anthropic", CLAUDE_MODEL, "claude", "primary"),
        ("google", GEMINI_MODEL, "gemini", "explicit_fallback"),
    )
    for provider, model, label, stage in candidates:
        try:
            result = llm_production.generate_json(
                prompt,
                provider=provider,
                model=model,
                purpose="dashboard.report_analysis",
                max_output_tokens=2500,
                cost_tag=_BUDGET_SCOPE,
                validator=usable_payload,
                deadline_seconds=75.0,
                metadata={
                    "surface": "dashboard_report_analysis",
                    "model_stage": stage,
                    "explicit_cross_model_fallback": stage == "explicit_fallback",
                },
            )
        except Exception:
            logger.warning(
                "report_analysis.strict_model_failed",
                extra={"provider": provider, "model": model, "stage": stage},
                exc_info=True,
            )
            continue
        payload = result.get("json") if isinstance(result, dict) else None
        if (
            str(result.get("status") or "") == "success"
            and str(result.get("provider") or "").strip().lower() == provider
            and usable_payload(payload)
        ):
            actual_model = str(result.get("model") or model).strip() or model
            return json.dumps(payload, ensure_ascii=False), f"{label}:{actual_model}"
        logger.info(
            "report_analysis.strict_model_unavailable",
            extra={
                "provider": provider,
                "model": model,
                "stage": stage,
                "status": (
                    str(result.get("status") or "failed")
                    if isinstance(result, dict)
                    else "invalid_result"
                ),
            },
        )
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


def _cached_row(chash: str) -> dict[str, Any] | None:
    """当天同内容的上一次分析。纯 SELECT;读不出来就当没有(失败方向 = 不谎报缓存)。"""

    try:
        row = get_conn().execute(
            "SELECT analysis_json, model FROM vkpi_report_analysis "
            "WHERE content_hash = ? AND created_at > now() - INTERVAL '1 day' "
            "ORDER BY created_at DESC LIMIT 1",
            (chash,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        logger.debug("report_analysis.cache_read_failed", exc_info=True)
        return None


def quote(report_text: str, period: str = "monthly", language: str = "zh") -> dict[str, Any]:
    """点之前先报价。**零成本**:只有缓存探测与预算读两条 SELECT,不调用任何模型。

    红线要求「花钱的动作不许自动武装」,所以入口分两步:先报价让人看见这一次要不要
    花钱、花多少,确认之后才真跑。三种结果,措辞都必须诚实:

    * ``cached=True``    当天同一份报告已经分析过 -> 直接复用,这一次 **0 成本**。
    * ``budget_blocked`` 当天额度已用尽 -> 点了也不会花钱,但也出不来结果。
    * 其余                会真花钱,``estimated_cost_usd`` 就是预估值。

    ``estimated_cost_usd`` 与送进预算闸的是同一个常量 ``_EST_COST``,刻意不在前端另写
    一份 —— 两处各写一遍就一定会漂,而漂掉的那一方正是给用户看的那个数。
    """

    report_text = str(report_text or "").strip()
    if len(report_text) < 40:
        return {"available": False, "dry_run": True, "reason": "report_too_short"}

    _ensure_schema()
    if _cached_row(_hash(period, language, report_text)) is not None:
        return {
            "available": True, "dry_run": True, "cached": True,
            "will_spend": False, "estimated_cost_usd": 0.0,
        }
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        return {
            "available": False, "dry_run": True, "cached": False,
            "will_spend": False, "estimated_cost_usd": 0.0, "reason": "budget_blocked",
        }
    return {
        "available": True, "dry_run": True, "cached": False,
        "will_spend": True, "estimated_cost_usd": _EST_COST,
    }


def analyze(
    report_text: str,
    period: str = "monthly",
    language: str = "zh",
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按需:命中缓存(当天同内容)直接复用;否则预算闸 → LLM 分析 → 存库。

    ``dry_run=True`` 只报价、绝不花钱(转交 ``quote``)。
    """
    if dry_run:
        return quote(report_text, period=period, language=language)

    report_text = str(report_text or "").strip()
    if len(report_text) < 40:
        return {"available": False, "reason": "report_too_short"}

    _ensure_schema()
    chash = _hash(period, language, report_text)

    # 1) 当天同内容缓存命中 → 直接返回(省预算 + 秒回)。
    cached = _cached_row(chash)
    if cached:
        return {
            "available": True,
            "cached": True,
            "model": cached.get("model"),
            "analysis": json.loads(cached.get("analysis_json") or "{}"),
        }

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

    # Cost is settled exactly once by llm_production's atomic reservation.

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
