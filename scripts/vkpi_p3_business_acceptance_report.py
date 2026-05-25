#!/usr/bin/env python3
"""Read-only P3 business usability acceptance report for V-KPI.

This report checks the business decision chain that P1-P3 built up:
Search -> IntelligenceCard/Evidence -> Decision audit/follow-up ->
Recommendation feedback readiness.

It does not call providers, enqueue tasks, run sync jobs, or write decision
rows. It intentionally reads existing tables directly for audit/backlog
readiness so a report run cannot create schema as a side effect.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains.kol import decision_audit as kol_decisions  # noqa: E402
from app.services.vkpi import (  # noqa: E402
    kol_intelligence_card,
    natural_search,
    recommendation_feedback_backlog,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return int(default or 0)


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _status_error(exc: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "error": f"{type(exc).__name__}: {_text(exc, 240)}",
    }


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _count(table_name: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(table_name):
        return 0
    clause = f" WHERE {where}" if where else ""
    try:
        row = get_conn().execute(f"SELECT COUNT(*) AS count FROM {table_name}{clause}", params).fetchone()
        return _int(row["count"] if row else 0)
    except Exception:
        return 0


def _search_summary(query: str, *, limit: int) -> dict[str, Any]:
    try:
        payload = natural_search.search(query, limit=limit)
    except Exception as exc:
        return {
            **_status_error(exc),
            "query": query,
            "provider_calls": False,
            "write_db": False,
            "total": 0,
            "items": [],
            "candidate": {},
        }
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    candidate = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("source_table") == "vkpi_kol_pool" and _int(item.get("source_id")) > 0:
            candidate = {
                "kol_pool_id": _int(item.get("source_id")),
                "title": _text(item.get("title")),
                "platform": _text(item.get("platform")),
                "handle": _text(item.get("handle")),
                "score": _int(item.get("score")),
            }
            break
    return {
        "status": "ready",
        "query": query,
        "provider_calls": bool(payload.get("provider_calls")),
        "write_db": bool(payload.get("write_db")),
        "total": _int(payload.get("total")),
        "result_types": sorted({str(item.get("result_type") or "") for item in items if isinstance(item, dict)}),
        "candidate": candidate,
    }


def _intelligence_summary(kol_pool_id: int, *, include_product_fit: bool) -> dict[str, Any]:
    if kol_pool_id <= 0:
        return {
            "status": "skipped",
            "reason": "no_kol_pool_candidate",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "evidence_count": 0,
            "decision_readiness": "unknown",
        }
    try:
        payload = kol_intelligence_card.build_kol_pool_intelligence_card(
            kol_pool_id,
            include_product_fit=include_product_fit,
        )
    except Exception as exc:
        return {
            **_status_error(exc),
            "kol_pool_id": kol_pool_id,
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "evidence_count": 0,
            "decision_readiness": "unknown",
        }
    evidence_index = payload.get("evidence_index") if isinstance(payload.get("evidence_index"), list) else []
    decision_support = payload.get("decision_support") if isinstance(payload.get("decision_support"), dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    section_statuses = {
        key: _text((payload.get(key) or {}).get("status"), 80)
        for key in ("dimensions11", "competitors", "brand_signal", "comment_intelligence", "memory_card", "product_fit")
        if isinstance(payload.get(key), dict)
    }
    return {
        "status": "ready",
        "kol_pool_id": kol_pool_id,
        "title": _text(item.get("display_name") or item.get("handle")),
        "platform": _text(item.get("platform")),
        "handle": _text(item.get("handle")),
        "provider_calls": bool(payload.get("provider_calls")),
        "llm_calls": bool(payload.get("llm_calls")),
        "write_db": bool(payload.get("write_db")),
        "evidence_count": sum(_int(row.get("evidence_count")) for row in evidence_index if isinstance(row, dict)),
        "evidence_sections": len(evidence_index),
        "decision_readiness": _text(decision_support.get("readiness") or "unknown", 40),
        "ready_sections": _int(decision_support.get("ready_sections")),
        "total_sections": _int(decision_support.get("total_sections")),
        "gaps": decision_support.get("gaps") if isinstance(decision_support.get("gaps"), list) else [],
        "section_statuses": section_statuses,
    }


def _decision_audit_summary(kol_pool_id: int, *, limit: int) -> dict[str, Any]:
    audit_ready = _table_exists("vkpi_kol_decision_audit")
    followup_ready = _table_exists("vkpi_kol_decision_followups")
    decisions_total = _count("vkpi_kol_decision_audit")
    followups_total = _count("vkpi_kol_decision_followups")
    candidate_decisions = _count("vkpi_kol_decision_audit", "kol_pool_id=?", (kol_pool_id,)) if kol_pool_id > 0 else 0
    recent: list[dict[str, Any]] = []
    if audit_ready:
        try:
            rows = get_conn().execute(
                """
                SELECT decision_uid, kol_pool_id, decision_key, decision_label, severity, created_at
                FROM vkpi_kol_decision_audit
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(50, int(limit or 20))),),
            ).fetchall()
            recent = [
                {
                    "decision_uid": _text(row["decision_uid"], 80),
                    "kol_pool_id": _int(row["kol_pool_id"]),
                    "decision_key": _text(row["decision_key"], 40),
                    "decision_label": _text(row["decision_label"], 80),
                    "severity": _text(row["severity"], 40),
                    "created_at": _text(row["created_at"], 80),
                }
                for row in rows
            ]
        except Exception:
            recent = []
    return {
        "status": "ready" if audit_ready and followup_ready else "missing_schema",
        "write_db": False,
        "decision_options": kol_decisions.DECISION_OPTIONS,
        "followup_outcomes": kol_decisions.FOLLOWUP_OUTCOMES,
        "decision_schema_ready": audit_ready,
        "followup_schema_ready": followup_ready,
        "decisions_total": decisions_total,
        "candidate_decisions": candidate_decisions,
        "followups_total": followups_total,
        "recent_decisions": recent,
    }


