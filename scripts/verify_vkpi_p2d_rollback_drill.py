#!/usr/bin/env python3
"""Verify or execute the V-KPI P2D rollback drill for a committed legacy batch."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains.legacy_import.legacy_kol_commit import (  # noqa: E402
    commit_kol_pool_batch,
    preview_kol_pool_rollback,
    rollback_kol_pool_commit,
)


DEFAULT_BATCH_UID = "vkpi_20260519033921_b36c6f28ec8d"


def _batch_id(batch_uid: str) -> int:
    row = get_conn().execute("SELECT id FROM vkpi_legacy_import_batches WHERE batch_uid=?", (batch_uid,)).fetchone()
    if not row:
        raise ValueError(f"batch not found: {batch_uid}")
    return int(row["id"])


def _count(sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(get_conn().execute(sql, params).fetchone()["n"])


def _state(batch_uid: str) -> dict[str, Any]:
    conn = get_conn()
    batch = conn.execute(
        "SELECT id,status,committed_rows,rolled_back_rows,rollback_until FROM vkpi_legacy_import_batches WHERE batch_uid=?",
        (batch_uid,),
    ).fetchone()
    if not batch:
        raise ValueError(f"batch not found: {batch_uid}")
    batch_id = int(batch["id"])
    ref_rows = conn.execute(
        """
        SELECT commit_attempt, commit_action, rollback_status, COUNT(*) AS n
        FROM vkpi_legacy_import_committed_refs
        WHERE import_batch_id=?
        GROUP BY commit_attempt, commit_action, rollback_status
        ORDER BY commit_attempt, commit_action, rollback_status
        """,
        (batch_id,),
    ).fetchall()
    refs = {
        f"attempt_{row['commit_attempt']}.{row['commit_action']}.{row['rollback_status']}": int(row["n"])
        for row in ref_rows
    }
    return {
        "batch_status": batch["status"],
        "committed_rows": int(batch["committed_rows"] or 0),
        "rolled_back_rows": int(batch["rolled_back_rows"] or 0),
        "rollback_until": batch["rollback_until"] or "",
        "pool_total": _count("SELECT COUNT(*) AS n FROM vkpi_kol_pool"),
        "pool_legacy_source": _count("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE source_type='legacy_excel_p2d'"),
        "pool_imported": _count("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE source_type='legacy_excel_p2d' AND sync_status='imported'"),
        "pool_needs_human_review": _count("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE source_type='legacy_excel_p2d' AND sync_status='needs_human_review'"),
        "refs": refs,
    }


def _verify_update_restore(batch_uid: str) -> dict[str, int]:
    conn = get_conn()
    batch_id = _batch_id(batch_uid)
    refs = conn.execute(
        """
        SELECT target_id, previous_snapshot_json
        FROM vkpi_legacy_import_committed_refs
        WHERE import_batch_id=? AND commit_action='update' AND rollback_status='rolled_back'
        ORDER BY commit_attempt DESC
        """,
        (batch_id,),
    ).fetchall()
    checked = 0
    mismatches = 0
    for ref in refs:
        previous = json.loads(ref["previous_snapshot_json"] or "{}")
        row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(ref["target_id"]),)).fetchone()
        if not row:
            mismatches += 1
            continue
        current = dict(row.items()) if hasattr(row, "items") else dict(row)
        for key, expected in previous.items():
            if key == "id":
                continue
            actual = current.get(key)
            if str(actual if actual is not None else "") != str(expected if expected is not None else ""):
                mismatches += 1
                break
        checked += 1
    return {"update_restore_checked": checked, "update_restore_mismatches": mismatches}


def _print_state(prefix: str, state: dict[str, Any]) -> None:
    print(f"{prefix}.batch_status={state['batch_status']}")
    print(f"{prefix}.committed_rows={state['committed_rows']}")
    print(f"{prefix}.rolled_back_rows={state['rolled_back_rows']}")
    print(f"{prefix}.rollback_until={state['rollback_until']}")
    print(f"{prefix}.pool_total={state['pool_total']}")
    print(f"{prefix}.pool_legacy_source={state['pool_legacy_source']}")
    print(f"{prefix}.pool_imported={state['pool_imported']}")
    print(f"{prefix}.pool_needs_human_review={state['pool_needs_human_review']}")
    for key, value in sorted(state["refs"].items()):
        print(f"{prefix}.refs.{key}={value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify or execute the V-KPI P2D rollback drill.")
    parser.add_argument("--batch-uid", default=DEFAULT_BATCH_UID)
    parser.add_argument("--execute", action="store_true", help="Run rollback and recommit. Default only inspects state.")
    parser.add_argument("--commit", action="store_true", help="Required with --execute to mutate data.")
    parser.add_argument("--force-rollback", action="store_true", help="Force rollback if the rollback window has expired.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        _print_state("before", _state(args.batch_uid))
        preview = preview_kol_pool_rollback(args.batch_uid, sample_limit=3, force=bool(args.force_rollback))
        print(f"preview.rollback_refs_count={preview['rollback_refs_count']}")
        print(f"preview.insert_refs={preview['insert_refs']}")
        print(f"preview.update_refs={preview['update_refs']}")
        print(f"preview.rollback_allowed={str(bool(preview['rollback_allowed'])).lower()}")
        print(f"preview.rollback_window_reason={preview['rollback_window_reason']}")
        if not args.execute:
            return 0
        if not args.commit:
            raise RuntimeError("--execute requires --commit")

        rolled_back = rollback_kol_pool_commit(args.batch_uid, force=bool(args.force_rollback), sample_limit=3)
        print(f"rollback.rolled_back_refs={rolled_back['rolled_back_refs']}")
        _print_state("after_rollback", _state(args.batch_uid))
        restore = _verify_update_restore(args.batch_uid)
        print(f"after_rollback.update_restore_checked={restore['update_restore_checked']}")
        print(f"after_rollback.update_restore_mismatches={restore['update_restore_mismatches']}")

        committed = commit_kol_pool_batch(args.batch_uid, sample_limit=3)
        print(f"recommit.committed_refs_count={committed['committed_refs_count']}")
        print(f"recommit.committed_refs_total={committed.get('committed_refs_total', 0)}")
        _print_state("final", _state(args.batch_uid))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
