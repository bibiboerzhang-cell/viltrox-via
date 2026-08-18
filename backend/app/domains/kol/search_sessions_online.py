"""Authoritative persistence for pool-backed strict-online search snapshots."""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol.profile_recall_match_evidence import (
    query_evidence_terms,
    why_fit_from_match_evidence,
)
from app.domains.kol.search_sessions_attach import (
    _safe_candidate_facets,
    _safe_gate_evidence,
    _safe_match_evidence,
    _safe_non_negative_float,
    _safe_non_negative_int,
    _safe_public_code,
)
from app.domains.kol.search_sessions_serde import _dict, _int_or_none, _list, _text


ONLINE_SCHEMA = "smart_online_net_new_qualified_v1"
ONLINE_SOURCE = "platform_discovery_strict"
ONLINE_TARGET = 30
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_GATE_FIELDS = (
    "account_quality", "followers", "activity", "market",
    "language", "profile_type", "platform", "relevance",
)


def queued_online_qualification(status: Any = "queued") -> dict[str, Any]:
    """Return the single safe placeholder used before an online snapshot exists."""
    return {
        "schema": ONLINE_SCHEMA,
        "policy_version": 1,
        "server_owned": True,
        "origin_lane": "online",
        "source": ONLINE_SOURCE,
        "status": _safe_public_code(status, limit=40) or "queued",
        "terminal": False,
        "snapshot_complete": False,
        "snapshot_revision": 0,
        "target_count": ONLINE_TARGET,
        "returned_count": 0,
        "net_new_accepted_count": 0,
        "shortfall": ONLINE_TARGET,
        "enrichment_queue": {
            "status": "not_enriched",
            "async": False,
            "queued": 0,
            "already_queued": 0,
            "failed": 0,
        },
    }


def safe_online_qualification(value: Any) -> dict[str, Any]:
    raw = _dict(value)
    if not (
        _text(raw.get("schema")) == ONLINE_SCHEMA
        and _safe_non_negative_int(raw.get("policy_version")) == 1
        and raw.get("server_owned") is True
        and _text(raw.get("origin_lane")) == "online"
        and _text(raw.get("source")) == ONLINE_SOURCE
        and _safe_non_negative_int(raw.get("target_count"), maximum=ONLINE_TARGET) == ONLINE_TARGET
    ):
        return {}
    output: dict[str, Any] = {
        "schema": ONLINE_SCHEMA,
        "policy_version": 1,
        "server_owned": True,
        "origin_lane": "online",
        "source": ONLINE_SOURCE,
        "terminal": raw.get("terminal") is True,
        "snapshot_complete": raw.get("snapshot_complete") is True,
        "exhausted": raw.get("exhausted") is True,
        "target_count": ONLINE_TARGET,
    }
    status = _text(raw.get("status")).lower()
    if status in {"ready", "shortfall"}:
        output["status"] = status
    for key in (
        "snapshot_revision", "evaluated_count", "strict_qualified_count",
        "net_new_accepted_count", "returned_count", "pending_count", "rejected_count",
        "qualified_overflow_count", "duplicate_local_count", "duplicate_local_inventory_count",
        "duplicate_online_count", "provider_rounds", "provider_calls", "candidate_budget",
        "candidate_budget_used", "shortfall", "inventory_snapshot_rows", "inventory_db_reads",
        "materialization_db_reads", "total_identity_db_reads",
    ):
        number = _safe_non_negative_int(raw.get(key), maximum=1_000_000)
        if number is not None:
            output[key] = number
    snapshot_id = _safe_public_code(raw.get("snapshot_id"), limit=64)
    if not snapshot_id:
        return {}
    output["snapshot_id"] = snapshot_id
    for source_key in ("shortfall_reasons", "rejected_by_reason"):
        counts: dict[str, int] = {}
        for key, value in list(_dict(raw.get(source_key)).items())[:48]:
            safe_key = _safe_public_code(key, limit=80)
            count = _safe_non_negative_int(value, maximum=1_000_000)
            if safe_key and count is not None:
                counts[safe_key] = count
        output[source_key] = counts
    timing: dict[str, float] = {}
    for key, value in list(_dict(raw.get("stage_timing")).items())[:8]:
        safe_key = _safe_public_code(key, limit=80)
        number = _safe_non_negative_float(value, maximum=86_400_000)
        if safe_key and number is not None:
            timing[safe_key] = number
    if timing:
        output["stage_timing"] = timing
    policy = _dict(raw.get("policy"))
    output["policy"] = {
        "policy_version": 1,
        "target_count": ONLINE_TARGET,
        "min_followers": _safe_non_negative_int(policy.get("min_followers"), maximum=5_000_000_000),
        "max_video_age_days": _safe_non_negative_int(policy.get("max_video_age_days"), maximum=3650),
        "market": _safe_public_code(policy.get("market"), limit=40),
        "platforms": [_safe_public_code(item, limit=40) for item in _list(policy.get("platforms"))[:3] if _safe_public_code(item, limit=40)],
        "languages": [_safe_public_code(item, limit=20) for item in _list(policy.get("languages"))[:8] if _safe_public_code(item, limit=20)],
        "profile_types": [_safe_public_code(item, limit=40) for item in _list(policy.get("profile_types"))[:3] if _safe_public_code(item, limit=40)],
        "supported_platforms": ["instagram", "tiktok", "youtube"],
        "exclude_chinese_regions": policy.get("exclude_chinese_regions") is True,
    }
    enrichment = _dict(raw.get("enrichment_queue"))
    output["enrichment_queue"] = {
        "status": _safe_public_code(enrichment.get("status"), limit=40),
        "async": enrichment.get("async") is True,
        "queued": _safe_non_negative_int(enrichment.get("queued")) or 0,
        "already_queued": _safe_non_negative_int(enrichment.get("already_queued")) or 0,
        "failed": _safe_non_negative_int(enrichment.get("failed")) or 0,
    }
    return output


