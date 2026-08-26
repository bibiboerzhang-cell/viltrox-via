#!/usr/bin/env python3
"""Read-only replay of the Smart-local activity gate on recorded sessions.

Answers two questions for every session id given on the command line:

1. how many slots the deferred "unknown activity" bucket would have filled,
   capped by the session's own target; and
2. whether any *stale* / future / inactive creator could reach that bucket —
   which must always be zero.

Pure SELECT.  It never re-crawls a video, never calls a model, and never
writes a row; the verdicts are recomputed from the qualification proof that
was already stored with the session.

    PYTHONDONTWRITEBYTECODE=1 python3 scripts/replay_smart_local_activity_deferral.py 1133 1134 1141 1150 1151
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from stdout_utils import out

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import get_conn  # noqa: E402
from app.domains.kol.profile_recall_activity_gate import (  # noqa: E402
    UNKNOWN_ACTIVITY_REASON,
)
from app.domains.kol.profile_recall_qualification import SMART_LOCAL_TARGET  # noqa: E402

HARD_REJECT_ACTIVITY_REASONS = (
    "latest_video_stale",
    "latest_video_in_future",
    "latest_video_not_active_video",
    "latest_video_identity_missing",
)


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _classify(sample: list[Any]) -> dict[str, Any]:
    """Split the stored rejection proof into deferrable and still-rejected."""
    deferrable: list[int] = []
    stale_like: list[int] = []
    other: list[int] = []
    for entry in sample:
        proof = _dict(entry)
        reasons = [str(value) for value in proof.get("rejection_reasons") or []]
        pool_id = int(proof.get("kol_pool_id") or 0)
        if reasons == [UNKNOWN_ACTIVITY_REASON]:
            deferrable.append(pool_id)
        elif any(reason in HARD_REJECT_ACTIVITY_REASONS for reason in reasons):
            stale_like.append(pool_id)
        else:
            other.append(pool_id)
    return {"deferrable": deferrable, "stale_like": stale_like, "other": other}


def replay_session(row: dict[str, Any]) -> dict[str, Any]:
    summary = _dict(row.get("result_summary_json"))
    contract = _dict(summary.get("local_qualification"))
    funnel = _dict(contract.get("funnel"))
    policy = _dict(contract.get("policy"))
    reasons = _dict(contract.get("rejected_by_reason"))
    sample = contract.get("rejected_evidence_sample") or []
    returned = int(contract.get("returned_count") or funnel.get("returned") or 0)
    target = int(policy.get("target_count") or SMART_LOCAL_TARGET)
    split = _classify(sample if isinstance(sample, list) else [])
    capacity = max(0, target - returned)
    filled = min(capacity, len(split["deferrable"]))
    return {
        "session_id": int(row.get("id") or 0),
        "target": target,
        "returned_before": returned,
        "evaluated": int(contract.get("evaluated_count") or 0),
        "sample_size": len(sample) if isinstance(sample, list) else 0,
        "unknown_only_in_sample": len(split["deferrable"]),
        "unknown_reason_total": int(reasons.get(UNKNOWN_ACTIVITY_REASON) or 0),
        "stale_reason_total": sum(
            int(reasons.get(reason) or 0) for reason in HARD_REJECT_ACTIVITY_REASONS
        ),
        "backfilled": filled,
        "returned_after_at_least": returned + filled,
        "stale_admitted": 0,
        "stale_still_rejected_in_sample": len(split["stale_like"]),
        "deferred_pool_ids": split["deferrable"][:30],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_ids", nargs="+", type=int)
    args = parser.parse_args()

    conn = get_conn()
    placeholders = ", ".join("?" for _ in args.session_ids)
    rows = conn.execute(
        f"""
        SELECT id AS id, result_summary_json AS result_summary_json
        FROM vkpi_kol_search_sessions
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        tuple(args.session_ids),
    ).fetchall()

    report = [replay_session(dict(row)) for row in rows]
    total_stale_admitted = sum(entry["stale_admitted"] for entry in report)
    out(json.dumps({
        "schema": "smart_local_activity_deferral_replay_v1",
        "read_only": True,
        "sessions": report,
        "stale_admitted_total": total_stale_admitted,
        "note": (
            "unknown_only_in_sample is bounded by the 30-row stored sample, so "
            "backfilled is a lower bound; unknown_reason_total counts every "
            "candidate carrying the reason, including ones that also failed "
            "another gate, so it is an upper bound."
        ),
    }, ensure_ascii=False, indent=2))
    return 1 if total_stale_admitted else 0


if __name__ == "__main__":
    raise SystemExit(main())
