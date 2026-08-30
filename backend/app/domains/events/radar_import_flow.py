"""Pure decisions and projections for the reviewed Event Radar import.

The database statements stay in :mod:`radar_import` so migration-contract
tests continue to inspect the implementation that owns those effects.  This
module contains the deterministic transformations used by that adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


REQUIRED_TABLES = (
    "vkpi_event_watch_targets",
    "vkpi_event_source_runs",
    "vkpi_event_opportunities",
    "vkpi_event_source_observations",
    "vkpi_event_opportunity_changes",
    "vkpi_event_opportunity_dealers",
    "vkpi_dealers",
    "vkpi_dealer_identity_aliases",
)


@dataclass(frozen=True)
class ImportDependencies:
    """Eager adapter for the compatibility wrapper's monkeypatch seams."""

    preview_reviewed_catalog: Callable[[], dict[str, Any]]
    load_reviewed_catalog: Callable[[], dict[str, Any]]
    require_organization_schema: Callable[[], Any]
    table_exists: Callable[[str], bool]
    organization_id: Callable[..., int]
    json_dumps: Callable[[Any], str]
    row: Callable[[Any], dict[str, Any]]
    normalize_title: Callable[[Any], str]
    content_hash: Callable[[dict[str, Any]], str]
    changed_fields: Callable[[dict[str, Any], dict[str, Any]], list[str]]
    source_columns: Iterable[str]

    @classmethod
    def from_object(cls, deps: Any) -> "ImportDependencies":
        # Resolve every attribute eagerly, matching the old facade's dependency
        # access and preserving the existing SimpleNamespace/monkeypatch API.
        return cls(
            preview_reviewed_catalog=deps.preview_reviewed_catalog,
            load_reviewed_catalog=deps.load_reviewed_catalog,
            require_organization_schema=deps.require_organization_schema,
            table_exists=deps.table_exists,
            organization_id=deps.organization_id,
            json_dumps=deps.json_dumps,
            row=deps.row,
            normalize_title=deps.normalize_title,
            content_hash=deps.content_hash,
            changed_fields=deps.changed_fields,
            source_columns=deps.source_columns,
        )


@dataclass(frozen=True)
class OpportunityDraft:
    item: dict[str, Any]
    checked_at: Any
    review_evidence: dict[str, Any]
    content_hash: str


@dataclass(frozen=True)
class OpportunityDecision:
    draft: OpportunityDraft
    old: dict[str, Any]
    changed_fields: list[str]
    hash_changed: bool
    approval_invalidated: bool

    @property
    def is_new(self) -> bool:
        return not self.old

    @property
    def requires_change_record(self) -> bool:
        return self.is_new or self.hash_changed


@dataclass
class ImportStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    observations: int = 0
    changes: int = 0
    invalidated_approvals: int = 0

    def record_opportunity(self, decision: OpportunityDecision) -> None:
        if decision.is_new:
            self.inserted += 1
        elif decision.hash_changed:
            self.updated += 1
        else:
            self.unchanged += 1
        if decision.approval_invalidated:
            self.invalidated_approvals += 1


def require_import_allowed(preview: dict[str, Any]) -> None:
    if not preview["ok"] or not preview["quality_contract"]["import_gate"]["allowed"]:
        raise ValueError("event radar catalog validation failed")


def require_import_tables(table_exists: Callable[[str], bool]) -> None:
    for required in REQUIRED_TABLES:
        if not table_exists(required):
            raise RuntimeError("event radar migrations 243/244 are not applied")


def source_values(
    source: dict[str, Any],
    source_columns: Iterable[str],
    json_dumps: Callable[[Any], str],
) -> tuple[list[Any], Any]:
    checked_at = source.get("source_checked_at")
    review_evidence = {
        "reviewer_id": source.get("reviewer_id"),
        "evidence_scope": source.get("evidence_scope"),
        "value_status": source.get("value_status"),
        "checked_at": checked_at,
        "observed_at": checked_at,
        "source_url": source.get("canonical_url"),
        "review_status": "quality_contract_accepted",
    }
    values: list[Any] = []
    for key in source_columns:
        values.append(_source_value(source, key, review_evidence, json_dumps))
    return values, checked_at


def _source_value(
    source: dict[str, Any],
    key: str,
    review_evidence: dict[str, Any],
    json_dumps: Callable[[Any], str],
) -> Any:
    if key == "metadata_json":
        raw_metadata = source.get(key)
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        metadata["review_evidence"] = review_evidence
        return json_dumps(metadata)
    if key == "parser_profile":
        return source.get(key) or "manual_reviewed_v1"
    if key == "terms_robots_status":
        return source.get(key) or "unknown"
    if key == "enabled":
        return bool(source.get(key, True)) and str(source.get("status") or "active") == "active"
    if key == "requires_human_review":
        return bool(source.get(key, False))
    if key == "priority_tier":
        return int(source.get(key, 2))
    return source.get(key) or ""


