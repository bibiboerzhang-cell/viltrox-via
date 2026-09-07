"""Traceable audience observations; a country label is not audience evidence.

References are opaque record identifiers, not URLs to fetch. A source label or
well-formed reference does not authenticate anything. A server-owned resolver
must bind a claim to trusted evidence before it can satisfy a hard audience gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from collections.abc import Callable

from app.domains.kol.identity import SUPPORTED_PLATFORMS, canonical_identity_platform
from app.domains.kol.search_geo_intent import normalize_geo_country

SCHEMA = "audience_evidence_v1"
ANALYTICS_SOURCES = frozenset({"platform_audience_analytics", "creator_shared_analytics"})
LOCAL_SOURCES = ANALYTICS_SOURCES | {"operator_verified", "manual_verified", "verified_annotation"}
_REFERENCE = re.compile(r"(?:audience|analytics|evidence|annotation):[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
AudienceEvidenceResolver = Callable[[str], dict[str, Any] | None]


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _candidates(raw: Any) -> list[Any]:
    raw = _mapping(raw)
    annotations = _mapping(raw.get("qualification_annotations"))
    candidates = [raw.get("audience_market_annotation"), raw.get("audience_geo"), annotations.get("audience_market")]
    extra = raw.get("audience_market_evidence")
    if isinstance(extra, list):
        candidates.extend(extra[:16])
    return [item for item in candidates if isinstance(item, dict)][:16]


def _observation(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _valid_subject_key(value: Any) -> bool:
    """Validate canonical binding syntax, not ownership or provider authenticity."""
    if not isinstance(value, str) or len(value) > 200:
        return False
    parts = value.split(":")
    if len(parts) != 3:
        return False
    platform, kind, identifier = parts
    return (platform in SUPPORTED_PLATFORMS and canonical_identity_platform(platform) == platform
            and kind in {"id", "handle"} and 0 < len(identifier) <= 160
            and identifier == identifier.casefold()
            and all(char.isalnum() or char in "._-" for char in identifier))


def normalize_audience_observation(
    value: Any, *, allowed_sources: frozenset[str] = LOCAL_SOURCES, as_of: datetime | None = None,
) -> tuple[dict[str, Any] | None, str]:
    candidate = _mapping(value)
    source = candidate.get("source")
    source = source.strip().lower() if isinstance(source, str) else ""
    if source not in allowed_sources:
        return None, "unsupported_source"
    if candidate.get("verified") is not True:
        return None, "verification_not_asserted"
    market = normalize_geo_country(candidate.get("market") or candidate.get("value"))
    if not market:
        return None, "market_invalid"
    reference = candidate.get("evidence_ref")
    if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
        return None, "missing_or_invalid_evidence_reference"
    observed = _observation(candidate.get("observed_at"))
    if observed is None:
        return None, "observation_timestamp_missing_or_invalid"
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if observed > now.astimezone(timezone.utc) + timedelta(minutes=5):
        return None, "observation_in_future"
    return {"schema": SCHEMA, "market": market, "source": source, "verified": True,
            "evidence_ref": reference, "observed_at": observed.isoformat(),
            "verification_basis": "source_asserted_with_trace", "freshness_status": "not_evaluated"}, ""


def project_online_audience(raw: Any) -> dict[str, Any]:
    """Drop all arbitrary provider blobs, private URLs and human-proof claims."""
    raw = _mapping(raw)
    values = _candidates(raw) + _candidates(raw.get("raw_platform_data"))
    accepted = []
    for value in values[:16]:
        observation, _ = normalize_audience_observation(value, allowed_sources=ANALYTICS_SOURCES)
        if observation is not None and observation not in accepted:
            accepted.append(observation)
    if not accepted:
        return {}
    result: dict[str, Any] = {"audience_market_annotation": accepted[0]}
    if len(accepted) > 1:
        result["audience_market_evidence"] = accepted[1:]
    return result


def _resolve_reference(
    observation: dict[str, Any], resolver: AudienceEvidenceResolver | None, *, as_of: datetime | None,
    expected_subject_key: str | None,
) -> tuple[dict[str, Any] | None, str]:
    if not callable(resolver):
        return None, "audience_reference_unresolved"
    if not _valid_subject_key(expected_subject_key):
        return None, "audience_subject_missing"
    try:
        trusted = resolver(observation["evidence_ref"])
    except Exception:
        # Resolver failures never authenticate a raw claim or expose errors.
        return None, "audience_reference_unavailable"
    resolved, _reason = normalize_audience_observation(trusted, as_of=as_of)
    fields = ("evidence_ref", "market", "source", "observed_at")
    if resolved is None or any(resolved[key] != observation[key] for key in fields):
        return None, "audience_reference_mismatch"
    subject_key = _mapping(trusted).get("subject_key")
    if not _valid_subject_key(subject_key):
        return None, "audience_subject_unbound"
    if subject_key != expected_subject_key:
        return None, "audience_subject_mismatch"
    return {**resolved, "verification_basis": "server_resolved_evidence",
            "subject_binding": "matched",
            "reference_resolution": "matched", "source_authentication": "server_resolver"}, ""


def resolve_audience_evidence(
    raw: Any, *, as_of: datetime | None = None,
    evidence_resolver: AudienceEvidenceResolver | None = None,
    expected_subject_key: str | None = None,
) -> dict[str, Any]:
    """Resolve server-owned records bound to this candidate, never raw subject claims.

    The caller supplies a canonical identity from the candidate projection. This
    matches records only; a trusted adapter still owns source/identity validation.
    """
    observations, reasons = [], set()
    for value in _candidates(raw):
        observation, reason = normalize_audience_observation(value, as_of=as_of)
        if observation is not None:
            observation, reason = _resolve_reference(observation, evidence_resolver, as_of=as_of,
                                                     expected_subject_key=expected_subject_key)
        if observation is not None and observation not in observations:
            observations.append(observation)
        elif reason:
            reasons.add(reason)
    if not observations:
        return {"market": "", "markets": [], "method": "unknown", "source": None,
                "confidence": None, "evidence_status": "unknown",
                "missing_evidence_reasons": sorted(reasons) or ["audience_evidence_missing"]}
    markets = sorted({value["market"].lower() for value in observations})
    sources = sorted({value["source"] for value in observations})
    return {"market": markets[0], "markets": markets, "method": "verified_audience_evidence",
            "source": sources[0] if len(sources) == 1 else "multiple_traceable_sources",
            "sources": sources, "confidence": None, "evidence_status": "verified",
            "verification_basis": "server_resolved_evidence", "freshness_status": "not_evaluated",
            "subject_binding": "matched",
            "reference_resolution": "matched", "source_authentication": "server_resolver",
            "observations": observations}
