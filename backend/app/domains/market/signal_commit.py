"""Pure helpers for controlled market-signal persistence results."""
from __future__ import annotations

from typing import Any

from app.domains.market.signal_review_package import competitor_signal_rows_from_review_package


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def validate_competitor_signal_write_request(package: dict[str, Any], *, backup_ref: str) -> list[dict[str, Any]]:
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
    return rows


def build_competitor_signal_run_summary(
    package: dict[str, Any],
    *,
    backup_ref: str,
    ready_count: int,
    insert_count: int,
    skipped_existing: int,
) -> dict[str, Any]:
    return {
        "source_package_mode": package.get("mode"),
        "source_generated_at": package.get("generated_at"),
        "ready_candidates": ready_count,
        "insert_candidates": insert_count,
        "skipped_existing": skipped_existing,
        "backup_ref": backup_ref,
        "provider_calls": False,
        "llm_calls": False,
        "gemini_calls": False,
    }


def build_competitor_signal_write_result(
    *,
    generated_at: str,
    backup_ref: str,
    run_uid: str,
    run_id: int,
    inserted: int,
    skipped_existing: int,
    ready_candidates: int,
    before_counts: dict[str, int],
    after_counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "mode": "market_signal_reviewed_competitor_signal_write_v0",
        "generated_at": generated_at,
        "write_db": True,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "backup_ref": backup_ref,
        "run_uid": run_uid,
        "run_id": run_id,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "ready_candidates": ready_candidates,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "checks": {
            "backup_ref_present": bool(_text(backup_ref)),
            "only_ready_candidates_written": inserted <= ready_candidates,
            "competitor_signals_delta_matches": after_counts["vkpi_competitor_signals"]
            - before_counts["vkpi_competitor_signals"]
            == inserted,
            "provider_calls_blocked": True,
            "llm_calls_blocked": True,
            "gemini_calls_blocked": True,
        },
    }
