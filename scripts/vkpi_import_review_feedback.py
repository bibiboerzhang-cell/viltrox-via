"""Import filled P13 recommendation review CSV into vkpi_recommendation_feedback."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402


ACTION_TO_FEEDBACK = {
    "accept": "shortlist",
    "reject": "reject",
    "snooze": "snooze",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _recommendation_exists(recommendation_id: int) -> bool:
    row = get_conn().execute(
        "SELECT id FROM vkpi_kol_recommendations WHERE id = ?",
        (int(recommendation_id),),
    ).fetchone()
    return bool(row)


def import_feedback(path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    rows = list(csv.DictReader(path.open("r", encoding="utf-8-sig")))
    skipped = 0
    errors: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    conn = get_conn()

    for index, row in enumerate(rows, start=2):
        rec_raw = _clean(row.get("recommendation_id"))
        action = _clean(row.get("action")).lower()
        reject_reason = _clean(row.get("reject_reason"))
        reviewer_name = _clean(row.get("reviewer_name"))
        if not action:
            skipped += 1
            continue
        if action not in ACTION_TO_FEEDBACK:
            errors.append({"line": index, "recommendation_id": rec_raw, "error": "action must be accept/reject/snooze"})
            continue
        if action == "reject" and not reject_reason:
            errors.append({"line": index, "recommendation_id": rec_raw, "error": "reject_reason is required for reject"})
            continue
        if not rec_raw.isdigit():
            errors.append({"line": index, "recommendation_id": rec_raw, "error": "recommendation_id must be an existing numeric vkpi_kol_recommendations.id"})
            continue
        recommendation_id = int(rec_raw)
        if not _recommendation_exists(recommendation_id):
            errors.append({"line": index, "recommendation_id": rec_raw, "error": "recommendation_id not found"})
            continue
        feedback_type = ACTION_TO_FEEDBACK[action]
        existing = conn.execute(
            """
            SELECT id
            FROM vkpi_recommendation_feedback
            WHERE recommendation_id = ? AND feedback_type = ?
            LIMIT 1
            """,
            (recommendation_id, feedback_type),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        note = reject_reason if action == "reject" else f"P13 CSV action: {action}"
        metadata = {
            "source": "p13_review_backlog_csv",
            "csv_action": action,
            "reviewer_name": reviewer_name,
            "suggested_sku": _clean(row.get("suggested_sku")),
            "kol_handle": _clean(row.get("kol_handle")),
            "platform": _clean(row.get("platform")),
            "top_evidence_summary": _clean(row.get("top_evidence_summary")),
            "reject_reason": reject_reason,
        }
        prepared.append(
            {
                "line": index,
                "recommendation_id": recommendation_id,
                "feedback_type": feedback_type,
                "note": note,
                "metadata": metadata,
            }
        )

    imported = 0
    commit_blocked = bool(errors) and not dry_run
    if not dry_run and not errors:
        try:
            for item in prepared:
                metadata = item["metadata"]
                conn.execute(
                    """
                    INSERT INTO vkpi_recommendation_feedback
                        (recommendation_id, feedback_type, note, created_by_staff_id, created_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["recommendation_id"],
                        item["feedback_type"],
                        item["note"],
                        None,
                        _utcnow(),
                        _json(metadata),
                    ),
                )
                imported += 1
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    feedback_type_counts: dict[str, int] = {}
    for item in prepared:
        key = str(item.get("feedback_type") or "")
        feedback_type_counts[key] = feedback_type_counts.get(key, 0) + 1

    return {
        "dry_run": dry_run,
        "path": str(path),
        "rows": len(rows),
        "prepared": len(prepared),
        "imported": imported if not dry_run else 0,
        "skipped": skipped,
        "commit_blocked": commit_blocked,
        "errors": errors[:20],
        "error_count": len(errors),
        "feedback_type_counts": feedback_type_counts,
        "accepted_actions": sorted(ACTION_TO_FEEDBACK),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--commit", action="store_true", help="Write validated feedback rows.")
    args = parser.parse_args()
    try:
        result = import_feedback(Path(args.csv_path), dry_run=not args.commit)
        stdout_out(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["error_count"] == 0 else 1
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
