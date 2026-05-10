"""
backend/app/services/vkpi/weekly_report_templates.py

P1.6: Weekly report template configuration.

6 templates across 3 layers, with prompt builders.
"""

from __future__ import annotations

from typing import Any


PROMPT_VERSION = "v1.0"


# ─── Template Registry ─────────────────────────────────────────

TEMPLATES: dict[str, dict] = {
    
    # ═══ Layer 1: Universal ════════════════════════════════════
    "layer1_universal": {
        "title_pattern": "V-KPI 周报 - {period_start} 至 {period_end}",
        "audience": "all",
        "layer": 1,
        "sections": [
            "weekly_kpi_summary",
            "comment_intelligence_summary",
            "top_kols",
            "top_events",
            "next_week_focus",
        ],
        "max_output_tokens": 1500,
    },
    
    # ═══ Layer 2: Role-specific ════════════════════════════════
    "layer2_leader": {
        "title_pattern": "V-KPI 领导周报 - {period_start} 至 {period_end}",
        "audience": "leader",
        "layer": 2,
        "sections": [
            "weekly_kpi_summary",
            "comment_intelligence_summary",
            "anomaly_detection",
            "competitor_intel",
            "budget_status",
            "team_performance",
            "next_week_priorities",
        ],
        "max_output_tokens": 2500,
    },
    
    "layer2_kol_ops": {
        "title_pattern": "V-KPI KOL 运营周报 - {period_start} 至 {period_end}",
        "audience": "kol_ops",
        "layer": 2,
        "sections": [
            "kol_pool_status",
            "outreach_pipeline",
            "expiring_contracts",
            "anomaly_kols",
        ],
        "max_output_tokens": 2000,
    },
    
    "layer2_marketing_analyst": {
        "title_pattern": "V-KPI 数据分析周报 - {period_start} 至 {period_end}",
        "audience": "marketing_analyst",
        "layer": 2,
        "sections": [
            "kpi_trends",
            "comment_intelligence_summary",
            "sentiment_distribution",
            "pillar_distribution",
            "growth_forecast",
        ],
        "max_output_tokens": 2000,
    },
    
    "layer2_content_reviewer": {
        "title_pattern": "V-KPI 内容审核周报 - {period_start} 至 {period_end}",
        "audience": "content_reviewer",
        "layer": 2,
        "sections": [
            "pillar_distribution",
            "low_quality_content",
            "high_engagement_winners",
            "moderation_queue",
        ],
        "max_output_tokens": 2000,
    },
    
    # ═══ Layer 3: Personal ═════════════════════════════════════
    "layer3_personal": {
        "title_pattern": "你的本周 V-KPI - {period_start} 至 {period_end}",
        "audience": "personal",
        "layer": 3,
        "sections": [
            "your_kols_status",
            "your_kpis_this_week",
            "your_top_events",
            "your_action_items",
        ],
        "max_output_tokens": 1500,
    },
}


# ─── Section → Prompt Description Map ──────────────────────────

