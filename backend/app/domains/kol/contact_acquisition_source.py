"""Trust classification for already-extracted contact candidates.

All policy data is supplied by the queue facade at call time so its existing
test/monkeypatch seam remains authoritative.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
import re
from typing import Any
from urllib.parse import urlsplit


_PUBLIC_BIO_FIELDS = frozenset(
    {
        "profile.bio",
        "profile.biography",
        "profile.about",
        "profile.description",
        "profile.channel_description",
        "profile.signature",
        "profile.snippet.description",
        "profile.items.0.snippet.description",
    }
)


def _confidence(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_field(candidate: dict[str, Any], pattern: re.Pattern[str]) -> str:
    raw = str(candidate.get("source_field") or "").strip()
    return raw if pattern.fullmatch(raw) else "raw_platform_data"


def _source_host(source_url: Any) -> str:
    try:
        return (urlsplit(str(source_url or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def _host_matches(
    source_host: str,
    platform_key: str,
    platform_hosts: Mapping[str, Collection[str]],
) -> bool:
    expected_hosts = platform_hosts.get(platform_key, ())
    return bool(
        source_host
        and expected_hosts
        and any(source_host == host or source_host.endswith("." + host) for host in expected_hosts)
    )


def _is_public_bio_field(field_key: str) -> bool:
    return field_key in _PUBLIC_BIO_FIELDS or field_key.endswith(
        (".bio", ".biography", ".signature", ".description")
    )


def candidate_source(
    candidate: dict[str, Any],
    *,
    platform: Any,
    source_url: Any,
    source_field_pattern: re.Pattern[str],
    platform_hosts: Mapping[str, Collection[str]],
    explicit_bio_anchors: Collection[str],
    ig_public_fields: Collection[str],
    youtube_public_fields: Collection[str],
    public_verification_sources: Collection[str],
) -> tuple[str, bool, str]:
    source = str(candidate.get("source_type") or "raw_full_scan").strip().lower()
    platform_key = str(platform or "").strip().casefold()
    contact_type = str(candidate.get("contact_type") or "").strip().lower()
    confidence = _confidence(candidate.get("confidence"))
    evidence = str(candidate.get("evidence_text") or "")[:280].casefold()
    value = str(candidate.get("contact_value") or "").strip().casefold()
    source_field = _source_field(candidate, source_field_pattern)
    field_key = source_field.casefold()
    platform_host_matches = _host_matches(
        _source_host(source_url), platform_key, platform_hosts
    )
    bounded_identity_proof = bool(
        value
        and value in evidence
        and any(anchor in evidence for anchor in explicit_bio_anchors)
    )
    field_identity_proof = bool(value and value in evidence)
    public_bio_field = _is_public_bio_field(field_key)
    if source == "ig_business_profile" and not (
        platform_key in {"instagram", "ig"}
        and platform_host_matches
        and field_key in ig_public_fields
        and field_identity_proof
    ):
        source = "raw_bio_scan"
    if source == "youtube_about_declared" and not (
        platform_key in {"youtube", "yt"}
        and platform_host_matches
        and field_key in youtube_public_fields
        and field_identity_proof
    ):
        source = "raw_bio_scan"
    if source == "website_declared":
        source = "raw_bio_scan"
    if (
        source == "raw_bio_scan"
        and platform_host_matches
        and public_bio_field
        and contact_type in {"email", "business_email", "public_email", "contact_email"}
        and confidence >= 0.85
        and bounded_identity_proof
    ):
        source = "bio_explicit_contact"
    if source == "bio_explicit_contact" and not (
        platform_host_matches
        and public_bio_field
        and confidence >= 0.85
        and bounded_identity_proof
    ):
        source = "raw_bio_scan"
    public_declared = source in public_verification_sources and confidence >= 0.85
    return source, public_declared, source_field