def _recommendation_feedback_summary(*, limit: int) -> dict[str, Any]:
    core_tables = {
        "runs": _table_exists("vkpi_kol_recommendation_runs"),
        "recommendations": _table_exists("vkpi_kol_recommendations"),
        "feedback": _table_exists("vkpi_recommendation_feedback"),
        "outcomes": _table_exists("vkpi_recommendation_outcomes"),
    }
    schema_ready = all(core_tables.values())
    summary = {
        "recommendation_rows": 0,
        "missing_feedback_rows": 0,
        "with_feedback_rows": 0,
        "run_count": 0,
    }
    runs: list[dict[str, Any]] = []
    if schema_ready:
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT
                  COUNT(DISTINCT rec.id) AS recommendation_rows,
                  COUNT(DISTINCT CASE WHEN fb.id IS NULL THEN rec.id END) AS missing_feedback_rows,
                  COUNT(DISTINCT CASE WHEN fb.id IS NOT NULL THEN rec.id END) AS with_feedback_rows,
                  COUNT(DISTINCT r.id) AS run_count
                FROM vkpi_kol_recommendations rec
                INNER JOIN vkpi_kol_recommendation_runs r ON r.id = rec.run_id
                LEFT JOIN vkpi_recommendation_feedback fb ON fb.recommendation_id = rec.id
                WHERE r.status IN ('previewed', 'completed')
                """
            ).fetchone()
            if row:
                summary = {key: _int(row[key]) for key in summary.keys()}
        except Exception:
            pass
        try:
            rows = conn.execute(
                """
                SELECT
                  r.run_uid,
                  r.strategy_version,
                  r.status,
                  COUNT(DISTINCT rec.id) AS recommendation_rows,
                  COUNT(DISTINCT fb.id) AS feedback_rows,
                  COUNT(DISTINCT CASE WHEN fb.id IS NULL THEN rec.id END) AS missing_feedback_rows
                FROM vkpi_kol_recommendation_runs r
                LEFT JOIN vkpi_kol_recommendations rec ON rec.run_id = r.id
                LEFT JOIN vkpi_recommendation_feedback fb ON fb.recommendation_id = rec.id
                WHERE r.status IN ('previewed', 'completed')
                GROUP BY r.id, r.run_uid, r.strategy_version, r.status
                HAVING COUNT(DISTINCT rec.id) > 0
                ORDER BY missing_feedback_rows DESC, r.id DESC
                LIMIT ?
                """,
                (max(1, min(50, int(limit or 20))),),
            ).fetchall()
            runs = [
                {
                    "run_uid": _text(row["run_uid"], 100),
                    "strategy_version": _text(row["strategy_version"], 100),
                    "status": _text(row["status"], 40),
                    "recommendation_rows": _int(row["recommendation_rows"]),
                    "feedback_rows": _int(row["feedback_rows"]),
                    "missing_feedback_rows": _int(row["missing_feedback_rows"]),
                }
                for row in rows
            ]
        except Exception:
            runs = []
    return {
        "status": "ready" if schema_ready else "missing_schema",
        "provider_calls": False,
        "write_db": False,
        "schema_ready": schema_ready,
        "tables": core_tables,
        "summary": summary,
        "runs": runs,
        "csv_fields": list(recommendation_feedback_backlog.CSV_FIELDS),
    }


def build_report(
    *,
    query: str = "viltrox",
    kol_pool_id: int = 0,
    limit: int = 20,
    include_product_fit: bool = True,
) -> dict[str, Any]:
    search = _search_summary(query, limit=limit)
    candidate_id = _int(kol_pool_id) or _int((search.get("candidate") or {}).get("kol_pool_id"))
    intelligence = _intelligence_summary(candidate_id, include_product_fit=include_product_fit)
    decision_audit = _decision_audit_summary(candidate_id, limit=limit)
    recommendation_feedback = _recommendation_feedback_summary(limit=limit)
    checks = {
        "search_readable": search.get("status") == "ready" and _int(search.get("total")) > 0,
        "search_has_kol_candidate": candidate_id > 0,
        "search_no_provider_or_write": not bool(search.get("provider_calls")) and not bool(search.get("write_db")),
        "intelligence_card_readable": intelligence.get("status") == "ready",
        "intelligence_has_evidence_index": _int(intelligence.get("evidence_sections")) > 0,
        "intelligence_decision_ready": intelligence.get("decision_readiness") in {"ready", "partial"},
        "intelligence_no_provider_llm_or_write": not bool(intelligence.get("provider_calls"))
        and not bool(intelligence.get("llm_calls"))
        and not bool(intelligence.get("write_db")),
        "decision_labels_available": set(kol_decisions.DECISION_OPTIONS.keys()) == {"contact", "watch", "caution", "avoid"},
        "decision_audit_schema_ready": bool(decision_audit.get("decision_schema_ready")),
        "decision_followup_schema_ready": bool(decision_audit.get("followup_schema_ready")),
        "recommendation_feedback_schema_ready": bool(recommendation_feedback.get("schema_ready")),
        "recommendation_feedback_csv_contract_ready": len(recommendation_feedback.get("csv_fields") or []) >= 20,
        "no_side_effects": True,
    }
    return {
        "mode": "read_only_p3_business_acceptance_report",
        "generated_at": _now(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "query": query,
        "kol_pool_id": candidate_id,
        "search": search,
        "intelligence_card": intelligence,
        "decision_audit": decision_audit,
        "recommendation_feedback": recommendation_feedback,
    }


def render_markdown(report: dict[str, Any]) -> str:
    search = report["search"]
    intelligence = report["intelligence_card"]
    decisions = report["decision_audit"]
    feedback = report["recommendation_feedback"]
    feedback_summary = feedback.get("summary") if isinstance(feedback.get("summary"), dict) else {}
    lines = [
        "# V-KPI P3 Business Acceptance Report",
        "",
        "Read-only P3 acceptance report. It does not call providers, LLMs, sync jobs, task queues, or write decision rows.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Query: `{report['query']}`",
        f"- KOL pool ID: `{report['kol_pool_id'] or 'none'}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Write DB: `{str(report['write_db']).lower()}`",
        "",
        "## Flow Summary",
        "",
        "| Step | Status | Evidence |",
        "| --- | --- | --- |",
        (
            f"| Search | `{search.get('status')}` | "
            f"total={_int(search.get('total'))}, candidate={((search.get('candidate') or {}).get('kol_pool_id') or 'none')} |"
        ),
        (
            f"| IntelligenceCard / Evidence | `{intelligence.get('status')}` | "
            f"readiness={intelligence.get('decision_readiness')}, "
            f"sections={_int(intelligence.get('evidence_sections'))}, evidence={_int(intelligence.get('evidence_count'))} |"
        ),
        (
            f"| Decision audit | `{decisions.get('status')}` | "
            f"decisions={_int(decisions.get('decisions_total'))}, followups={_int(decisions.get('followups_total'))}, "
            f"labels={','.join((decisions.get('decision_options') or {}).keys())} |"
        ),
        (
            f"| Recommendation feedback | `{feedback.get('status')}` | "
            f"runs={_int(feedback_summary.get('run_count'))}, recs={_int(feedback_summary.get('recommendation_rows'))}, "
            f"missing_feedback={_int(feedback_summary.get('missing_feedback_rows'))} |"
        ),
        "",
        "## Checks",
        "",
    ]
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(
        [
            "",
            "## Section Statuses",
            "",
            f"`{intelligence.get('section_statuses') or {}}`",
            "",
            "## Recommendation Runs",
            "",
        ]
    )
    for run in feedback.get("runs") or []:
        lines.append(
            f"- `{run.get('run_uid')}`: status={run.get('status')} "
            f"strategy={run.get('strategy_version')} missing_feedback={run.get('missing_feedback_rows')}"
        )
    if not feedback.get("runs"):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only V-KPI P3 business acceptance report.")
    parser.add_argument("--query", default="viltrox", help="Search query to use for the Search -> Detail path")
    parser.add_argument("--kol-pool-id", type=int, default=0, help="Optional explicit KOL pool ID for the detail card")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--skip-product-fit", action="store_true", help="Skip product fit in the IntelligenceCard")
    parser.add_argument("--json-out", default="", help="Write JSON report to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    return parser.parse_args(argv)


def _write(path_value: str, content: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def async_main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = build_report(
            query=str(args.query or "viltrox"),
            kol_pool_id=max(0, int(args.kol_pool_id or 0)),
            limit=max(1, min(100, int(args.limit or 20))),
            include_product_fit=not bool(args.skip_product_fit),
        )
        markdown = render_markdown(report)
        if args.json_out:
            _write(args.json_out, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if args.md_out:
            _write(args.md_out, markdown)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(markdown)
        return 0 if report.get("passed") else 3
    finally:
        await close_db_runtime()


def main(argv: list[str] | None = None) -> int:
    import asyncio

    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
