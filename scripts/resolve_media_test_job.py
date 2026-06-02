#!/usr/bin/env python3
"""Resolve one video evidence media URL without downloading or analyzing it."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import psycopg  # noqa: E402
from app.core.config import DB_RUNTIME_URL  # noqa: E402
from app.workers.apify_jobs_worker import _load_video_evidence, _resolve_video_media  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-id", required=True, help="vkpi_kol_video_evidence.id")
    args = parser.parse_args()
    if not DB_RUNTIME_URL:
        raise SystemExit("DATABASE_URL is required")

    with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
        evidence = _load_video_evidence(conn, args.target_id)
        resolved = _resolve_video_media(evidence)

    print(
        json.dumps(
            {
                "target_id": str(args.target_id),
                "source_url_host": resolved.get("source_url_host"),
                "platform": resolved.get("platform"),
                "apify_video_url_found": bool(resolved.get("direct_video_url")),
                "direct_video_url_host": resolved.get("direct_video_url_host"),
                "status": resolved.get("status"),
                "reason": resolved.get("reason"),
                "scraped_ok": bool(resolved.get("scraped_ok")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