def _strict_bound_item(
    raw: dict[str, Any],
    *,
    incoming_snapshot_id: str,
    incoming_revision: int,
    allowed_terms: set[str],
) -> dict[str, Any] | None:
    kol_pool_id = _int_or_none(raw.get("kol_pool_id"))
    fingerprint = _safe_public_code(raw.get("canonical_fingerprint"), limit=64)
    proof = _safe_gate_evidence(raw.get("qualification_evidence"), allowed_terms=allowed_terms)
    server_rank = _safe_non_negative_int(raw.get("server_rank"), maximum=ONLINE_TARGET)
    global_rank = _safe_non_negative_int(raw.get("global_unique_rank"), maximum=ONLINE_TARGET * 2)
    if not (
        kol_pool_id
        and _FINGERPRINT_RE.fullmatch(fingerprint)
        and raw.get("origin_lane") == "online"
        and raw.get("source") == ONLINE_SOURCE
        and raw.get("qualification_status") == "accepted"
        and proof.get("passed") is True
        and all(_dict(proof.get(field)).get("passed") is True for field in _GATE_FIELDS)
        and _list(_dict(proof.get("relevance")).get("evidence"))
        and _int_or_none(proof.get("kol_pool_id")) == kol_pool_id
        and proof.get("canonical_fingerprint") == fingerprint
        and raw.get("snapshot_id") == incoming_snapshot_id
        and proof.get("snapshot_id") == incoming_snapshot_id
        and _safe_non_negative_int(raw.get("snapshot_revision")) == incoming_revision
        and _safe_non_negative_int(proof.get("snapshot_revision")) == incoming_revision
        and server_rank is not None and server_rank >= 1
        and global_rank is not None and global_rank >= 1
        and _safe_non_negative_int(proof.get("server_rank"), maximum=ONLINE_TARGET) == server_rank
        and _safe_non_negative_int(proof.get("global_unique_rank"), maximum=ONLINE_TARGET * 2) == global_rank
    ):
        return None
    return {
        "kol_pool_id": kol_pool_id,
        "canonical_fingerprint": fingerprint,
        "proof": proof,
        "server_rank": server_rank,
        "global_unique_rank": global_rank,
    }


