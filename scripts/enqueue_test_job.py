#!/usr/bin/env python3
"""Enqueue one local mock apify_jobs task for worker smoke validation."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import psycopg  # noqa: E402
from app.core.config import DB_RUNTIME_URL  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-type", default="video")
    parser.add_argument("--target-type", default="video")
    parser.add_argument("--target-id", default="b3-smoke-video")
    parser.add_argument("--triggered-by-user-id", type=int, default=None)
    args = parser.parse_args()
    if not DB_RUNTIME_URL:
        raise SystemExit("DATABASE_URL is required")
    payload = {
        "target_type": args.target_type,
        "target_id": args.target_id,
        "triggered_by_user_id": args.triggered_by_user_id,
    }
    with psycopg.connect(DB_RUNTIME_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
                VALUES (%s, %s::jsonb, 'queued', NOW(), NOW())
                RETURNING id
                """,
                (args.job_type, json.dumps(payload, ensure_ascii=False)),
            )
            job_id = cur.fetchone()[0]
        conn.commit()
    stdout_out(json.dumps({"job_id": job_id, "status": "queued", "payload": payload}, ensure_ascii=False))


if __name__ == "__main__":
    main()
