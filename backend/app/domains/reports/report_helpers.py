"""Shared report formatting and summary helpers."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.core.logging import get_logger
from app.domains.reports.model_policy import (
    ReportModelDecision,
    ReportSourceSample,
    evaluate_report_model_policy,
)
from app.platform import llm_gateway
from app.platform.models.runtime import split_binding


logger = get_logger("app.domains.reports.reports")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _money_cents(value: Any, *, language: str = "zh") -> str:
    if value in (None, ""):
        return _localized(language, "未知", "Unknown")
    try:
        cents = int(value)
    except (TypeError, ValueError):
        return _localized(language, "未知", "Unknown")
    return f"${cents / 100:,.0f}"


def _uid(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def _staff_name(staff: dict[str, Any] | None) -> str:
    if not staff:
        return "system"
    return str(staff.get("name") or staff.get("id") or staff.get("staff_id") or "staff")


def _localized(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(mapping: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in mapping:
            value = _int_or_none(mapping.get(key))
            if value is not None:
                return value
    return None


def _load_json(value: Any) -> Any:
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception as exc:
        logger.warning("vkpi report json parse failed: %s", exc)
        return {}


def _metric_label(metric_key: str) -> str:
    labels = {
        "views": "播放量",
        "gmv": "本周销售额",
        "cost": "成本",
        "net_contribution": "销售额减成本",
        "roi": "ROI",
        "new_kol": "新增 KOL",
        "published_content": "已发布内容",
        "valid_clicks": "有效点击",
        "active_projects": "进行中项目",
        "staff_kpi": "员工 KPI",
        "product_roi": "产品表现",
        "alerts": "提醒",
    }
    return labels.get(metric_key, metric_key)


def _format_metric_value(metric_key: str, value: Any, unit: str = "", currency: str = "") -> str:
    if value in (None, ""):
        return "未知"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "未知"
    if currency == "USD" or metric_key in {"gmv", "cost", "net_contribution"}:
        return _money_cents(numeric)
    if metric_key == "roi":
        return f"{numeric:.2f}x"
    if unit == "count" or metric_key in {"views", "new_kol", "published_content", "valid_clicks", "active_projects"}:
        return f"{int(numeric):,}"
    return f"{numeric:,.2f}".rstrip("0").rstrip(".")


def _format_kpis_for_prompt(kpis: list[dict[str, Any]]) -> str:
    if not kpis:
        return "- 暂无 KPI 数据"
    return "\n".join(
        f"- {str(item.get('label') or '-')}: {str(item.get('value') or '-')} "
        f"[data_status={str(item.get('data_status') or 'unknown')}] "
        f"({str(item.get('note') or '无备注')})"
        for item in kpis[:12]
    )


def _format_funnel_for_prompt(funnel: list[dict[str, Any]]) -> str:
    if not funnel:
        return "- 暂无漏斗数据"
    return "\n".join(
        f"- {str(item.get('stage') or '-')}: {int(item.get('count') or 0)}"
        for item in funnel[:16]
    )


def _format_staff_for_prompt(staff_rows: list[dict[str, Any]]) -> str:
    if not staff_rows:
        return "- 暂无员工表现数据"
    lines: list[str] = []
    for item in staff_rows[:5]:
        lines.append(
            "- "
            f"{str(item.get('name') or '员工')}: "
            f"销售 {str(item.get('sales') or '$0')}, "
            f"成本 {str(item.get('cost') or '$0')}, "
            f"新增 KOL {int(item.get('kol_claims') or 0)}, "
            f"发布 {int(item.get('published') or 0)}, "
            f"项目 {int(item.get('projects') or 0)}"
        )
    return "\n".join(lines)


def _format_alerts_for_prompt(alert_rows: list[dict[str, Any]]) -> str:
    if not alert_rows:
        return "- 当前无未处理提醒"
    return "\n".join(
        f"- {str(item.get('title') or '提醒')}: {str(item.get('description') or '')[:140]}"
        for item in alert_rows[:6]
    )


def _build_weekly_prompt(context: dict[str, Any]) -> str:
    totals = context.get("totals") if isinstance(context.get("totals"), dict) else {}
    if context.get("language") == "en":
        return f"""You are Viltrox's internal marketing analyst. Write a concise report summary using only the verified system data below.

