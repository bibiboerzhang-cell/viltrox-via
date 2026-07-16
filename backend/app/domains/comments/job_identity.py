"""Stable identities and freshness policy for KOL comment collection jobs."""
from __future__ import annotations

import hashlib
import os
from typing import Iterable

from app.domains.tasks.apify_idempotency import active_job_idempotency_key


DEFAULT_COMMENTS_FRESHNESS_HOURS = 24
COMMENTS_IDENTITY_VERSION = "comments-evidence-v1"


def normalize_evidence_ids(values: Iterable[object] | None) -> list[int]:
    normalized: set[int] = set()
    for value in values or ():
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            normalized.add(parsed)
    return sorted(normalized)


def evidence_set_hash(values: Iterable[object] | None) -> str:
    canonical = ",".join(str(value) for value in normalize_evidence_ids(values))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def comments_freshness_hours() -> int:
    raw = os.environ.get("APIFY_COMMENTS_FRESHNESS_HOURS", str(DEFAULT_COMMENTS_FRESHNESS_HOURS))
    try:
        return max(1, min(int(raw), 24 * 30))
    except (TypeError, ValueError):
        return DEFAULT_COMMENTS_FRESHNESS_HOURS


def comments_job_identity(kol_pool_id: int, evidence_ids: Iterable[object] | None) -> tuple[str, str]:
    data_version = evidence_set_hash(evidence_ids)
    key = active_job_idempotency_key(
        "kol_pool_comments_collect",
        int(kol_pool_id),
        COMMENTS_IDENTITY_VERSION,
        data_version,
    )
    return key, data_version
