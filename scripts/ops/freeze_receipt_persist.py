#!/usr/bin/env python3
"""Persist the canonical static-gate receipt that freeze would otherwise discard.

Why this module exists (2026-09-02 实测):
``freeze_worktree_candidate`` runs ``scripts/verify.sh`` inside a phase-A sandbox
and points ``VKPI_VERIFY_JSON_OUT`` at ``<sandbox>/canonical-static-gate.json``.
The sandbox is removed on the way out, so every freeze produced a timed canonical
receipt and then deleted it.  Thirteen train launches on 2026-09-01 left the
delivery collector (``vkpi_engineering_health_delivery.py``) with **zero**
``build_test_p95_minutes`` samples, although each run had a real duration.

The collector reads exactly one place — ``runtime/ops/verify-receipts/`` — and
only counts receipts that carry ``duration_seconds`` and ``generated_at``.
This helper copies the already-validated payload there, atomically, after the
freeze has passed its final source-state assertion (``runtime/`` is git-ignored,
so the write can never trip that assertion).  It is telemetry, not a release
artifact: it never influences the candidate, the manifest, or any gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

VERIFY_RECEIPTS_RELATIVE = Path("runtime/ops/verify-receipts")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def collector_eligible(canonical: object) -> bool:
    """Mirror the delivery collector's acceptance rule for one receipt."""
    if not isinstance(canonical, dict):
        return False
    duration = canonical.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return False
    return isinstance(canonical.get("generated_at"), str) and bool(canonical["generated_at"])


def _compact_utc_stamp(value: object) -> str:
    """``2026-09-02T01:52:23+00:00`` -> ``20260902T015223Z``; unparseable -> now."""
    moment: datetime | None = None
    if isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is None:
        moment = datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def receipt_file_name(output: Path, canonical: dict[str, object]) -> str:
    stamp = _compact_utc_stamp(canonical.get("generated_at"))
    return f"{_SAFE.sub('-', output.name)}-{stamp}.canonical-gate.json"


def persist_build_test_receipt(
    *, source: Path, output: Path, canonical: dict[str, object], writer,
) -> dict[str, object]:
    """Write ``canonical`` under ``<source>/runtime/ops/verify-receipts``.

    ``writer(path, payload) -> identity`` is freeze's own atomic JSON writer so
    ownership and exclusivity rules stay identical to the other freeze outputs.
    Returns a record for the manifest; ``collector_eligible`` says whether the
    delivery collector will actually count this sample.
    """
    target = source / VERIFY_RECEIPTS_RELATIVE / receipt_file_name(output, canonical)
    identity = writer(target, canonical)
    digest = hashlib.sha256(
        (json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()
    return {
        "path": str(target),
        "sha256": digest,
        "identity": list(identity) if isinstance(identity, tuple) else identity,
        "collector_eligible": collector_eligible(canonical),
        "collector_dir": str(VERIFY_RECEIPTS_RELATIVE),
    }
