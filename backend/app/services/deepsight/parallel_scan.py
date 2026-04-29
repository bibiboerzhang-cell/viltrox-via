from __future__ import annotations

import asyncio
from typing import Any

from app.services.deepsight.constants import OFFICIAL_MATRIX

try:
    from app.api.routers.account_scanner import SCANNERS as ACCOUNT_SCANNERS
except Exception:
    ACCOUNT_SCANNERS = {}


async def scan_accounts_concurrently(accounts: list[dict] | None = None, max_posts_per_account: int = 60, concurrency: int = 4) -> dict[str, Any]:
    accounts = accounts or OFFICIAL_MATRIX
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    async def _scan_one(account: dict) -> None:
        platform = str(account.get("platform") or "").lower()
        handle = str(account.get("handle") or "")
        name = str(account.get("name") or handle)
        scanner = ACCOUNT_SCANNERS.get(platform)
        async with semaphore:
            if not scanner:
                results.append({"platform": platform, "handle": handle, "account_name": name, "posts": [], "stats": {"total_posts": 0}, "error": "scanner_not_available"})
                return
            try:
                res = await scanner(handle, max_posts_per_account)
                res["account_name"] = name
                results.append(res)
            except Exception as exc:  # pragma: no cover
                results.append({"platform": platform, "handle": handle, "account_name": name, "posts": [], "stats": {"total_posts": 0}, "error": str(exc)})

    await asyncio.gather(*[_scan_one(a) for a in accounts])
    aggregate = {
        "total_posts": sum(len(r.get("posts", [])) for r in results),
        "total_views": sum(r.get("stats", {}).get("total_views", 0) for r in results),
        "total_likes": sum(r.get("stats", {}).get("total_likes", 0) for r in results),
        "total_comments": sum(r.get("stats", {}).get("total_comments", 0) for r in results),
    }
    return {"scanned": len(results), "total": len(accounts), "aggregate": aggregate, "results": results}