def attach_online_qualified_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    from app.domains.kol.search_sessions import get_session, record_items

    current = get_session(int(session_id))
    existing_summary = _dict(current.get("result_summary"))
    existing_contract = _dict(existing_summary.get("online_qualification"))
    contract = safe_online_qualification(result)
    if not contract or contract.get("snapshot_complete") is not True or contract.get("terminal") is not True:
        raise ValueError("invalid online qualification contract")
    incoming_revision = _safe_non_negative_int(contract.get("snapshot_revision")) or 1
    existing_revision = _safe_non_negative_int(existing_contract.get("snapshot_revision")) or 0
    same_snapshot = contract["snapshot_id"] == existing_contract.get("snapshot_id")
    revision = existing_revision if same_snapshot and existing_revision else max(existing_revision + 1, incoming_revision)

    result_query = _dict(result.get("query"))
    query_text = _text(result_query.get("query_text")) if result_query.get("source") == "server_effective_query" else _text(current.get("query_text"))
    allowed_terms = set(query_evidence_terms(query_text))
    allowed_terms.update(query_evidence_terms(" ".join(_text(term) for term in _list(result_query.get("required_product_evidence_terms"))[:12])))
    items: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    seen_pool_ids: set[int] = set()
    seen_server_ranks: set[int] = set()
    seen_global_ranks: set[int] = set()
    for raw in _list(result.get("items")):
        if not isinstance(raw, dict):
            continue
        bound = _strict_bound_item(
            raw,
            incoming_snapshot_id=contract["snapshot_id"],
            incoming_revision=incoming_revision,
            allowed_terms=allowed_terms,
        )
        if not bound:
            continue
        if (
            bound["canonical_fingerprint"] in seen_fingerprints
            or bound["kol_pool_id"] in seen_pool_ids
            or bound["server_rank"] in seen_server_ranks
            or bound["global_unique_rank"] in seen_global_ranks
        ):
            continue
        seen_fingerprints.add(bound["canonical_fingerprint"])
        seen_pool_ids.add(bound["kol_pool_id"])
        seen_server_ranks.add(bound["server_rank"])
        seen_global_ranks.add(bound["global_unique_rank"])
        proof = dict(bound["proof"])
        proof["snapshot_revision"] = revision
        relevance = _dict(proof.get("relevance"))
        source_url = _text(raw.get("profile_url") or raw.get("channel_url"))
        contact = _dict(raw.get("contact_preview"))
        analysis = _dict(raw.get("analysis_preview"))
        payload = {
            "schema": ONLINE_SCHEMA,
            "origin_lane": "online",
            "source": ONLINE_SOURCE,
            "qualification_status": "accepted",
            "canonical_fingerprint": bound["canonical_fingerprint"],
            "duplicate_of_lane": None,
            "server_rank": bound["server_rank"],
            "global_unique_rank": bound["global_unique_rank"],
            "snapshot_revision": revision,
            "snapshot_id": contract["snapshot_id"],
            "handle": raw.get("handle"),
            "display_name": raw.get("display_name"),
            "platform": raw.get("platform"),
            "profile_type": raw.get("profile_type"),
            "language": raw.get("language"),
            "country": raw.get("country"),
            "followers": raw.get("followers"),
            "profile_url": source_url,
            "avatar_url": raw.get("avatar_url"),
            "candidate_facets": _safe_candidate_facets(raw.get("candidate_facets")),
            "match_evidence": _safe_match_evidence(raw.get("match_evidence"), allowed_terms=allowed_terms),
            "why_fit": why_fit_from_match_evidence(relevance.get("evidence") or []),
            "qualification_evidence": proof,
            "contact_preview": {
                "status": _safe_public_code(contact.get("status"), limit=40) or "pending",
                "channel_count": _safe_non_negative_int(contact.get("channel_count"), maximum=100) or 0,
                "async": contact.get("async") is True,
            },
            "analysis_preview": {
                "status": _safe_public_code(analysis.get("status"), limit=40) or "pending",
                "async": analysis.get("async") is True,
                **({"job_id": _int_or_none(analysis.get("job_id"))} if _int_or_none(analysis.get("job_id")) else {}),
            },
        }
        items.append({
            "dedupe_key": f"online:{bound['canonical_fingerprint']}",
            "item_type": "online_qualified_candidate",
            "status": "ready",
            "stage": "qualified",
            "rank": bound["server_rank"],
            "score": _safe_non_negative_float(raw.get("display_rank_score") or raw.get("recall_rank_score")),
            "kol_pool_id": bound["kol_pool_id"],
            "source_url": source_url,
            "payload": payload,
        })
    contract["snapshot_revision"] = revision
    contract["returned_count"] = len(items)
    contract["net_new_accepted_count"] = len(items)
    contract["shortfall"] = ONLINE_TARGET - len(items)
    contract["status"] = "ready" if len(items) == ONLINE_TARGET else "shortfall"
    summary = {
        **existing_summary,
        "_authoritative_snapshot_lane": "online",
        "online_snapshot_attached": True,
        "online_snapshot_complete": True,
        "online_qualification": contract,
    }
    recorded = record_items(
        int(session_id),
        items,
        status="ready" if len(items) == ONLINE_TARGET else "partial",
        summary=summary,
    )
    recorded["online_qualification"] = contract
    return recorded
