"""Content-addressed snapshots for the offline Dealer/Event source contract."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.domains.source_passport_core import (
    CONTRACT_ID,
    SNAPSHOT_VERSION,
    canonical_json_sha256,
)
from app.domains.source_passport_urls import canonical_source_url


_CONTACT_FIELDS = ("phone", "contact_email", "store_hours", "public_services")


def _record_fingerprint(fields: Mapping[str, Any]) -> dict[str, Any]:
    canonical = dict(fields)
    field_sha256 = {
        key: canonical_json_sha256(value)
        for key, value in sorted(canonical.items())
    }
    return {
        "record_sha256": canonical_json_sha256(canonical),
        "field_sha256": field_sha256,
    }


def _snapshot_key(prefix: str, explicit: Any, fallback: Mapping[str, Any]) -> str:
    value = str(explicit or "").strip()
    if value:
        return value
    return f"unresolved_{prefix}_{canonical_json_sha256(fallback)[:20]}"


def build_source_snapshot(
    catalog: Mapping[str, Any],
    dealers: list[dict[str, Any]],
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    """Hash truth-relevant fields while keeping raw public values out of snapshots."""
    records: dict[str, dict[str, Any]] = {
        "event_sources": {},
        "event_opportunities": {},
        "dealer_locations": {},
    }
    sources = catalog.get("sources", [])
    for raw in sources if isinstance(sources, list) else []:
        if not isinstance(raw, Mapping):
            continue
        url = canonical_source_url(raw.get("canonical_url"))
        key = _snapshot_key(
            "event_source",
            raw.get("id"),
            {"name": raw.get("name"), "url": url},
        )
        records["event_sources"][key] = _record_fingerprint(
            {
                "canonical_url": url,
                "source_kind": raw.get("source_kind"),
                "country_code": raw.get("country_code"),
                "status": raw.get("status"),
                "publisher_tier": raw.get("publisher_tier"),
                "publisher_identity_evidence": raw.get("publisher_identity_evidence"),
                "source_checked_at": raw.get("source_checked_at"),
            }
        )
    opportunities = catalog.get("opportunities", [])
    for raw in opportunities if isinstance(opportunities, list) else []:
        if not isinstance(raw, Mapping):
            continue
        official_url = canonical_source_url(raw.get("official_url"))
        key = _snapshot_key(
            "event_opportunity",
            raw.get("id"),
            {
                "canonical_key": raw.get("canonical_key"),
                "official_url": official_url,
            },
        )
        records["event_opportunities"][key] = _record_fingerprint(
            {
                "canonical_key": raw.get("canonical_key"),
                "source_id": raw.get("source_id"),
                "external_event_key": raw.get("external_event_key"),
                "lane": raw.get("lane"),
                "title": raw.get("title"),
                "start_date": raw.get("start_date"),
                "end_date": raw.get("end_date"),
                "country_code": raw.get("country_code"),
                "official_url": official_url,
                "verification_status": raw.get("verification_status"),
                "source_checked_at": raw.get("source_checked_at"),
                "dealer_stable_location_key": raw.get("dealer_stable_location_key"),
                "viltrox_presence_status": raw.get("viltrox_presence_status"),
                "viltrox_evidence": raw.get("viltrox_evidence"),
            }
        )
    for raw in dealers:
        if not isinstance(raw, Mapping):
            continue
        location_url = canonical_source_url(raw.get("location_source_url"))
        key = _snapshot_key(
            "dealer_location",
            raw.get("stable_location_key"),
            {
                "name": raw.get("name"),
                "address": raw.get("address"),
                "country": raw.get("country_code") or raw.get("country"),
            },
        )
        records["dealer_locations"][key] = _record_fingerprint(
            {
                "source_id": raw.get("source_id"),
                "stable_org_key": raw.get("stable_org_key"),
                "stable_location_key": raw.get("stable_location_key"),
                "name": raw.get("name"),
                "address": raw.get("address"),
                "country_code": raw.get("country_code") or raw.get("country"),
                "location_source_url": location_url,
                "brand_listing_url": canonical_source_url(raw.get("brand_listing_url")),
                "publisher_tier": raw.get("publisher_tier"),
                "publisher_identity_evidence": raw.get("publisher_identity_evidence"),
                "source_checked_at": raw.get("source_checked_at"),
                "contact_values": {field: raw.get(field) for field in _CONTACT_FIELDS},
                "contact_evidence": raw.get("contact_evidence"),
                "social_evidence": raw.get("social_evidence"),
                "viltrox_product_evidence": raw.get("viltrox_product_evidence"),
                "activity_evidence": raw.get("activity_evidence"),
            }
        )
    snapshot_payload = {
        "contract_id": CONTRACT_ID,
        "snapshot_version": SNAPSHOT_VERSION,
        "records": records,
    }
    return {
        **snapshot_payload,
        "generated_at": generated_at.isoformat(),
        "snapshot_sha256": canonical_json_sha256(snapshot_payload),
    }


def compare_source_snapshots(
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare content hashes and report field names, never raw field values."""
    if previous is None:
        return {
            "status": "baseline_unavailable",
            "previous_snapshot_sha256": None,
            "current_snapshot_sha256": current.get("snapshot_sha256"),
            "scopes": {},
            "identity_drift": [],
        }
    if (
        previous.get("contract_id") != CONTRACT_ID
        or previous.get("snapshot_version") != SNAPSHOT_VERSION
        or not isinstance(previous.get("records"), Mapping)
    ):
        return {
            "status": "invalid_previous_snapshot",
            "previous_snapshot_sha256": previous.get("snapshot_sha256"),
            "current_snapshot_sha256": current.get("snapshot_sha256"),
            "scopes": {},
            "identity_drift": [],
        }
    scopes: dict[str, Any] = {}
    identity_drift: list[dict[str, Any]] = []
    current_records = current.get("records")
    previous_records = previous.get("records")
    if not isinstance(current_records, Mapping) or not isinstance(previous_records, Mapping):
        return {
            "status": "invalid_snapshot_records",
            "previous_snapshot_sha256": previous.get("snapshot_sha256"),
            "current_snapshot_sha256": current.get("snapshot_sha256"),
            "scopes": {},
            "identity_drift": [],
        }
    for scope in ("event_sources", "event_opportunities", "dealer_locations"):
        current_scope = current_records.get(scope, {})
        previous_scope = previous_records.get(scope, {})
        if not isinstance(current_scope, Mapping) or not isinstance(previous_scope, Mapping):
            return {
                "status": "invalid_snapshot_scope",
                "previous_snapshot_sha256": previous.get("snapshot_sha256"),
                "current_snapshot_sha256": current.get("snapshot_sha256"),
                "scopes": {},
                "identity_drift": [],
            }
        current_keys = set(current_scope)
        previous_keys = set(previous_scope)
        changed: list[dict[str, Any]] = []
        for key in sorted(current_keys & previous_keys):
            current_record = current_scope[key]
            previous_record = previous_scope[key]
            if not isinstance(current_record, Mapping) or not isinstance(previous_record, Mapping):
                continue
            if current_record.get("record_sha256") == previous_record.get("record_sha256"):
                continue
            current_fields = current_record.get("field_sha256", {})
            previous_fields = previous_record.get("field_sha256", {})
            if not isinstance(current_fields, Mapping) or not isinstance(previous_fields, Mapping):
                field_names: list[str] = []
            else:
                field_names = sorted(
                    field
                    for field in set(current_fields) | set(previous_fields)
                    if current_fields.get(field) != previous_fields.get(field)
                )
            changed.append({"entity_key": key, "changed_fields": field_names})
            identity_fields = {
                "event_sources": {"canonical_url"},
                "event_opportunities": {"canonical_key", "official_url", "source_id"},
                "dealer_locations": {
                    "stable_location_key",
                    "location_source_url",
                    "source_id",
                },
            }[scope]
            drift_fields = sorted(identity_fields.intersection(field_names))
            if drift_fields:
                identity_drift.append(
                    {
                        "scope": scope,
                        "entity_key": key,
                        "changed_fields": drift_fields,
                    }
                )
        scopes[scope] = {
            "previous": len(previous_keys),
            "current": len(current_keys),
            "added": sorted(current_keys - previous_keys),
            "removed": sorted(previous_keys - current_keys),
            "changed": changed,
            "unchanged": len(current_keys & previous_keys) - len(changed),
        }
    return {
        "status": "compared",
        "previous_snapshot_sha256": previous.get("snapshot_sha256"),
        "current_snapshot_sha256": current.get("snapshot_sha256"),
        "scopes": scopes,
        "identity_drift": identity_drift,
    }


__all__ = ["build_source_snapshot", "compare_source_snapshots"]
