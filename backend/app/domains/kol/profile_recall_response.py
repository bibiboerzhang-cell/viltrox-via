"""HTTP-only projection for strict Smart-local recall responses.

The recall engine and durable search-session attachment keep the full rich
bucket contract.  This module removes repeated copies only at the Smart HTTP
response boundary; the legacy ``/kol-recall`` route is intentionally untouched.
"""
from __future__ import annotations

from typing import Any

from app.domains.kol.profile_recall_qualification import SMART_LOCAL_SCHEMA


_REFERENCE_FIELDS = (
    "kol_pool_id",
    "id",
    "platform",
    "handle",
    "bucket",
    "candidate_bucket",
    "server_rank",
    "global_rank",
)


def _item_reference(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    return {key: item[key] for key in _REFERENCE_FIELDS if item.get(key) not in (None, "")}


def compact_smart_local_api_result(result: dict[str, Any]) -> dict[str, Any]:
    """Send one rich canonical item copy plus lightweight bucket references."""

    contract = result.get("local_qualification") if isinstance(result.get("local_qualification"), dict) else {}
    if contract.get("schema") != SMART_LOCAL_SCHEMA:
        return result

    compacted = dict(result)
    for field in ("buckets", "business_buckets"):
        buckets = result.get(field) if isinstance(result.get(field), dict) else {}
        compacted[field] = {
            key: [_item_reference(item) for item in values if isinstance(item, dict)]
            for key, values in buckets.items()
            if isinstance(values, list)
        }

    compact_contract = dict(contract)
    gate_evidence = compact_contract.pop("gate_evidence", [])
    rejected_sample = compact_contract.pop("rejected_evidence_sample", [])
    compact_contract["gate_evidence_count"] = len(gate_evidence) if isinstance(gate_evidence, list) else 0
    compact_contract["rejected_evidence_sample_count"] = len(rejected_sample) if isinstance(rejected_sample, list) else 0
    compacted["local_qualification"] = compact_contract
    compacted["response_projection"] = {
        "schema": "smart_local_compact_v1",
        "canonical_items": "items",
        "bucket_entries": "references",
        "qualification_evidence": "items[].qualification_evidence",
    }
    return compacted
