"""
backend/app/services/vkpi/weekly_report_generator.py

P1.6: Weekly report generator orchestrator.

Generates 23 reports/week (1 leader×2 + 7 employees×3) by:
  1. Querying section data for each template
  2. Building grounded prompt
  3. Calling llm_gateway.invoke
  4. Persisting to vkpi_weekly_reports
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from app.core.release_validation import release_validation_active
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.access import scope
from app.domains.reports.report_helpers import (
    _explicit_report_model_policy,
    _invoke_exact_report_model,
)
from app.domains.reports.weekly_templates import (
    PROMPT_VERSION,
    TEMPLATES,
    build_prompt,
    template_keys_for_role,
    title_for,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def ensure_vkpi_weekly_reports_schema() -> None:
    """Create P1.6 weekly report table when migrations are absent."""
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_weekly_reports (
          id BIGSERIAL PRIMARY KEY,
          staff_id INT,
          layer SMALLINT NOT NULL,
          template_key VARCHAR(50) NOT NULL,
          period_start DATE NOT NULL,
          period_end DATE NOT NULL,
          title VARCHAR(200),
          body_md TEXT,
          llm_provider VARCHAR(50),
          llm_model VARCHAR(100),
          prompt_version VARCHAR(20),
          input_tokens INT DEFAULT 0,
          output_tokens INT DEFAULT 0,
          cost_cents INT DEFAULT 0,
          status VARCHAR(20) DEFAULT 'draft',
          sent_at TIMESTAMPTZ,
          delivery_channels TEXT,
          truth_invalidated_at TIMESTAMPTZ,
          truth_invalidation_reason TEXT NOT NULL DEFAULT '',
          truth_invalidation_migration INT,
          truth_restorable BOOLEAN NOT NULL DEFAULT TRUE,
          source_data_status VARCHAR(30) NOT NULL DEFAULT 'awaiting_source',
          source_count INT NOT NULL DEFAULT 0,
          source_is_partial BOOLEAN NOT NULL DEFAULT TRUE,
          generated_at TIMESTAMPTZ DEFAULT NOW(),
          CONSTRAINT vkpi_weekly_reports_uniq UNIQUE (staff_id, layer, template_key, period_start)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_weekly_reports_period ON vkpi_weekly_reports(period_start DESC, period_end DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_weekly_reports_staff ON vkpi_weekly_reports(staff_id, period_start DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_weekly_reports_status ON vkpi_weekly_reports(status, generated_at DESC)")
    # CREATE TABLE IF NOT EXISTS does not evolve an already-created SQLite
    # table.  The production PostgreSQL path is handled by migration 256; this
    # guarded compatibility loop keeps local/tests on the same typed contract.
    if not is_postgres_runtime():
        columns = {
            str(row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1])
            for row in conn.execute("PRAGMA table_info(vkpi_weekly_reports)").fetchall()
        }
        for column, declaration in (
            ("truth_invalidated_at", "TEXT"),
            ("truth_invalidation_reason", "TEXT NOT NULL DEFAULT ''"),
            ("truth_invalidation_migration", "INTEGER"),
            ("truth_restorable", "INTEGER NOT NULL DEFAULT 1"),
            ("source_data_status", "TEXT NOT NULL DEFAULT 'awaiting_source'"),
            ("source_count", "INTEGER NOT NULL DEFAULT 0"),
            ("source_is_partial", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE vkpi_weekly_reports ADD COLUMN {column} {declaration}"
                )
    conn.commit()


def _ensure_weekly_reports_read_schema() -> None:
    """Avoid compatibility DDL inside the release-fenced read transaction."""
    if not release_validation_active():
        ensure_vkpi_weekly_reports_schema()


def _safe_section(label: str, fn) -> str:
    try:
        return fn()
    except Exception as exc:
        return f"({label} unavailable: {str(exc)[:120]})"


def _source_truth_from_policy(policy_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce explicit source checks to the persisted public truth contract.

    Report body availability and source truth are intentionally independent:
    deterministic markdown can remain readable while its business claims stay
    awaiting_source.  Only non-empty, all-real, passed source checks may become
    ``real``.
    """

    checks = policy_payload.get("checks") if isinstance(policy_payload, Mapping) else {}
    source_check = checks.get("sources") if isinstance(checks, Mapping) else {}
    raw_items = source_check.get("items") if isinstance(source_check, Mapping) else []
    items = [dict(item) for item in raw_items if isinstance(item, Mapping)] if isinstance(raw_items, list) else []
    trusted_count = sum(
        max(0, int(item.get("source_count") or 0))
        for item in items
        if str(item.get("data_status") or "").strip().lower() == "real"
    )
    all_real = bool(items) and all(
        str(item.get("data_status") or "").strip().lower() == "real"
        and int(item.get("source_count") or 0) > 0
        for item in items
    )
    passed = source_check.get("passed") is True if isinstance(source_check, Mapping) else False
    if passed and all_real and trusted_count > 0:
        status = "real"
    elif trusted_count > 0:
        status = "partial"
    else:
        status = "awaiting_source"
    return {
        "source_data_status": status,
        "source_count": trusted_count,
        "source_is_partial": status != "real",
    }


