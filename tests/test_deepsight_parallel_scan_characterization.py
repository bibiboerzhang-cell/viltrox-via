"""Behavior locks for the DeepSight parallel account-scan coordinator."""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.deepsight import parallel_scan  # noqa: E402
from app.services.intelligence import account_scan_service  # noqa: E402


def test_default_registry_comes_from_api_independent_scan_service() -> None:
    assert parallel_scan.ACCOUNT_SCANNERS is account_scan_service.SCANNERS


def test_public_signature_remains_stable() -> None:
    assert str(inspect.signature(parallel_scan.scan_accounts_concurrently)) == (
        "(accounts: 'list[dict] | None' = None, max_posts_per_account: 'int' = 60, "
        "concurrency: 'int' = 4) -> 'dict[str, Any]'"
    )


def test_success_missing_scanner_and_exception_keep_legacy_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, int]] = []

    async def success(handle: str, limit: int) -> dict[str, Any]:
        seen.append((handle, limit))
        return {
            "platform": "youtube",
            "handle": handle,
            "posts": [{"id": "v1"}, {"id": "v2"}],
            "stats": {"total_posts": 2, "total_views": 20, "total_likes": 5, "total_comments": 3},
        }

    async def failure(_handle: str, _limit: int) -> dict[str, Any]:
        raise RuntimeError("locked failure")

    monkeypatch.setattr(
        parallel_scan,
        "ACCOUNT_SCANNERS",
        {"youtube": success, "tiktok": failure},
    )
    accounts = [
        {"platform": "YOUTUBE", "handle": "alpha", "name": "Alpha Channel"},
        {"platform": "tiktok", "handle": "beta"},
        {"platform": "unknown", "handle": "gamma", "name": "Gamma"},
    ]

    result = asyncio.run(
        parallel_scan.scan_accounts_concurrently(
            accounts,
            max_posts_per_account=17,
            concurrency=2,
        )
    )

    assert seen == [("alpha", 17)]
    assert result["scanned"] == 3
    assert result["total"] == 3
    assert result["aggregate"] == {
        "total_posts": 2,
        "total_views": 20,
        "total_likes": 5,
        "total_comments": 3,
    }
    rows = {(row["platform"], row["handle"]): row for row in result["results"]}
    assert rows[("youtube", "alpha")]["account_name"] == "Alpha Channel"
    assert rows[("tiktok", "beta")] == {
        "platform": "tiktok",
        "handle": "beta",
        "account_name": "beta",
        "posts": [],
        "stats": {"total_posts": 0},
        "error": "locked failure",
    }
    assert rows[("unknown", "gamma")] == {
        "platform": "unknown",
        "handle": "gamma",
        "account_name": "Gamma",
        "posts": [],
        "stats": {"total_posts": 0},
        "error": "scanner_not_available",
    }


@pytest.mark.parametrize("accounts", [None, []])
def test_none_and_empty_accounts_both_use_official_matrix_with_concurrency_bound(
    monkeypatch: pytest.MonkeyPatch,
    accounts: list[dict[str, Any]] | None,
) -> None:
    active = 0
    peak = 0

    async def scanner(handle: str, _limit: int) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {
            "platform": "youtube",
            "handle": handle,
            "posts": [],
            "stats": {"total_posts": 0},
        }

    official = [
        {"platform": "youtube", "handle": "one"},
        {"platform": "youtube", "handle": "two"},
        {"platform": "youtube", "handle": "three"},
    ]
    monkeypatch.setattr(parallel_scan, "OFFICIAL_MATRIX", official)
    monkeypatch.setattr(parallel_scan, "ACCOUNT_SCANNERS", {"youtube": scanner})

    result = asyncio.run(parallel_scan.scan_accounts_concurrently(accounts, concurrency=2))

    assert result["total"] == 3
    assert result["scanned"] == 3
    assert peak == 2
