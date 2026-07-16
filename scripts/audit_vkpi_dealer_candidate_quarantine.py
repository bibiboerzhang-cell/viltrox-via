#!/usr/bin/env python3
"""Capture public Dealer pages into a read-only, non-activating quarantine."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from stdout_utils import out


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.domains.commerce.dealer_candidate_quarantine import (  # noqa: E402
    build_quarantine,
    eligible_preflight_sources,
)
from app.domains.commerce.dealer_source_preflight import USER_AGENT  # noqa: E402


DEFAULT_PREFLIGHT = ROOT / "runtime" / "ops" / "dealer-source-technical-preflight-20260715.json"
DEFAULT_REGISTRY = ROOT / "backend" / "app" / "domains" / "events" / "us_coverage_source_registry.json"


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value, raw


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch only preflight-reachable/robots-allowed Dealer pages and emit "
            "evidence-only address candidates. No database or source activation is available."
        )
    )
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    captured_at = now.isoformat().replace("+00:00", "Z")
    output = args.output or (
        ROOT
        / "runtime"
        / "ops"
        / f"dealer-candidate-quarantine-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    preflight, preflight_bytes = _load(args.preflight)
    registry, registry_bytes = _load(args.registry)
    eligible, _excluded = eligible_preflight_sources(preflight)

    captured: dict[str, dict[str, Any]] = {}

    def capture(url: str) -> dict[str, Any]:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(max(1.0, args.timeout)),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.2",
            },
            max_redirects=5,
        ) as client:
            response = client.get(url)
            return {
                "status_code": int(response.status_code),
                "final_url": str(response.url),
                "content_type": str(response.headers.get("content-type") or ""),
                "content": bytes(response.content or b""),
            }

    with ThreadPoolExecutor(max_workers=max(1, min(int(args.workers), 8))) as pool:
        futures = {
            pool.submit(capture, str(row["canonical_url"])): str(row["canonical_url"])
            for row in eligible
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                captured[url] = future.result()
            except Exception as exc:
                captured[url] = {"capture_error": exc}

    def replay(url: str) -> dict[str, Any]:
        value = captured[url]
        error = value.get("capture_error")
        if isinstance(error, BaseException):
            raise error
        return value

    payload = build_quarantine(
        preflight=preflight,
        registry=registry,
        captured_at=captured_at,
        fetch=replay,
        preflight_sha256=hashlib.sha256(preflight_bytes).hexdigest(),
        registry_sha256=hashlib.sha256(registry_bytes).hexdigest(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o600)
    out(output)
    out(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if not payload.get("blocked_source_calls") else 2


if __name__ == "__main__":
    raise SystemExit(main())
