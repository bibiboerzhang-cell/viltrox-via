#!/usr/bin/env python3
"""Backfill vkpi_llm_calls.cost_micro_usd from stored token counts (zero-cost metering).

Why this exists
---------------
Migration 203 added cost_micro_usd and tried to backfill old rows from cost_cents
(SET cost_micro_usd = cost_cents * 10000 WHERE cost_cents <> 0). But cost_cents was
structurally 0 on every historical row: the old _estimate_cost_cents used integer
division (tokens * cents_per_million // 1_000_000) which truncates any sub-cent or
even few-thousand-token call to 0. So the 203 UPDATE matched 0 rows and the whole
ledger reads $0 of spend, defeating the monthly budget gate that sums cost_micro_usd.

This one-off re-derives cost from input_tokens / output_tokens using the live
_estimate_cost_micro_usd(provider, in, out) -- the exact same function the write
path now uses for new calls -- for rows where cost_micro_usd is still 0, tokens are
present, and the provider is canonical (openai / google / anthropic, i.e. has a
PROVIDER_CONFIG price row). rule_v0 / internal_ml rows have no tokens and no price,
so they are correctly skipped and stay at 0.

Safety
------
- Pure metering metadata. NEVER touches viltrox_fit_score (red line). Only writes
  cost_micro_usd and the derived cost_cents on vkpi_llm_calls.
- Default is DRY-RUN: prints how many rows would be backfilled and the total micro.
  Nothing is written without --commit.
- --commit writes a JSON backup of the affected rows first, then UPDATEs in one tx.
- Idempotent: re-running only touches rows still at cost_micro_usd = 0.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.connection import close_db_runtime, get_conn
from app.platform.llm_gateway import (
    PROVIDER_CONFIG,
    _estimate_cost_micro_usd,
    _micro_usd_to_cents,
)

# Canonical providers = those with a real price row. rule_v0 / internal_ml excluded.
CANONICAL_PROVIDERS = tuple(sorted(PROVIDER_CONFIG.keys()))


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def plan_backfill(conn: Any) -> list[dict[str, Any]]:
    """Return per-row backfill plan for canonical, zero-micro rows that have tokens."""
    rows = conn.execute(
        """
        SELECT id, call_uid, provider, input_tokens, output_tokens, cost_cents, cost_micro_usd
          FROM vkpi_llm_calls
         WHERE COALESCE(cost_micro_usd, 0) = 0
           AND (COALESCE(input_tokens, 0) > 0 OR COALESCE(output_tokens, 0) > 0)
        """
    ).fetchall()

    planned: list[dict[str, Any]] = []
    for raw in rows:
        r = _row_to_dict(raw)
        provider = str(r.get("provider") or "").strip().lower()
        if provider not in PROVIDER_CONFIG:
            continue
        in_tok = int(r.get("input_tokens") or 0)
        out_tok = int(r.get("output_tokens") or 0)
        micro = _estimate_cost_micro_usd(provider, in_tok, out_tok)
        if micro <= 0:
            # Honest sub-tenth-cent rounding to 0 -> nothing to backfill, leave as-is.
            continue
        planned.append(
            {
                "id": r.get("id"),
                "call_uid": r.get("call_uid"),
                "provider": provider,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "old_cost_cents": int(r.get("cost_cents") or 0),
                "old_cost_micro_usd": int(r.get("cost_micro_usd") or 0),
                "new_cost_micro_usd": int(micro),
                "new_cost_cents": int(_micro_usd_to_cents(micro)),
            }
        )
    return planned


def summarize(planned: list[dict[str, Any]]) -> dict[str, Any]:
    by_provider: dict[str, dict[str, int]] = {}
    total_micro = 0
    for p in planned:
        total_micro += int(p["new_cost_micro_usd"])
        slot = by_provider.setdefault(p["provider"], {"rows": 0, "micro": 0})
        slot["rows"] += 1
        slot["micro"] += int(p["new_cost_micro_usd"])
    return {
        "rows": len(planned),
        "total_micro_usd": total_micro,
        "total_usd": round(total_micro / 1_000_000, 6),
        "by_provider": by_provider,
        "canonical_providers": list(CANONICAL_PROVIDERS),
    }


def write_backup(planned: list[dict[str, Any]], backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"vkpi-llm-cost-micro-backfill-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(planned, ensure_ascii=False, indent=2, default=str))
    return path


def apply_backfill(conn: Any, planned: list[dict[str, Any]]) -> int:
    written = 0
    for p in planned:
        # Guard in WHERE keeps it idempotent and avoids stomping a concurrently-written value.
        cur = conn.execute(
            """
            UPDATE vkpi_llm_calls
               SET cost_micro_usd = ?, cost_cents = ?
             WHERE id = ? AND COALESCE(cost_micro_usd, 0) = 0
            """,
            (int(p["new_cost_micro_usd"]), int(p["new_cost_cents"]), p["id"]),
        )
        rc = getattr(cur, "rowcount", None)
        written += rc if isinstance(rc, int) and rc >= 0 else 1
    conn.commit()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", default="runtime/vkpi-llm-cost-backfill")
    parser.add_argument("--commit", action="store_true", help="Actually write (default is dry-run).")
    parser.add_argument("--show", type=int, default=5, help="How many sample rows to print.")
    args = parser.parse_args()

    conn = get_conn()
    planned = plan_backfill(conn)
    summary = summarize(planned)

    print("=== vkpi_llm_calls cost_micro_usd backfill ===")
    print(f"mode: {'COMMIT' if args.commit else 'DRY-RUN'}")
    print(f"would backfill rows: {summary['rows']}")
    print(f"total micro_usd:     {summary['total_micro_usd']}  (~${summary['total_usd']})")
    print(f"canonical providers: {summary['canonical_providers']}")
    for prov, slot in sorted(summary["by_provider"].items()):
        print(f"  {prov:10s} rows={slot['rows']:5d}  micro={slot['micro']}  (~${round(slot['micro']/1_000_000,4)})")
    for p in planned[: max(0, args.show)]:
        print(
            f"  sample {p['call_uid']}: {p['provider']} in={p['input_tokens']} out={p['output_tokens']} "
            f"-> micro={p['new_cost_micro_usd']} cents={p['new_cost_cents']}"
        )

    backup_path = None
    if args.commit and planned:
        backup_path = str(write_backup(planned, Path(args.backup_dir)))
        written = apply_backfill(conn, planned)
        print(f"backup written: {backup_path}")
        print(f"rows written:   {written}")
    elif args.commit:
        print("nothing to commit (0 rows planned)")
    else:
        print("dry-run only; pass --commit to write (a JSON backup is taken first).")

    print(
        json.dumps(
            {
                "mode": "commit" if args.commit else "dry_run",
                **summary,
                "backup_path": backup_path,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