def prepare_opportunity(
    raw_item: dict[str, Any],
    content_hash: Callable[[dict[str, Any]], str],
) -> OpportunityDraft:
    item = dict(raw_item)
    item.setdefault("date_precision", "date")
    item.setdefault("is_online", False)
    item.setdefault("registration_url", "")
    item.setdefault("event_status", "scheduled")
    item.setdefault("evidence_grade", "A2")
    item.setdefault("verification_status", "needs_review")
    item.setdefault("confidence", 0)
    item.setdefault("relevance_score", None)
    item.setdefault("relevance_basis", "")
    item.setdefault("viltrox_presence_status", "unknown")
    item.setdefault("viltrox_evidence_url", "")
    checked_at = item.get("source_checked_at")
    review_evidence = {
        "reviewer_id": item.get("reviewer_id"),
        "evidence_scope": item.get("evidence_scope"),
        "value_status": item.get("value_status"),
        "checked_at": checked_at,
        "observed_at": checked_at,
        "source_url": item.get("official_url"),
        "review_status": "quality_contract_accepted",
    }
    return OpportunityDraft(
        item=item,
        checked_at=checked_at,
        review_evidence=review_evidence,
        content_hash=content_hash(item),
    )


def decide_opportunity(
    draft: OpportunityDraft,
    old: dict[str, Any],
    *,
    changed_fields: Callable[[dict[str, Any], dict[str, Any]], list[str]],
    metadata_dict: Callable[[Any], dict[str, Any]],
) -> OpportunityDecision:
    old_for_comparison = dict(old)
    if old:
        old_for_comparison["dealer_stable_location_key"] = metadata_dict(
            old.get("metadata_json")
        ).get("dealer_stable_location_key", old.get("dealer_stable_location_key"))
    changed = changed_fields(old_for_comparison, draft.item) if old else []
    hash_changed = bool(old) and str(old.get("content_hash") or "") != draft.content_hash
    approval_invalidated = bool(
        hash_changed and str(old.get("decision_status") or "") == "approved"
    )
    return OpportunityDecision(
        draft=draft,
        old=old,
        changed_fields=changed,
        hash_changed=hash_changed,
        approval_invalidated=approval_invalidated,
    )


def opportunity_metadata(
    decision: OpportunityDecision,
    *,
    catalog_version: Any,
    json_dumps: Callable[[Any], str],
) -> str:
    item = decision.draft.item
    return json_dumps(
        {
            "catalog_version": catalog_version,
            "dealer_match_name": item.get("dealer_match_name") or "",
            "dealer_stable_location_key": item.get("dealer_stable_location_key") or "",
            "review_evidence": decision.draft.review_evidence,
            "viltrox_evidence": item.get("viltrox_evidence") or {},
        }
    )


def opportunity_upsert_params(
    decision: OpportunityDecision,
    *,
    organization_id: int,
    catalog_version: Any,
    normalize_title: Callable[[Any], str],
    json_dumps: Callable[[Any], str],
) -> tuple[Any, ...]:
    draft = decision.draft
    item = draft.item
    return (
        organization_id,
        item.get("id"),
        item.get("canonical_key"),
        item.get("source_id"),
        item.get("external_event_key"),
        item.get("lane"),
        item.get("title"),
        normalize_title(item.get("title")),
        item.get("organizer") or "",
        item.get("start_date") or None,
        item.get("end_date") or None,
        item.get("timezone") or "UTC",
        item.get("local_time_text") or "",
        item.get("date_precision"),
        item.get("venue") or "",
        item.get("address") or "",
        item.get("city") or "",
        item.get("region") or "",
        item.get("country_code") or "",
        bool(item.get("is_online")),
        item.get("official_url"),
        item.get("registration_url") or "",
        item.get("event_status"),
        item.get("evidence_grade"),
        item.get("verification_status"),
        float(item.get("confidence") or 0),
        item.get("relevance_score"),
        item.get("relevance_basis") or "",
        item.get("viltrox_presence_status"),
        item.get("viltrox_evidence_url") or "",
        draft.checked_at,
        draft.checked_at,
        draft.checked_at,
        draft.content_hash,
        opportunity_metadata(
            decision,
            catalog_version=catalog_version,
            json_dumps=json_dumps,
        ),
    )


def observation_payload(decision: OpportunityDecision) -> dict[str, Any]:
    item = decision.draft.item
    return {
        **item,
        "source_url": item.get("official_url"),
        "observed_at": decision.draft.checked_at,
        "review_status": "quality_contract_accepted",
    }


def observation_identity_kwargs(decision: OpportunityDecision) -> dict[str, Any]:
    item = decision.draft.item
    return {
        "opportunity_content_hash": decision.draft.content_hash,
        "source_url": item.get("official_url"),
        "observed_at": decision.draft.checked_at,
        "review_status": "quality_contract_accepted",
        "reviewer_id": item.get("reviewer_id"),
        "evidence_scope": item.get("evidence_scope"),
        "value_status": item.get("value_status"),
        "dealer_stable_location_key": item.get("dealer_stable_location_key"),
    }


def change_fields(decision: OpportunityDecision) -> list[str]:
    fields = ["initial_observation"] if decision.is_new else list(decision.changed_fields)
    if decision.approval_invalidated and "decision_status" not in fields:
        fields.append("decision_status")
    return fields


def change_kind(decision: OpportunityDecision) -> str:
    if decision.is_new:
        return "discovered"
    if decision.approval_invalidated:
        return "approval_invalidated"
    return "source_update"


def result_projection(
    preview: dict[str, Any],
    *,
    discovered: int,
    run_key: str,
    stats: ImportStats,
) -> dict[str, Any]:
    return {
        **preview,
        "record_only": False,
        "discovered": discovered,
        "run_key": run_key,
        "inserted": stats.inserted,
        "updated": stats.updated,
        "unchanged": stats.unchanged,
        "observations_inserted": stats.observations,
        "changes_inserted": stats.changes,
        "invalidated_approvals": stats.invalidated_approvals,
    }
