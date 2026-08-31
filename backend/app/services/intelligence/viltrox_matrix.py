"""
services/intelligence/viltrox_matrix.py — official roster-backed admin overview for legacy Viltrox zone
"""
from __future__ import annotations

from typing import Any

from app.db.repositories.viltrox_matrix import (
    get_latest_viltrox_scan_bundle,
    save_viltrox_scan_snapshot,
    sync_viltrox_official_accounts,
)
from app.services.deepsight.parallel_scan import scan_accounts_concurrently


def _scan_post(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(row.get("title") or ""),
        "url": str(row.get("post_url") or ""),
        "thumbnail": str(row.get("thumbnail_url") or ""),
        "views": int(row.get("views") or 0),
        "likes": int(row.get("likes") or 0),
        "comments": int(row.get("comments") or 0),
        "shares": int(row.get("shares") or 0),
        "published": str(row.get("published_at") or ""),
        "type": str(row.get("content_type") or ""),
    }


def _posts_by_account(posts: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in posts:
        grouped.setdefault(int(row.get("account_id") or 0), []).append(_scan_post(row))
    return grouped


def _scan_account_result(
    account: dict[str, Any],
    scan_row: dict[str, Any],
    account_posts: list[dict[str, Any]],
) -> dict[str, Any]:
    account_id = int(account.get("id") or 0)
    return {
        "account": {
            "id": account_id,
            "name": str(account.get("name") or ""),
            "platform": str(account.get("platform") or ""),
            "handle": str(account.get("handle") or ""),
        },
        "data": {
            "overview": {
                "total_posts": int(scan_row.get("total_posts") or len(account_posts)),
                "total_views": int(scan_row.get("total_views") or 0),
                "total_likes": int(scan_row.get("total_likes") or 0),
                "total_comments": int(scan_row.get("total_comments") or 0),
            },
            "posts": account_posts,
            "error": str(scan_row.get("error_message") or "") or None,
        },
        "duration_sec": float(scan_row.get("duration_sec") or 0.0),
    }


def _scan_results(
    accounts: list[dict[str, Any]],
    scan_accounts: list[dict[str, Any]],
    posts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped_posts = _posts_by_account(posts)
    scan_rows = {int(row.get("account_id") or 0): row for row in scan_accounts}
    # Preserve the historical eager account-id normalization before projection.
    _account_lookup = {int(account.get("id") or 0): account for account in accounts}
    return [
        _scan_account_result(
            account,
            scan_rows.get(int(account.get("id") or 0)) or {},
            grouped_posts.get(int(account.get("id") or 0), []),
        )
        for account in accounts
    ]


def _scan_aggregate(run: dict[str, Any]) -> dict[str, Any]:
    aggregate = dict(run.get("aggregate") or {})
    if aggregate:
        return aggregate
    return {
        "total_posts": int(run.get("total_posts") or 0),
        "total_views": int(run.get("total_views") or 0),
        "total_likes": int(run.get("total_likes") or 0),
        "total_comments": int(run.get("total_comments") or 0),
    }


def _build_scan_payload(bundle: dict[str, Any]) -> dict[str, Any] | None:
    run = bundle.get("run") or {}
    if not run:
        return None
    accounts = bundle.get("accounts") or []
    results = _scan_results(
        accounts,
        bundle.get("scan_accounts") or [],
        bundle.get("posts") or [],
    )
    return {
        "run_id": int(run.get("id") or 0),
        "run_key": str(run.get("run_key") or ""),
        "status": str(run.get("status") or "completed"),
        "timestamp": str(run.get("completed_at") or run.get("started_at") or ""),
        "results": results,
        "scanned": int(run.get("scanned_accounts") or len(results)),
        "total": int(run.get("total_accounts") or len(results)),
        "aggregate": _scan_aggregate(run),
    }


def build_viltrox_overview() -> dict[str, Any]:
    accounts = sync_viltrox_official_accounts(reset=False)
    bundle = get_latest_viltrox_scan_bundle()
    scan = _build_scan_payload(bundle)
    enriched_accounts: list[dict[str, Any]] = []
    latest_rows = {
        int(row.get("account_id") or 0): row
        for row in (bundle.get("scan_accounts") or [])
    }
    for account in accounts:
        account_id = int(account.get("id") or 0)
        latest = latest_rows.get(account_id) or {}
        enriched_accounts.append(
            {
                **account,
                "latest_scan_status": "error" if str(latest.get("error_message") or "").strip() else ("completed" if scan else "not_scanned"),
                "latest_error": str(latest.get("error_message") or ""),
                "latest_stats": {
                    "total_posts": int(latest.get("total_posts") or 0),
                    "total_views": int(latest.get("total_views") or 0),
                    "total_likes": int(latest.get("total_likes") or 0),
                    "total_comments": int(latest.get("total_comments") or 0),
                },
            }
        )
    return {
        "accounts": enriched_accounts,
        "scan": scan,
    }


async def scan_viltrox_official_matrix_now(
    *,
    max_posts_per_account: int = 120,
    concurrency: int = 4,
) -> dict[str, Any]:
    sync_viltrox_official_accounts(reset=False)
    raw = await scan_accounts_concurrently(
        max_posts_per_account=max(1, int(max_posts_per_account)),
        concurrency=max(1, int(concurrency)),
    )
    save_viltrox_scan_snapshot(raw)
    return build_viltrox_overview()


def reset_viltrox_official_roster() -> dict[str, Any]:
    sync_viltrox_official_accounts(reset=True)
    return build_viltrox_overview()


__all__ = [
    "build_viltrox_overview",
    "reset_viltrox_official_roster",
    "scan_viltrox_official_matrix_now",
]
