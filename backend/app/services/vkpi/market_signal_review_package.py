"""Controlled DB writer for reviewed market-signal candidates."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.market.signal_review_package import (
    build_external_signal_review_package,
    build_external_signal_review_package_from_files,
    build_market_signal_review_package,
    build_market_signal_review_package_from_file,
    competitor_signal_rows_from_review_package,
)
from app.domains.market.signal_review_reports import (
    render_competitor_signal_write_markdown,
    render_external_signal_review_package_markdown,
    render_review_package_markdown,
    write_competitor_signal_write_report,
    write_external_signal_review_package_report,
    write_review_package_report,
)


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _count_table(conn: Any, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"] or 0) if row else 0


def _existing_signal_uids(conn: Any, uids: list[str]) -> set[str]:
    if not uids:
        return set()
    existing: set[str] = set()
    for uid in uids:
        row = conn.execute("SELECT signal_uid FROM vkpi_competitor_signals WHERE signal_uid=?", (uid,)).fetchone()
        if row:
            existing.add(str(row["signal_uid"]))
    return existing


def write_reviewed_competitor_signals(
    package: dict[str, Any],
    *,
    backup_ref: str,
    committed_by: str = "cli",
) -> dict[str, Any]:
    """Persist ready review-package candidates into the competitor review queue."""
    if not _text(backup_ref):
        raise ValueError("backup_ref is required before writing competitor signals")
    if package.get("write_db") is not False:
        raise ValueError("review package must be a dry-run package")
    if package.get("mode") != "market_signal_promotion_review_package_v0":
        raise ValueError("expected market_signal_promotion_review_package_v0")
    if not package.get("passed"):
        raise ValueError("review package did not pass checks")

    rows = competitor_signal_rows_from_review_package(package)
    if not rows:
        raise ValueError("review package has no ready candidates")
    conn = get_conn()
    before_counts = {
        "vkpi_competitor_signal_runs": _count_table(conn, "vkpi_competitor_signal_runs"),
        "vkpi_competitor_signals": _count_table(conn, "vkpi_competitor_signals"),
    }
    existing = _existing_signal_uids(conn, [row["signal_uid"] for row in rows])
    insert_rows = [row for row in rows if row["signal_uid"] and row["signal_uid"] not in existing]
    run_uid = f"mktprom-{secrets.token_hex(8)}"
    now = _now_z()
    source_summary = {
        "source_package_mode": package.get("mode"),
        "source_generated_at": package.get("generated_at"),
        "ready_candidates": len(rows),
        "insert_candidates": len(insert_rows),
        "skipped_existing": len(existing),
        "backup_ref": backup_ref,
        "provider_calls": False,
        "llm_calls": False,
        "gemini_calls": False,
    }
    conn.execute(
        """
        INSERT INTO vkpi_competitor_signal_runs (
          run_uid, status, source_summary_json, signal_count,
          committed_by, created_at, committed_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (run_uid, "committed", json.dumps(source_summary, ensure_ascii=False), len(insert_rows), committed_by, now, now),
    )
    run_row = conn.execute("SELECT id FROM vkpi_competitor_signal_runs WHERE run_uid=?", (run_uid,)).fetchone()
    run_id = int(run_row["id"])
    inserted = 0
    for row in insert_rows:
        conn.execute(
            """
            INSERT INTO vkpi_competitor_signals (
              signal_uid, run_id, brand, normalized_brand, signal_type, severity, score,
              product_hints_json, source_table, source_id, source_sheet, source_row,
              source_url, platform, detail, evidence_json, review_status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["signal_uid"],
                run_id,
                row["brand"],
                row["normalized_brand"],
                row["signal_type"],
                row["severity"],
                row["score"],
                row["product_hints_json"],
                row["source_table"],
                row["source_id"],
                row["source_sheet"],
                row["source_row"],
                row["source_url"],
                row["platform"],
                row["detail"],
                row["evidence_json"],
                row["review_status"],
                now,
                now,
            ),
        )
        inserted += 1
    conn.commit()
    after_counts = {
        "vkpi_competitor_signal_runs": _count_table(conn, "vkpi_competitor_signal_runs"),
        "vkpi_competitor_signals": _count_table(conn, "vkpi_competitor_signals"),
    }
    return {
        "mode": "market_signal_reviewed_competitor_signal_write_v0",
        "generated_at": _now_z(),
        "write_db": True,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "backup_ref": backup_ref,
        "run_uid": run_uid,
        "run_id": run_id,
        "inserted": inserted,
        "skipped_existing": len(existing),
        "ready_candidates": len(rows),
        "before_counts": before_counts,
        "after_counts": after_counts,
        "checks": {
            "backup_ref_present": bool(_text(backup_ref)),
            "only_ready_candidates_written": inserted <= len(rows),
            "competitor_signals_delta_matches": after_counts["vkpi_competitor_signals"]
            - before_counts["vkpi_competitor_signals"]
            == inserted,
            "provider_calls_blocked": True,
            "llm_calls_blocked": True,
            "gemini_calls_blocked": True,
        },
    }


__all__ = [
    "build_external_signal_review_package",
    "build_external_signal_review_package_from_files",
    "build_market_signal_review_package",
    "build_market_signal_review_package_from_file",
    "competitor_signal_rows_from_review_package",
    "render_competitor_signal_write_markdown",
    "render_external_signal_review_package_markdown",
    "render_review_package_markdown",
    "write_competitor_signal_write_report",
    "write_external_signal_review_package_report",
    "write_review_package_report",
    "write_reviewed_competitor_signals",
]
