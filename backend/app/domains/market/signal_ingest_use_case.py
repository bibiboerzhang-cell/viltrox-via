"""Controlled DB writer for reviewed market signal packages.

Package construction belongs to ``app.domains.market``. This service keeps the
backup-gated persistence boundary close to the database layer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.market.signal_write_package import (
    TARGET_TABLES,
    build_external_market_signal_write_package,
    build_market_signal_write_package,
    build_market_signal_write_package_from_file,
)


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if parsed == parsed else 0.0
    except Exception:
        return 0.0


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _count_rows(conn: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TARGET_TABLES + ["vkpi_competitor_signals"]:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        counts[table] = int(row["n"] or 0) if row else 0
    return counts


def _fetch_existing_run_id(conn: Any, run_uid: str) -> int | None:
    row = conn.execute("SELECT id FROM vkpi_market_scan_runs WHERE run_uid=?", (run_uid,)).fetchone()
    if not row:
        return None
    return int(row["id"])


def write_market_signal_package(package: dict[str, Any], *, backup_ref: str) -> dict[str, Any]:
    """Persist raw market scan rows from a reviewed package.

    This function does not promote rows into ``vkpi_competitor_signals``. That
    remains a later review/classifier step.
    """

    if package.get("write_db") is not False:
        raise ValueError("only no-write packages can be promoted by this controlled writer")
    if not package.get("passed"):
        raise ValueError("market signal package checks did not pass")
    if not str(backup_ref or "").strip():
        raise ValueError("backup_ref is required before writing market signal package")

    scan = package.get("scan_run") if isinstance(package.get("scan_run"), dict) else {}
    run_uid = _text(scan.get("run_uid"))
    if not run_uid:
        raise ValueError("scan_run.run_uid is required")

    conn = get_conn()
    before_counts = _count_rows(conn)
    if _fetch_existing_run_id(conn, run_uid) is not None:
        raise ValueError(f"market scan run already exists: {run_uid}")

    now = _now_z()
    inserted_source_ids: dict[str, int] = {}
    try:
        cursor = conn.execute(
            """
            INSERT INTO vkpi_market_scan_runs
                (run_uid, scan_type, platforms_json, keywords_json, status,
                 provider_status, summary_json, error_message, triggered_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                run_uid,
                _text(scan.get("scan_type"), "manual"),
                _json(scan.get("platforms_json") or []),
                _json(scan.get("keywords_json") or []),
                _text(scan.get("status"), "completed"),
                _text(scan.get("provider_status"), "configured"),
                _json({**(scan.get("summary_json") or {}), "backup_ref": backup_ref}),
                _text(scan.get("error_message")),
                now,
                _text(scan.get("completed_at"), now),
            ),
        )
        run_row = cursor.fetchone()
        if not run_row:
            raise RuntimeError("failed to insert vkpi_market_scan_runs row")
        run_id = int(run_row["id"])

        for source in package.get("sources") or []:
            if not isinstance(source, dict):
                continue
            temp_uid = _text(source.get("source_temp_uid"))
            cursor = conn.execute(
                """
                INSERT INTO vkpi_market_sources
                    (run_id, source_type, platform, source_ref, source_url, title, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    run_id,
                    _text(source.get("source_type"), "reddit_post"),
                    _text(source.get("platform"), "reddit"),
                    _text(source.get("source_ref")),
                    _text(source.get("source_url")),
                    _text(source.get("title"))[:500],
                    _json(source.get("metadata_json") or {}),
                    now,
                ),
            )
            row = cursor.fetchone()
            if row and temp_uid:
                inserted_source_ids[temp_uid] = int(row["id"])

        mention_count = 0
        for mention in package.get("mentions") or []:
            if not isinstance(mention, dict):
                continue
            source_temp_uid = _text(mention.get("source_temp_uid"))
            conn.execute(
                """
                INSERT INTO vkpi_market_mentions
                    (run_id, source_id, platform, handle, mention_text, product_sku,
                     competitor_product, sentiment, score, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    inserted_source_ids.get(source_temp_uid),
                    _text(mention.get("platform"), "reddit"),
                    _text(mention.get("handle")),
                    _text(mention.get("mention_text"))[:1000],
                    _text(mention.get("product_sku")),
                    _text(mention.get("competitor_product")),
                    _text(mention.get("sentiment")),
                    _safe_float(mention.get("score")),
                    _json(mention.get("metadata_json") or {}),
                    now,
                ),
            )
            mention_count += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    after_counts = _count_rows(conn)
    return {
        "mode": "market_signal_first_write_v0",
        "generated_at": _now_z(),
        "backup_ref": backup_ref,
        "run_uid": run_uid,
        "run_id": run_id,
        "write_db": True,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "inserted": {
            "vkpi_market_scan_runs": 1,
            "vkpi_market_sources": len(inserted_source_ids),
            "vkpi_market_mentions": mention_count,
            "vkpi_competitor_signals": 0,
        },
        "counts_before": before_counts,
        "counts_after": after_counts,
        "checks": {
            "backup_ref_recorded": bool(backup_ref),
            "scan_run_inserted": after_counts["vkpi_market_scan_runs"] == before_counts["vkpi_market_scan_runs"] + 1,
            "sources_inserted": after_counts["vkpi_market_sources"] == before_counts["vkpi_market_sources"] + len(inserted_source_ids),
            "mentions_inserted": after_counts["vkpi_market_mentions"] == before_counts["vkpi_market_mentions"] + mention_count,
            "competitor_signals_unchanged": after_counts["vkpi_competitor_signals"] == before_counts["vkpi_competitor_signals"],
        },
    }


__all__ = [
    "build_external_market_signal_write_package",
    "build_market_signal_write_package",
    "build_market_signal_write_package_from_file",
    "write_market_signal_package",
]