def _staff_role_for_templates(role: str, *, is_owner: bool = False) -> str:
    normalized = str(role or "").lower()
    if is_owner or normalized in {"owner", "admin", "leader"}:
        return "leader"
    if normalized in {"kol_ops", "marketing_analyst"}:
        return normalized
    return "kol_ops"


# ─── Section Data Queries ──────────────────────────────────────

def _query_section_data(
    section_key: str,
    *,
    period_start: date,
    period_end: date,
    staff_id: int | None = None,
    owned_kols: list[int] | None = None,
) -> str:
    """Return short grounded text for a report section using current schema."""
    conn = get_conn()
    period_start_iso = period_start.strftime("%Y-%m-%dT00:00:00Z")
    period_end_iso = period_end.strftime("%Y-%m-%dT23:59:59Z")

    if section_key == "weekly_kpi_summary":
        def run() -> str:
            rows = conn.execute(
                """
                SELECT platform, COUNT(*) AS posts,
                       SUM(COALESCE(likes,0)) AS total_likes,
                       SUM(COALESCE(comments,0)) AS total_comments
                FROM vkpi_industry_posts
                WHERE created_at BETWEEN ? AND ?
                GROUP BY platform
                ORDER BY total_likes DESC
                LIMIT 7
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            if not rows:
                return "(no posts this period)"
            lines = ["Platform metrics this period:"]
            for r in rows:
                lines.append(f"  - {r['platform']}: {r['posts']} posts, {r['total_likes'] or 0} likes, {r['total_comments'] or 0} comments")
            return "\n".join(lines)
        return _safe_section(section_key, run)

    if section_key == "comment_intelligence_summary":
        def run() -> str:
            run_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM vkpi_comment_intelligence_runs
                WHERE created_at BETWEEN ? AND ?
                GROUP BY status
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            sentiment_rows = conn.execute(
                """
                SELECT COALESCE(sentiment, 'unknown') AS label, COUNT(*) AS n
                FROM vkpi_sentiment_results
                WHERE analyzed_at BETWEEN ? AND ?
                GROUP BY COALESCE(sentiment, 'unknown')
                ORDER BY n DESC, label ASC
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            brand_rows = conn.execute(
                """
                SELECT COALESCE(brand_attitude, 'unknown') AS label, COUNT(*) AS n
                FROM vkpi_sentiment_results
                WHERE analyzed_at BETWEEN ? AND ?
                GROUP BY COALESCE(brand_attitude, 'unknown')
                ORDER BY n DESC, label ASC
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            pillar_rows = conn.execute(
                """
                SELECT pl.display_name, pl.layer, COUNT(*) AS n
                FROM vkpi_post_pillars pp
                JOIN vkpi_pillars pl ON pl.id = pp.pillar_id
                WHERE pp.classified_at BETWEEN ? AND ?
                GROUP BY pl.display_name, pl.layer
                ORDER BY n DESC, pl.display_name ASC
                LIMIT 5
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            coverage = conn.execute(
                """
                SELECT
                  COUNT(*) AS comments_total,
                  SUM(CASE WHEN sentiment_id IS NOT NULL THEN 1 ELSE 0 END) AS with_sentiment,
                  SUM(CASE WHEN pillar_id IS NOT NULL THEN 1 ELSE 0 END) AS with_pillar
                FROM vkpi_comments
                WHERE fetched_at BETWEEN ? AND ?
                """,
                (period_start_iso, period_end_iso),
            ).fetchone() or {}
            total_comments = int(coverage.get("comments_total") or 0)
            with_sentiment = int(coverage.get("with_sentiment") or 0)
            with_pillar = int(coverage.get("with_pillar") or 0)
            if not run_rows and not sentiment_rows and not pillar_rows and not total_comments:
                return "(no comment intelligence data this period)"
            total_runs = sum(int(r["n"] or 0) for r in run_rows)
            ok_runs = sum(int(r["n"] or 0) for r in run_rows if str(r["status"]) == "ok")
            success_rate = ok_runs / total_runs * 100 if total_runs else 0
            lines = [
                "Comment intelligence summary:",
                f"  - Pipeline runs: {total_runs} total, {ok_runs} ok ({success_rate:.1f}% success)",
                f"  - Comment coverage: {total_comments} comments, {with_sentiment} with sentiment, {with_pillar} with pillar",
            ]
            if sentiment_rows:
                lines.append("  - Sentiment: " + ", ".join(f"{r['label']}={r['n']}" for r in sentiment_rows))
            if brand_rows:
                lines.append("  - Brand attitude: " + ", ".join(f"{r['label']}={r['n']}" for r in brand_rows))
            if pillar_rows:
                lines.append("  - Top pillars: " + ", ".join(f"{r['display_name']} L{r['layer']}={r['n']}" for r in pillar_rows))
            return "\n".join(lines)
        return _safe_section(section_key, run)

    if section_key == "top_kols":
        def run() -> str:
            rows = conn.execute(
                """
                SELECT a.id, a.handle, a.platform, COUNT(p.id) AS posts,
                       SUM(COALESCE(p.likes,0)) AS total_likes
                FROM vkpi_industry_accounts a
                JOIN vkpi_industry_posts p ON p.account_id = a.id
                WHERE p.created_at BETWEEN ? AND ?
                GROUP BY a.id, a.handle, a.platform
                ORDER BY total_likes DESC
                LIMIT 5
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            if not rows:
                return "(no KOL activity)"
            lines = ["Top 5 accounts by engagement:"]
            for r in rows:
                lines.append(f"  - {r['handle']} ({r['platform']}): {r['posts']} posts, {r['total_likes'] or 0} likes")
            return "\n".join(lines)
        return _safe_section(section_key, run)

    if section_key == "top_events":
        def run() -> str:
            rows = conn.execute(
                """
                SELECT a.handle, p.platform, p.title, p.likes, p.comments, p.post_url
                FROM vkpi_industry_posts p
                JOIN vkpi_industry_accounts a ON a.id = p.account_id
                WHERE p.created_at BETWEEN ? AND ?
                ORDER BY p.likes DESC NULLS LAST
                LIMIT 3
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            if not rows:
                return "(no notable events)"
            lines = ["Top performing posts:"]
            for r in rows:
                title = (r["title"] or "")[:80]
                lines.append(f"  - {r['handle']} on {r['platform']}: '{title}' ({r['likes'] or 0} likes, {r['comments'] or 0} comments)")
            return "\n".join(lines)
        return _safe_section(section_key, run)

    if section_key == "sentiment_distribution":
        def run() -> str:
            rows = conn.execute(
                """
                SELECT sentiment, COUNT(*) AS n
                FROM vkpi_sentiment_results
                WHERE analyzed_at BETWEEN ? AND ?
                GROUP BY sentiment
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            if not rows:
                return "(no sentiment data this period)"
            total = sum(r["n"] for r in rows)
            lines = ["Sentiment distribution:"]
            for r in rows:
                pct = r["n"] / total * 100 if total else 0
                lines.append(f"  - {r['sentiment']}: {r['n']} ({pct:.1f}%)")
            return "\n".join(lines)
        return _safe_section(section_key, run)

    if section_key == "pillar_distribution":
        def run() -> str:
            rows = conn.execute(
                """
                SELECT pl.pillar_key, pl.display_name, pl.layer, COUNT(*) AS n
                FROM vkpi_post_pillars pp
                JOIN vkpi_pillars pl ON pl.id = pp.pillar_id
                WHERE pp.classified_at BETWEEN ? AND ? AND pp.is_primary
                GROUP BY pl.pillar_key, pl.display_name, pl.layer
                ORDER BY n DESC
                LIMIT 10
                """,
                (period_start_iso, period_end_iso),
            ).fetchall()
            if not rows:
                return "(no pillar classifications this period)"
            lines = ["Top pillars (primary):"]
            for r in rows:
                lines.append(f"  - {r['display_name']} (L{r['layer']}): {r['n']} posts")
            return "\n".join(lines)
        return _safe_section(section_key, run)

    if section_key == "anomaly_detection":
        def run() -> str:
            hostile = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM vkpi_sentiment_results
                WHERE analyzed_at BETWEEN ? AND ? AND brand_attitude = 'hostile'
                """,
                (period_start_iso, period_end_iso),
            ).fetchone()
            cutoff_14d = (period_end - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
            silent = conn.execute(
                """
                SELECT COUNT(DISTINCT a.id) AS n
                FROM vkpi_industry_accounts a
                LEFT JOIN vkpi_industry_posts p ON p.account_id = a.id AND p.created_at >= ?
                WHERE p.id IS NULL
                """,
                (cutoff_14d,),
            ).fetchone()
            return f"Anomalies:\n  - Hostile brand_attitude posts: {hostile['n'] if hostile else 0}\n  - Accounts silent >14 days: {silent['n'] if silent else 0}"
        return _safe_section(section_key, run)

    if section_key == "budget_status":
        def run() -> str:
            month_start = period_end.replace(day=1).strftime("%Y-%m-%dT00:00:00Z")
            cost = conn.execute(
                """
                SELECT SUM(cost_cents) AS total_cents, COUNT(*) AS calls
                FROM vkpi_llm_calls
                WHERE created_at >= ?
                """,
                (month_start,),
            ).fetchone()
            return f"LLM cost MTD: ${(cost['total_cents'] or 0)/100:.2f} ({cost['calls'] or 0} calls)"
        return _safe_section(section_key, run)

    if section_key == "your_kols_status":
        return "(personal KOL ownership is not mapped in current staff schema yet)"

    return f"(data query for '{section_key}' not implemented)"

def _build_data_context(
    template_key: str,
    *,
    period_start: date,
    period_end: date,
    staff_id: int | None,
    owned_kols: list[int] | None,
) -> str:
    """Aggregate all section data into single context string."""
    template = TEMPLATES.get(template_key)
    if not template:
        return "(template not found)"
    
    parts = []
    for section in template["sections"]:
        snippet = _query_section_data(
            section,
            period_start=period_start,
            period_end=period_end,
            staff_id=staff_id,
            owned_kols=owned_kols,
        )
        parts.append(f"### {section}\n{snippet}\n")
    
    return "\n".join(parts)


# ─── Main Generator API ───────────────────────────────────────

def generate_for_template(
    *,
    staff_id: int | None,
    template_key: str,
    period_start: date,
    period_end: date,
    model_policy_input: Mapping[str, Any] | None = None,
) -> dict:
    """Generate single report for staff + template."""
    ensure_vkpi_weekly_reports_schema()
    conn = get_conn()
    
    # Skip if already exists
    existing = conn.execute(
        """
        SELECT id FROM vkpi_weekly_reports 
        WHERE COALESCE(staff_id, -1) = COALESCE(?, -1)
          AND template_key = ? 
          AND period_start = ?
        """,
        (staff_id, template_key, period_start),
    ).fetchone()
    if existing:
        return {
            "status": "duplicate",
            "report_id": existing["id"],
        }
    
    # Lookup staff
    staff_name = "All Staff"
    staff_role = "all"
    owned_kols = None
    
    if staff_id:
        staff_row = conn.execute(
            """
            SELECT s.id, s.role, s.is_owner, COALESCE(u.name, 'Staff ' || s.id::TEXT) AS name
            FROM staff s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.id = ?
            """,
            (staff_id,),
        ).fetchone()
        if staff_row:
            staff_name = staff_row["name"]
            staff_role = _staff_role_for_templates(staff_row["role"], is_owner=bool(staff_row["is_owner"]))
            owned_kols = []
    
    # Build context
    data_context = _build_data_context(
        template_key,
        period_start=period_start,
        period_end=period_end,
        staff_id=staff_id,
        owned_kols=owned_kols,
    )
    
    # Build prompt
    prompt = build_prompt(
        template_key,
        staff_name=staff_name,
        staff_role=staff_role,
        period_start=_format_date(period_start),
        period_end=_format_date(period_end),
        data_context=data_context,
    )
    
    # Model-policy gate.  The legacy text context has no structured provenance,
    # so provider execution remains blocked unless a trusted internal caller
    # supplies explicit readiness/source evidence and runtime verification also
    # passes the shared exact-model contract.
    template = TEMPLATES[template_key]
    model_decision = _explicit_report_model_policy(model_policy_input)
    policy_payload = model_decision.to_dict()
    source_truth = _source_truth_from_policy(policy_payload)
    response = _invoke_exact_report_model(
        prompt,
        decision=model_decision,
        purpose="vkpi_weekly_report",
        max_output_tokens=template["max_output_tokens"],
        metadata={
            "template_key": template_key,
            "period_start": _format_date(period_start),
            "period_end": _format_date(period_end),
        },
    )
    if response is None:
        response = {
            "status": "fallback_to_rule",
            "text": "",
            "provider": "rule_v0",
            "model": "deterministic_descriptive",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_cents": 0,
            "reason": "report_model_policy_blocked",
        }
    
    response_status = str(response.get("status") or "")
    if response_status not in {"success", "fallback_to_rule"}:
        return {
            "staff_id": staff_id,
            "template_key": template_key,
            "status": "fail",
            "error": response.get("error") or response.get("reason"),
        }
    
    # Persist
    title = title_for(
        template_key,
        _format_date(period_start),
        _format_date(period_end),
    )
    body = response.get("text", "") or (
        "Deterministic descriptive report generated from grounded context.\n\n"
        + data_context[:3000]
    )
    
    cursor = conn.execute(
        """
        INSERT INTO vkpi_weekly_reports (
          staff_id, layer, template_key,
          period_start, period_end,
          title, body_md,
          llm_provider, llm_model, prompt_version,
          input_tokens, output_tokens, cost_cents,
          status, source_data_status, source_count, source_is_partial, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            staff_id, template["layer"], template_key,
            period_start, period_end,
            title, body,
            response.get("provider"), response.get("model"), PROMPT_VERSION,
            response.get("input_tokens", 0), response.get("output_tokens", 0),
            response.get("cost_cents", 0),
            "draft",
            source_truth["source_data_status"],
            source_truth["source_count"],
            source_truth["source_is_partial"],
            _now_iso(),
        ),
    )
    
    conn.commit()

    return {
        "staff_id": staff_id,
        "template_key": template_key,
        "status": "ok",
        "title": title,
        "cost_cents": response.get("cost_cents", 0),
        "provider": response.get("provider"),
        "claim_level": model_decision.claim_level,
        "model_policy": policy_payload,
    }


def generate_for_staff(
    staff_id: int,
    *,
    period_start: date,
    period_end: date,
    layers: list[int] | None = None,
) -> dict:
    """Generate all applicable reports for one staff member."""
    ensure_vkpi_weekly_reports_schema()
    conn = get_conn()
    staff_row = conn.execute(
        """
        SELECT s.id, s.role, s.is_owner, COALESCE(u.name, 'Staff ' || s.id::TEXT) AS name
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (staff_id,),
    ).fetchone()
    if not staff_row:
        return {"status": "fail", "error": f"staff {staff_id} not found"}
    
    role = _staff_role_for_templates(staff_row["role"], is_owner=bool(staff_row["is_owner"]))
    template_keys = template_keys_for_role(role)
    
    # Filter by requested layers
    if layers:
        template_keys = [
            tk for tk in template_keys 
            if TEMPLATES.get(tk, {}).get("layer") in layers
        ]
    
    results = []
    for tk in template_keys:
        result = generate_for_template(
            staff_id=staff_id,
            template_key=tk,
            period_start=period_start,
            period_end=period_end,
        )
        results.append(result)
    
    return {
        "staff_id": staff_id,
        "staff_name": staff_row["name"],
        "role": role,
        "reports": results,
        "total_reports": len(results),
    }


def generate_for_all_staff(
    *,
    period_end: date | None = None,
) -> dict:
    """Generate weekly reports for all active staff."""
    if period_end is None:
        period_end = date.today()
    period_start = period_end - timedelta(days=7)
    
    ensure_vkpi_weekly_reports_schema()
    conn = get_conn()
    staff_rows = conn.execute(
        """
        SELECT s.id, s.role, s.is_owner, COALESCE(u.name, 'Staff ' || s.id::TEXT) AS name
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE COALESCE(s.active, 1) IN (1, TRUE)
        ORDER BY s.id
        LIMIT 50
        """
    ).fetchall()
    
    summary = {
        "period_start": _format_date(period_start),
        "period_end": _format_date(period_end),
        "staff_count": len(staff_rows),
        "total_reports": 0,
        "by_status": {},
        "results": [],
    }
    
    for staff in staff_rows:
        result = generate_for_staff(
            staff_id=staff["id"],
            period_start=period_start,
            period_end=period_end,
        )
        summary["total_reports"] += result.get("total_reports", 0)
        summary["results"].append(result)
        
        for r in result.get("reports", []):
            status = r.get("status", "unknown")
            summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
    
    return summary


# Leader-layer templates carry company-wide budget / AI spend (see weekly_templates
# layer2_leader → budget_status). Per the read ruling employees may not see AI spend,
# so these reports are readable by management only.
_LEADER_TEMPLATE_KEYS = ("layer2_leader",)


def _report_readable(report: dict, staff: dict[str, Any] | None) -> bool:
    """Weekly-report read ruling (mirrors reports.report_file / scope.assert_staff_access).

    Management (can_view_all) reads everything. Non-management may only read reports
    filed under their own staff_id, and never the company budget / AI-spend leader
    reports nor company-level (unowned) reports.
    """
    if scope.can_view_all(staff, domain="export"):
        return True
    try:
        report_staff_id = int(report.get("staff_id") or 0)
    except (TypeError, ValueError):
        report_staff_id = 0
    if not report_staff_id:
        return False
    if str(report.get("template_key") or "") in _LEADER_TEMPLATE_KEYS:
        return False
    return report_staff_id == scope.actor_staff_id(staff)


def _public_report(report: dict[str, Any], *, include_body: bool) -> dict[str, Any]:
    """Serialize a weekly artifact without leaking withdrawn markdown."""

    item = dict(report)
    invalidated = (
        str(item.get("status") or "").strip().lower() == "invalidated"
        or bool(item.get("truth_invalidated_at"))
        or int(item.get("truth_invalidation_migration") or 0) == 256
    )
    item["truth_invalidated"] = invalidated
    item["truth_invalidation_reason"] = (
        str(item.get("truth_invalidation_reason") or "").strip()
        if invalidated
        else ""
    )
    stored_status = str(item.get("source_data_status") or "").strip().lower()
    stored_source_count = int(item.get("source_count") or 0)
    if invalidated:
        public_status = "unavailable"
    elif stored_status == "real" and stored_source_count > 0:
        public_status = "real"
    elif stored_status == "partial" and stored_source_count > 0:
        public_status = "partial"
    else:
        public_status = "awaiting_source"
    item["data_status"] = public_status
    item["is_partial"] = bool(item.get("source_is_partial")) or public_status != "real"
    if invalidated or not include_body:
        item.pop("body_md", None)
    return item


def get_report(report_id: int, *, staff: dict[str, Any] | None = None) -> dict:
    """Get full report by ID, enforcing the weekly-report read ruling."""
    _ensure_weekly_reports_read_schema()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, staff_id, layer, template_key, period_start, period_end,
               title, body_md, llm_provider, status, cost_cents, generated_at,
               truth_invalidated_at, truth_invalidation_reason,
               truth_invalidation_migration, truth_restorable,
               source_data_status, source_count, source_is_partial
        FROM vkpi_weekly_reports WHERE id = ?
        """,
        (report_id,),
    ).fetchone()
    if not row:
        return {
            "status": "not_found",
            "data_status": "no_data",
            "report_id": report_id,
            "reason": "report_not_found",
        }
    report = dict(row)
    if not _report_readable(report, staff):
        raise scope.ScopeDenied("weekly report scope denied")
    return _public_report(report, include_body=True)


def list_reports(
    *,
    staff_id: int | None = None,
    period_start: date | None = None,
    limit: int = 100,
    staff: dict[str, Any] | None = None,
) -> dict:
    """List weekly reports, scoped to the caller's read permissions."""
    _ensure_weekly_reports_read_schema()
    conn = get_conn()

    where = ["1=1"]
    params: list = []
    if scope.can_view_all(staff, domain="export"):
        if staff_id is not None:
            where.append("staff_id = ?")
            params.append(staff_id)
    else:
        # Non-management: own reports only, and never the leader budget/AI-spend reports.
        where.append("staff_id = ?")
        params.append(scope.actor_staff_id(staff))
        placeholders = ", ".join("?" for _ in _LEADER_TEMPLATE_KEYS)
        where.append(f"template_key NOT IN ({placeholders})")
        params.extend(_LEADER_TEMPLATE_KEYS)
    if period_start:
        where.append("period_start >= ?")
        params.append(period_start)

    where_sql = " AND ".join(where)
    rows = conn.execute(
        f"""
        SELECT id, staff_id, layer, template_key, period_start, period_end,
               title, status, cost_cents, generated_at,
               truth_invalidated_at, truth_invalidation_reason,
               truth_invalidation_migration, truth_restorable,
               source_data_status, source_count, source_is_partial
        FROM vkpi_weekly_reports
        WHERE {where_sql}
        ORDER BY period_start DESC, staff_id ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    return {
        "count": len(rows),
        "reports": [_public_report(dict(r), include_body=False) for r in rows],
    }
