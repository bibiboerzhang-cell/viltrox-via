"""Bounded, IO-free discovery observations; identity is not evidence identity.

These records preserve provider evidence in memory, not a public qualification
claim. Consumers must still validate content identity, provenance and dates.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable
import hashlib
import json
from typing import Any

MAX_CONTENT_OBSERVATIONS = 24
_CONTENT_FIELDS = {
    "title": ("title", "sample_title"),
    "description": ("description", "video_description", "sample_description", "content_description"),
    "caption": ("caption", "caption_text", "sample_caption"),
    "transcript": ("transcript", "transcript_text", "sample_transcript"),
    "subtitles": ("subtitles", "subtitle_text"),
    "content_url": ("content_url", "video_url", "post_url", "source_url"),
    "video_id": ("video_id", "native_video_id", "content_id"),
    "posted_at": ("posted_at", "published_at", "published"),
    "source": ("source",),
    "platform": ("platform",),
    "fetched_at": ("fetched_at",),
    "is_active": ("is_active",),
}
_CELL_FIELDS = (
    "query_cell_id", "primary_query", "executed_query", "segment", "objective",
    "required_scene_terms", "required_role_terms", "required_evidence_groups",
)
_PROFILE_FIELDS = (
    "bio", "description", "profile_text", "primary_topic", "content_style",
    "followers", "subscriber_count", "follower_count", "country", "country_source", "market_source", "language", "content_language",
    "language_source", "profile_type", "profile_type_source", "avg_views",
    "avg_likes", "avg_comments", "engagement_rate", "activation_sample_count",
    "audience_evidence", "facet_evidence",
)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _lineage(raw: dict[str, Any]) -> list[dict[str, Any]]:
    cells = raw.get("matched_query_cells")
    values = cells if isinstance(cells, list) else [raw]
    return [{key: deepcopy(cell[key]) for key in _CELL_FIELDS if cell.get(key) not in (None, "", [])}
            for cell in values[:8] if isinstance(cell, dict) and cell.get("query_cell_id")]


def _content_record(source: dict[str, Any], *, raw: dict[str, Any], flat: bool) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field, aliases in _CONTENT_FIELDS.items():
        for alias in aliases:
            value = source.get(alias)
            if value not in (None, "", [], {}):
                record[field] = deepcopy(value[:8000] if isinstance(value, str) else value)
                break
    if not any(record.get(key) for key in ("title", "description", "caption", "transcript", "subtitles", "content_url", "video_id")):
        return {}
    record.setdefault("platform", raw.get("platform"))
    if raw.get("fetched_at") and not record.get("fetched_at"):
        record["fetched_at"] = str(raw["fetched_at"])[:80]
    # This matches the existing flat content-search adapter, not a new trust
    # grant for structured provider records lacking an auditable source.
    if flat and record.get("content_url") and record.get("posted_at"):
        record["source"] = "platform_content_search"
    lineage = source.get("discovery_observations") or _lineage(raw)
    if isinstance(lineage, list) and lineage:
        record["discovery_observations"] = deepcopy(lineage[:8])
    return record


def content_observations(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain separate video/text records and their server discovery cells."""
    sources: list[tuple[dict[str, Any], bool]] = []
    # Preserve already-merged records first so repeated merges do not crowd out
    # later-cell observations with duplicated flat/representative records.
    for key in ("video_evidence", "representative_evidence", "recent_videos"):
        values = raw.get(key)
        if isinstance(values, list):
            sources.extend((value, False) for value in values[:MAX_CONTENT_OBSERVATIONS] if isinstance(value, dict))
    if isinstance(raw.get("latest_real_video"), dict):
        sources.append((raw["latest_real_video"], False))
    sources.append((raw, True))
    output: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for source, flat in sources:
        record = _content_record(source, raw=raw, flat=flat)
        if not record:
            continue
        key = _digest({name: value for name, value in record.items() if name not in {"discovery_observations", "fetched_at"}})
        if key in seen:
            _merge_lineage(seen[key], record, field="discovery_observations")
        elif len(output) < MAX_CONTENT_OBSERVATIONS:
            output.append(record)
            seen[key] = record
    return output


def _merge_lineage(existing: dict[str, Any], incoming: dict[str, Any], *, field: str) -> None:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for owner in (existing, incoming):
        values = owner.get(field)
        for value in values if isinstance(values, list) else []:
            if not isinstance(value, dict):
                continue
            key = _digest(value) if field == "discovery_observations" else str(value.get("query_cell_id") or "")
            if key and key not in seen:
                merged.append(deepcopy(value))
                seen.add(key)
    if merged:
        existing[field] = merged[:8]


