"""Report writers for read-only legacy import audits."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CSV_FIELDS = [
    "sheet",
    "row_number",
    "risk_level",
    "manual_review",
    "platform",
    "handle",
    "profile_url",
    "contact",
    "email",
    "product",
    "project",
    "owner",
    "status",
    "amount_raw",
    "amount_value",
    "currency",
    "issues_text",
    "raw_preview",
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def markdown_report(result: dict[str, Any]) -> str:
    source = result.get("source") or {}
    summary = result.get("summary") or {}
    issue_counts = result.get("issue_counts") or {}
    duplicates = result.get("duplicate_candidates") or []
    lines = [
        "# V-KPI P2A Legacy Excel Read-Only Audit",
        "",
        f"- Source: `{source.get('path', '')}`",
        f"- Filename: `{source.get('filename', '')}`",
        f"- Sheet filter: `{source.get('sheet') or 'all'}`",
        f"- Generated at: `{_utcnow()}`",
        "",
        "## Summary",
        "",
        f"- Total rows: {summary.get('total_rows', 0)}",
        f"- Recognizable platform/handle rows: {summary.get('recognizable_kol_rows', 0)}",
        f"- Duplicate KOL candidate rows: {summary.get('duplicate_kol_candidates', 0)}",
        f"- Missing contact rows: {summary.get('missing_contact_rows', 0)}",
        f"- Missing product/project rows: {summary.get('missing_product_project_rows', 0)}",
        f"- Amount/currency issue rows: {summary.get('amount_currency_issue_rows', 0)}",
        f"- Manual review rows: {summary.get('manual_review_rows', 0)}",
        "",
        "## Risk",
        "",
        f"- Low: {summary.get('low_risk_rows', 0)}",
        f"- Medium: {summary.get('medium_risk_rows', 0)}",
        f"- High: {summary.get('high_risk_rows', 0)}",
        "",
        "## Issue Counts",
        "",
    ]
    if issue_counts:
        for key, count in sorted(issue_counts.items()):
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none: 0")
    lines.extend(["", "## Duplicate Candidates", ""])
    if duplicates:
        for item in duplicates[:50]:
            lines.append(f"- {item.get('dedup_key')}: {item.get('count')}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## P2A Guardrail",
            "",
            "This audit is read-only. It does not write `kols`, `vkpi_projects`, `vkpi_kol_pool`, `vkpi_cost_ledger`, or `vkpi_ai_cost_ledger`.",
            "",
            "Detailed row issues are exported to the companion CSV.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_reports(result: dict[str, Any], output_dir: str | Path, *, prefix: str | None = None) -> dict[str, str]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = prefix or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = out_dir / f"{date_prefix}-vkpi-legacy-excel-audit.md"
    csv_path = out_dir / f"{date_prefix}-vkpi-legacy-excel-audit.csv"
    md_path.write_text(markdown_report(result), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in result.get("rows") or []:
            writer.writerow(row)
    return {"markdown": str(md_path), "csv": str(csv_path)}
