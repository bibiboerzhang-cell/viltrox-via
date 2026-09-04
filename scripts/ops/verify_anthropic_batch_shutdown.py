#!/usr/bin/env python3
"""Prove that an Anthropic workspace has no active Message Batches.

The release controller runs this only after every local submitter is stopped
and runtime-masked.  It scans the provider list to exhaustion and emits counts
only: batch ids, prompts, results, and credentials never enter the receipt.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


SCHEMA_VERSION = "vkpi-anthropic-batch-shutdown/v1"
ACTIVE_STATUSES = frozenset({"in_progress", "canceling"})
TERMINAL_STATUSES = frozenset({"ended"})
DEFAULT_PAGE_SIZE = 1000
DEFAULT_MAX_PAGES = 100


class ProviderProofError(RuntimeError):
    """A bounded reason that is safe to expose in a release receipt."""


def _failure(reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": False,
        "provider": "anthropic",
        "provider_scope": "api_key_workspace",
        "reconcile_complete": False,
        "credentials_emitted": False,
        "batch_ids_emitted": False,
        "reason": reason,
    }


def build_provider_shutdown_receipt(
    client: Any,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Exhaustively list one API-key workspace and count active batches."""

    if isinstance(page_size, bool) or not 1 <= int(page_size) <= 1000:
        raise ProviderProofError("provider_page_size_invalid")
    if isinstance(max_pages, bool) or int(max_pages) <= 0:
        raise ProviderProofError("provider_page_limit_invalid")

    page = client.messages.batches.list(limit=int(page_size), timeout=30.0)
    seen_ids: set[str] = set()
    pages_scanned = 0
    active_count = 0
    ended_count = 0

    while True:
        pages_scanned += 1
        if pages_scanned > int(max_pages):
            raise ProviderProofError("provider_pagination_limit_exceeded")
        data = getattr(page, "data", None)
        if not isinstance(data, list):
            raise ProviderProofError("provider_page_invalid")
        for batch in data:
            batch_id = str(getattr(batch, "id", "") or "").strip()
            status = str(getattr(batch, "processing_status", "") or "").strip()
            if not batch_id or batch_id in seen_ids:
                raise ProviderProofError("provider_batch_identity_invalid")
            seen_ids.add(batch_id)
            if status in ACTIVE_STATUSES:
                active_count += 1
            elif status in TERMINAL_STATUSES:
                ended_count += 1
            else:
                raise ProviderProofError("provider_batch_status_unknown")
        try:
            has_next = page.has_next_page()
        except Exception as exc:  # provider SDK shape is part of the proof
            raise ProviderProofError("provider_pagination_invalid") from exc
        if not has_next:
            break
        if pages_scanned >= int(max_pages):
            raise ProviderProofError("provider_pagination_limit_exceeded")
        try:
            page = page.get_next_page()
        except Exception as exc:
            raise ProviderProofError("provider_page_fetch_failed") from exc

    passed = active_count == 0
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": passed,
        "provider": "anthropic",
        "provider_scope": "api_key_workspace",
        "reconcile_complete": True,
        "pages_scanned": pages_scanned,
        "batches_scanned": len(seen_ids),
        "active_count": active_count,
        "ended_count": ended_count,
        "credentials_emitted": False,
        "batch_ids_emitted": False,
        "reason": "provider_batches_active" if not passed else "",
    }


def main() -> int:
    api_key = str(os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        receipt = _failure("anthropic_api_key_missing")
        exit_code = 2
    else:
        try:
            import anthropic

            receipt = build_provider_shutdown_receipt(
                anthropic.Anthropic(api_key=api_key, max_retries=1, timeout=30.0)
            )
            exit_code = 0 if receipt["passed"] else 3
        except ProviderProofError as exc:
            receipt = _failure(str(exc))
            exit_code = 2
        except Exception:
            receipt = _failure("provider_list_unavailable")
            exit_code = 2
    sys.stdout.write(json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