Period: {context.get('period_label') or ''}

KPIs:
{_format_kpis_for_prompt(context.get('kpis') or [])}

Project funnel:
{_format_funnel_for_prompt(context.get('funnel') or [])}

Staff contribution (top 5):
{_format_staff_for_prompt(context.get('staff_rows') or [])}

Open risks:
{_format_alerts_for_prompt(context.get('alerts') or [])}

Totals:
- Sales cents: {totals.get('sales_cents') if totals.get('sales_cents') is not None else 'unknown'}
- Cost cents: {totals.get('cost_cents') if totals.get('cost_cents') is not None else 'unknown'}
- Views: {totals.get('views') if totals.get('views') is not None else 'unknown'}
- New KOLs: {totals.get('new_kol') if totals.get('new_kol') is not None else 'unknown'}
- Published content: {totals.get('published') if totals.get('published') is not None else 'unknown'}
- Active projects: {totals.get('active_projects') if totals.get('active_projects') is not None else 'unknown'}

Requirements:
- 160 words or fewer
- Never invent or replace unknown values with zero
- Lead with the conclusion, then state the main risk or next action
- Write in professional, direct English
"""
    return f"""你是 Viltrox 内部营销分析师。请基于真实系统数据写一段中文报告总结。

时间范围: {context.get('period_label') or ''}

KPI 概览:
{_format_kpis_for_prompt(context.get('kpis') or [])}

漏斗:
{_format_funnel_for_prompt(context.get('funnel') or [])}

员工表现 Top 5:
{_format_staff_for_prompt(context.get('staff_rows') or [])}

未处理提醒:
{_format_alerts_for_prompt(context.get('alerts') or [])}

汇总口径:
- 销售额 cents: {totals.get('sales_cents') if totals.get('sales_cents') is not None else 'unknown'}
- 成本 cents: {totals.get('cost_cents') if totals.get('cost_cents') is not None else 'unknown'}
- 播放量: {totals.get('views') if totals.get('views') is not None else 'unknown'}
- 新增 KOL: {totals.get('new_kol') if totals.get('new_kol') is not None else 'unknown'}
- 已发布内容: {totals.get('published') if totals.get('published') is not None else 'unknown'}
- 进行中项目: {totals.get('active_projects') if totals.get('active_projects') is not None else 'unknown'}