SECTION_PROMPTS: dict[str, str] = {
    # Layer 1 sections
    "weekly_kpi_summary": (
        "Weekly KPI summary: total followers across platforms, "
        "follower growth, engagement rate change, total content posts, "
        "comparing to previous week."
    ),
    "comment_intelligence_summary": (
        "Comment intelligence health: pipeline run success rate, comment coverage, "
        "sentiment distribution, brand attitude distribution, and dominant pillars."
    ),
    "top_kols": (
        "Top 5 KOLs by engagement this week (post count × engagement rate)."
    ),
    "top_events": (
        "Top 3 notable events: highest performing post, biggest follower gain, "
        "biggest sentiment shift, brand crisis (hostile spike), or campaign launch."
    ),
    "next_week_focus": (
        "Recommended focus for next week (1-3 priorities) based on data."
    ),
    
    # Layer 2 leader sections
    "anomaly_detection": (
        "Anomalies in last 7 days: KPI drops >20%, sentiment negative spike, "
        "hostile brand_attitude posts (>3 in week), KOL goes silent (no posts 14d)."
    ),
    "competitor_intel": (
        "Competitor activity (if data available from industry monitoring)."
    ),
    "budget_status": (
        "LLM cost month-to-date, projected month-end, remaining budget, top cost drivers."
    ),
    "team_performance": (
        "Team performance: outreach completed, KOLs onboarded, content scored."
    ),
    "next_week_priorities": (
        "Strategic priorities for the team next week."
    ),
    
    # Layer 2 kol_ops sections
    "kol_pool_status": (
        "Current KOL pool: active count, by tier, by platform, by status."
    ),
    "outreach_pipeline": (
        "Outreach pipeline: contacted/replied/negotiating/contracted this week."
    ),
    "expiring_contracts": (
        "Contracts expiring in next 30 days, with KOL status & last activity."
    ),
    "anomaly_kols": (
        "KOLs with anomalies: silent >14d, sentiment hostile, brand_attitude critical."
    ),
    
    # Layer 2 analyst sections
    "kpi_trends": (
        "KPI trends: 7-day, 30-day, by platform, by KOL tier."
    ),
    "sentiment_distribution": (
        "Sentiment distribution this week: positive/neutral/negative %, "
        "brand_attitude breakdown (advocate/supportive/critical/hostile)."
    ),
    "pillar_distribution": (
        "Content pillar distribution: which pillars dominated this week, "
        "comparing to historical average."
    ),
    "growth_forecast": (
        "Growth forecast for next 30 days based on current trends (rough)."
    ),
    
    # Layer 2 reviewer sections
    "low_quality_content": (
        "Posts with low engagement + negative sentiment ratio."
    ),
    "high_engagement_winners": (
        "Top 5 highest engagement posts this week, breakdown by pillar."
    ),
    "moderation_queue": (
        "Posts pending review (sentiment_id IS NULL or pillar_id IS NULL)."
    ),
    
    # Layer 3 personal sections (uses staff_id WHERE clause)
    "your_kols_status": (
        "Your owned KOLs status: their KPIs this week, last activity, "
        "any anomalies needing your attention."
    ),
    "your_kpis_this_week": (
        "KPI summary for your KOLs only (not company-wide)."
    ),
    "your_top_events": (
        "Top events for your KOLs this week."
    ),
    "your_action_items": (
        "Action items: outreach follow-ups, expiring contracts you own, "
        "anomalies to investigate."
    ),
}


# ─── Master Prompt Builder ─────────────────────────────────────

PROMPT_BASE = """You are generating a weekly report for Viltrox V-KPI marketing platform.

Recipient profile:
  Name: {staff_name}
  Role: {staff_role}
  Audience: {audience}

Period: {period_start} to {period_end}

Data context (DO NOT hallucinate, only use the data provided):
{data_context}

Generate a {audience}-focused weekly report covering these sections:
{sections_list}

Format:
- Markdown
- Use headers (##) for each section
- Use bullet points for lists
- Cite specific numbers from the data context
- DO NOT invent statistics or KOL names not in the data context
- If a section has no data, write "(no data this week)"
- Keep concise (max ~{max_tokens} tokens)
- Tone: {tone}

End with a short "Next week" suggestion (1-3 bullet points)."""


def build_prompt(
    template_key: str,
    *,
    staff_name: str,
    staff_role: str,
    period_start: str,
    period_end: str,
    data_context: str,
    tone: str = "professional, data-driven",
) -> str:
    """Build full LLM prompt from template + context."""
    template = TEMPLATES.get(template_key)
    if not template:
        raise ValueError(f"Unknown template: {template_key}")
    
    sections = template["sections"]
    sections_list = "\n".join(
        f"  - {s}: {SECTION_PROMPTS.get(s, s)}" for s in sections
    )
    
    return PROMPT_BASE.format(
        staff_name=staff_name,
        staff_role=staff_role,
        audience=template["audience"],
        period_start=period_start,
        period_end=period_end,
        data_context=data_context[:8000],  # cap context size
        sections_list=sections_list,
        max_tokens=template["max_output_tokens"],
        tone=tone,
    )


def title_for(
    template_key: str,
    period_start: str,
    period_end: str,
) -> str:
    template = TEMPLATES.get(template_key)
    if not template:
        return f"V-KPI Report {period_start} - {period_end}"
    return template["title_pattern"].format(
        period_start=period_start,
        period_end=period_end,
    )


def template_keys_for_role(role: str) -> list[str]:
    """Return template keys this role should receive."""
    role = (role or "employee").lower()
    
    if role == "leader":
        return ["layer1_universal", "layer2_leader"]
    
    role_to_layer2 = {
        "kol_ops": "layer2_kol_ops",
        "marketing_analyst": "layer2_marketing_analyst",
        "content_reviewer": "layer2_content_reviewer",
    }
    
    layer2_key = role_to_layer2.get(role)
    keys = ["layer1_universal"]
    if layer2_key:
        keys.append(layer2_key)
    keys.append("layer3_personal")
    return keys
