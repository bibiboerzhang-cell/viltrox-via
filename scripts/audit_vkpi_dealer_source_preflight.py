#!/usr/bin/env python3
"""Capture a bounded, read-only technical preflight for all Dealer sources."""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

from stdout_utils import out


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.domains.commerce.dealer_source_preflight import (  # noqa: E402
    USER_AGENT,
    audit_one_source,
)
from app.domains.events.us_coverage_registry import dealer_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    output = args.output or (
        ROOT / "runtime" / "ops" / f"dealer-source-technical-preflight-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    registry = dealer_registry()
    sources = list(registry.get("dealer_discovery_sources") or [])

    def run(source):
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(max(1.0, args.timeout)),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"},
            max_redirects=5,
        ) as client:
            return audit_one_source(source, client)

    rows = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 12))) as pool:
        futures = {pool.submit(run, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append(
                    {
                        "source_registry_id": source.get("id"),
                        "technical_status": "audit_worker_failed",
                        "error": f"{exc.__class__.__name__}: {str(exc)[:240]}",
                        "source_activation_recommended": False,
                        "candidate_extraction_performed": False,
                        "business_rows_written": 0,
                        "claim_status": "descriptive_only",
                    }
                )
    rows.sort(key=lambda item: str(item.get("source_registry_id") or ""))
    status_counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("technical_status") or "unknown")
        status_counts[key] = status_counts.get(key, 0) + 1
    payload = {
        "generated_at": now.isoformat(),
        "registry_version": registry.get("registry_version"),
        "source_count": len(sources),
        "audited_count": len(rows),
        "status_counts": status_counts,
        "terms_legal_approval_count": 0,
        "source_activation_count": 0,
        "candidate_extraction_count": 0,
        "business_rows_written": 0,
        "claim_status": "descriptive_only",
        "truth_note": "Technical transport/robots/content snapshots are not legal approval, source activation, Dealer entities, authorization, inventory, sales, ROI, or local impact.",
        "sources": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    out(output)
    out(json.dumps(status_counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