要求:
- 200 字以内
- 只基于上面的真实数据,不要编造数据
- 先讲当前周期结论,再指出风险或下一步动作
- 中文,专业、直接,不要写成广告文案
"""


def _structured_report_model_policy(context: Mapping[str, Any]) -> ReportModelDecision:
    """Evaluate the exact-model policy from structured report KPI evidence.

    The report context is generated inside the reports domain.  Even so, this
    adapter treats missing/partial/seeded metrics and zero source counts as
    blockers.  Runtime evidence is deliberately left to the shared resolver's
    operator-maintained verified-model evidence; registration alone cannot
    enable provider calls.
    """
    raw_kpis = context.get("kpis")
    kpis = raw_kpis if isinstance(raw_kpis, list) else []
    sources: list[ReportSourceSample] = []
    readiness_blockers: list[str] = []
    for index, raw_item in enumerate(kpis):
        if not isinstance(raw_item, Mapping):
            readiness_blockers.append(f"kpi_{index}:invalid")
            continue
        key = str(raw_item.get("key") or f"kpi_{index}").strip()
        source_count = _int_or_none(raw_item.get("source_count"))
        observed = max(0, int(source_count or 0))
        data_status = str(raw_item.get("data_status") or "").strip().lower()
        sources.append(
            ReportSourceSample(
                key=key,
                observed=observed,
                minimum=1,
                source_count=observed,
                data_status=data_status,
                label=str(raw_item.get("label") or key),
            )
        )
        if data_status != "real":
            readiness_blockers.append(f"{key}:status_{data_status or 'missing'}")
        if observed < 1:
            readiness_blockers.append(f"{key}:source_count<1")

    if not sources:
        readiness_blockers.append("report_kpis:missing")
    readiness_ready = bool(sources) and not readiness_blockers
    readiness = {
        "status": "ready" if readiness_ready else "insufficient",
        "ready": readiness_ready,
        "claimable": readiness_ready,
        "claim_level": "validated" if readiness_ready else "descriptive_only",
        "blockers": readiness_blockers,
    }
    return evaluate_report_model_policy(readiness, sources)


def _explicit_report_model_policy(
    policy_input: Mapping[str, Any] | None,
) -> ReportModelDecision:
    """Evaluate caller-supplied *data* evidence while keeping runtime fail-closed.

    This is used by the legacy scheduled weekly generator, whose text snippets
    do not carry structured provenance.  Callers may supply readiness and
    source evidence, but runtime availability is never accepted from this
    payload; it still comes only from the shared runtime-verification contract.
    """
    safe_input = dict(policy_input or {})
    readiness = safe_input.get("data_readiness")
    if not isinstance(readiness, Mapping):
        readiness = {
            "status": "insufficient",
            "ready": False,
            "claimable": False,
            "claim_level": "descriptive_only",
            "blockers": ["structured_report_evidence:missing"],
        }
    raw_sources = safe_input.get("sources")
    sources: Iterable[ReportSourceSample | Mapping[str, Any]]
    if isinstance(raw_sources, (list, tuple)):
        sources = raw_sources
    else:
        sources = ()
    return evaluate_report_model_policy(readiness, sources)


def _invoke_exact_report_model(
    prompt: str,
    *,
    decision: ReportModelDecision,
    purpose: str,
    max_output_tokens: int,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Invoke only the exact primary/challenger chain authorized by policy."""
    if not decision.provider_calls_allowed:
        return None
    primary_provider, primary_model = split_binding(decision.primary_model or "")
    fallback_items: list[tuple[str, str]] = []
    for binding in decision.selected_models[1:]:
        provider, model = split_binding(binding)
        if provider and model:
            fallback_items.append((provider, model))
    fallback_chain = tuple(fallback_items)
    if not primary_provider or not primary_model or not fallback_chain:
        logger.warning("vkpi report model policy produced an incomplete exact chain")
        return None
    policy_payload = decision.to_dict()
    return llm_gateway.invoke(
        prompt,
        purpose=purpose,
        max_output_tokens=max_output_tokens,
        preferred_provider=primary_provider,
        model_override=primary_model,
        model_fallbacks=fallback_chain,
        require_runtime_verified=True,
        metadata={**(metadata or {}), "report_model_policy": policy_payload},
        staff=staff,
    )


def _generate_ai_summary(context: dict[str, Any], *, staff: dict[str, Any] | None = None) -> str:
    prompt = _build_weekly_prompt(context)
    decision = _structured_report_model_policy(context)
    context["model_policy"] = decision.to_dict()
    if str(os.getenv("VKPI_WEEKLY_SUMMARY_AI_DISABLED") or "").strip().lower() in {"1", "true", "yes", "on"}:
        llm_gateway.record_call(
            provider="rule_v0",
            model="rule_v0",
            purpose="vkpi_weekly_summary",
            prompt=prompt,
            status="disabled",
            fallback_used=True,
            metadata={"reason": "VKPI_WEEKLY_SUMMARY_AI_DISABLED", "period_label": context.get("period_label", "")},
            staff=staff,
        )
        return ""

    result = _invoke_exact_report_model(
        prompt,
        decision=decision,
        purpose="vkpi_weekly_summary",
        max_output_tokens=1024,
        metadata={"period_label": context.get("period_label", "")},
        staff=staff,
    )
    if result is None:
        return ""
    if str(result.get("status") or "") == "success":
        text = str(result.get("text") or "").strip()
        if not text:
            return ""
        return text[:1200].strip()
    return ""


__all__ = [
    "_build_weekly_prompt",
    "_explicit_report_model_policy",
    "_first_int",
    "_format_alerts_for_prompt",
    "_format_funnel_for_prompt",
    "_format_kpis_for_prompt",
    "_format_metric_value",
    "_format_staff_for_prompt",
    "_generate_ai_summary",
    "_int_or_none",
    "_json",
    "_load_json",
    "_localized",
    "_metric_label",
    "_money_cents",
    "_invoke_exact_report_model",
    "_structured_report_model_policy",
    "_staff_name",
    "_uid",
    "_utcnow",
]