def merge_candidate_observations(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge observations without replacing first-observed profile attributes."""
    merged = deepcopy(existing)
    for key, value in incoming.items():
        if merged.get(key) in (None, "", [], {}):
            merged[key] = deepcopy(value)
    observations = content_observations(existing) + content_observations(incoming)
    if observations:
        merged["video_evidence"] = content_observations({"video_evidence": observations, "platform": merged.get("platform")})
    _merge_lineage(merged, incoming, field="matched_query_cells")
    return merged


def candidate_observation_fingerprint(raw: dict[str, Any], *, creator_key: str) -> str:
    """Ignore fetch timestamps; new content or an actual new cell is not replay."""
    from app.domains.kol.audience_evidence import project_online_audience

    return _digest({
        "creator": creator_key,
        "profile": {key: raw[key] for key in _PROFILE_FIELDS if raw.get(key) not in (None, "", [], {})},
        "content": [{key: value for key, value in row.items() if key != "fetched_at"} for row in content_observations(raw)],
        "cells": _lineage(raw),
        # Hash the same safe audience projection consumed by the actual gate,
        # not arbitrary private provider blobs or an unused lookalike field.
        "audience": project_online_audience(raw),
    })


def merge_qualified_cell_observations(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Enrich an already accepted card without re-enrolling or changing its rank.

    Both arguments are server-qualified public projections. A later failed cell
    never erases an earlier passing cell; this does not promote discovery tags.
    """
    cells = {str(cell.get("query_cell_id")): deepcopy(cell)
             for cell in existing.get("cell_qualification") or [] if isinstance(cell, dict)}
    for cell in incoming.get("cell_qualification") or []:
        if not isinstance(cell, dict):
            continue
        key = str(cell.get("query_cell_id") or "")
        previous = cells.get(key, {})
        if key and (previous.get("passed") is not True or cell.get("passed") is True):
            cells[key] = deepcopy(cell)
    if cells:
        existing["cell_qualification"] = list(cells.values())[:8]
    _merge_lineage(existing, incoming, field="matched_query_cells")


def batch_observation_fingerprint(batch: list[dict[str, Any]], creator_key: Callable[..., str]) -> str:
    return _digest([candidate_observation_fingerprint(raw, creator_key=creator_key(raw)) for raw in batch])


class OnlineObservationCache:
    """One bounded collect-loop cache; never changes candidate/provider quotas."""

    def __init__(self) -> None:
        self.seen: dict[str, set[str]] = {}
        self.previous: dict[str, dict[str, Any]] = {}

    def prepare(self, raw: dict[str, Any], *, creator_key: str, aliases: set[str], accepted_aliases: set[str]) -> dict[str, Any] | None:
        fingerprint = candidate_observation_fingerprint(raw, creator_key="")
        refreshing = bool(aliases.intersection(accepted_aliases))
        if refreshing and any(fingerprint in self.seen.get(alias, set()) for alias in aliases):
            return None
        for alias in aliases:
            self.seen.setdefault(alias, set()).add(fingerprint)
        identity_keys = aliases or ({creator_key} if creator_key else set())
        previous = next((self.previous[key] for key in sorted(identity_keys) if key in self.previous), None)
        merged = merge_candidate_observations(raw, previous) if previous is not None else deepcopy(raw)
        for key in identity_keys:
            self.previous[key] = merged
        if refreshing:
            merged["_refresh_accepted_observation"] = True
        return merged


def refresh_accepted_observation(accepted: list[dict[str, Any]], outcome: dict[str, Any], aliases_fn: Callable[..., set[str]]) -> bool:
    source = outcome.get("source")
    if not isinstance(source, dict) or source.get("_refresh_accepted_observation") is not True:
        return False
    projected = outcome.get("item") or {}
    aliases = aliases_fn(projected)
    for existing in accepted:
        if aliases.intersection(aliases_fn(existing)):
            merge_qualified_cell_observations(existing, projected)
            return True
    return False


def qualification_cell_observations(adapted: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project real candidate×cell adjudications, never discovery tags as proof."""
    observations = []
    for item, outcome in zip(adapted, outcomes):
        cells = [{"query_cell_id": str(cell.get("query_cell_id") or "")[:120],
                  "passed": cell.get("passed") is True,
                  "reasons": [str(reason)[:120] for reason in (cell.get("reasons") or [])[:8]]}
                 for cell in (item.get("cell_qualification") or [])[:8] if isinstance(cell, dict)]
        observations.append({"canonical_key": outcome.get("canonical_key"), "status": outcome.get("status"),
                             "eight_gates_passed": outcome.get("eight_gates_passed") is True, "cells": cells})
    return observations


def notify_round_observer(observer: Callable[..., Any] | None, payload: dict[str, Any]) -> bool:
    if observer is None:
        return True
    try:
        observer(deepcopy(payload))
    except Exception:
        return False
    return True


def merge_shortfall_reasons(rejected: dict[str, int], statuses: dict[str, int], *, shortfall: int, terminal_reason: str) -> dict[str, int]:
    reasons = dict(rejected)
    for reason in ("pending", "rejected", "duplicate_local", "duplicate_local_inventory", "duplicate_online", "duplicate_batch"):
        count = statuses.get(reason, 0)
        if count:
            reasons[reason] = reasons.get(reason, 0) + count
    if shortfall:
        reasons[terminal_reason] = reasons.get(terminal_reason, 0) + shortfall
    return reasons


def audience_source_block_reason(policy: dict[str, Any], resolver: Any) -> str:
    """A resolver is a fixed trusted in-memory snapshot, never a fetch adapter.

    Its invocation must not perform provider/network/DB work outside the
    discovery budget. Production analytics integration is not configured by
    passing a source name or serialized policy field.
    """
    geo = policy.get("geo_constraints")
    geo = geo if isinstance(geo, dict) else {}
    if geo.get("audience_markets") and geo.get("audience_mode", "require") == "require" and not callable(resolver):
        return "audience_evidence_source_unavailable"
    return ""


__all__ = ["content_observations", "merge_candidate_observations", "candidate_observation_fingerprint",
           "merge_qualified_cell_observations", "OnlineObservationCache", "batch_observation_fingerprint",
           "refresh_accepted_observation", "qualification_cell_observations", "notify_round_observer", "merge_shortfall_reasons",
           "audience_source_block_reason"]
