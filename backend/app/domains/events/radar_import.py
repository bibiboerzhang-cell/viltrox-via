"""Transactional import for the reviewed Event Radar catalog.

Kept separate from the read/decision service so the atomic import workflow can
evolve without turning the public service module into a monolith. Dependencies
are injected by the compatibility wrapper, preserving existing test seams.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from app.domains.events import radar_import_flow as flow


def observation_identity_hash(
    *,
    opportunity_content_hash: str,
    source_url: Any,
    observed_at: Any,
    review_status: Any,
    reviewer_id: Any,
    evidence_scope: Any,
    value_status: Any,
    dealer_stable_location_key: Any,
) -> str:
    """Bind immutable observation identity to provenance and review time.

    Opportunity content and observation evidence have different lifecycles: a
    current re-review should produce a new observation without pretending that
    the event's business fields changed or invalidating an approval by itself.
    """
    payload = {
        "opportunity_content_hash": str(opportunity_content_hash or ""),
        "source_url": str(source_url or "").strip(),
        "observed_at": str(observed_at or "").strip(),
        "review_status": str(review_status or "").strip(),
        "reviewer_id": str(reviewer_id or "").strip(),
        "evidence_scope": str(evidence_scope or "").strip(),
        "value_status": str(value_status or "").strip(),
        "dealer_stable_location_key": str(dealer_stable_location_key or "").strip(),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_dict(value: Any) -> dict[str, Any]:
    """Decode persisted opportunity metadata for truth-field comparison."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _create_source_run(
    conn: Any,
    *,
    organization_id: int,
    run_key: str,
    data: dict[str, Any],
    deps: flow.ImportDependencies,
) -> int:
    run_row = conn.execute(
        """
        INSERT INTO vkpi_event_source_runs
          (organization_id, run_key, run_kind, status, record_only, discovered_count, metadata_json)
        VALUES (?, ?, 'reviewed_seed', 'running', FALSE, ?, ?::jsonb)
        RETURNING id
        """,
        (
            organization_id,
            run_key,
            len(data["opportunities"]),
            deps.json_dumps({"catalog_version": data.get("catalog_version")}),
        ),
    ).fetchone()
    return int(deps.row(run_row).get("id"))


def _upsert_sources(
    conn: Any,
    sources: list[dict[str, Any]],
    deps: flow.ImportDependencies,
) -> None:
    for source in sources:
        values, checked_at = flow.source_values(
            source,
            deps.source_columns,
            deps.json_dumps,
        )
        # Reviewed active sources default to enabled, while an explicit false
        # remains authoritative.  This prevents fail-closed hiding of the
        # reviewed catalog (verified by the 2026-07-17 replay).
        conn.execute(
            """
            INSERT INTO vkpi_event_watch_targets
              (id, name, source_kind, country_code, region, timezone, canonical_url, discovery_url,
               fetch_mode, parser_profile, evidence_grade, priority_tier, refresh_policy,
               requires_human_review, terms_robots_status, status, enabled, metadata_json,
               last_checked_at, last_success_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,?,NULL, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE SET
              name=excluded.name, source_kind=excluded.source_kind, country_code=excluded.country_code,
              region=excluded.region, timezone=excluded.timezone, canonical_url=excluded.canonical_url,
              discovery_url=excluded.discovery_url, fetch_mode=excluded.fetch_mode,
              parser_profile=excluded.parser_profile, evidence_grade=excluded.evidence_grade,
              priority_tier=excluded.priority_tier, refresh_policy=excluded.refresh_policy,
              requires_human_review=excluded.requires_human_review,
              terms_robots_status=excluded.terms_robots_status, status=excluded.status,
              enabled=CASE WHEN excluded.status='active' THEN excluded.enabled ELSE FALSE END,
              metadata_json=excluded.metadata_json, last_checked_at=excluded.last_checked_at,
              last_error='', updated_at=NOW()
            """,
            (source.get("id"), *values, checked_at),
        )


def _find_existing_opportunity(
    conn: Any,
    decision_input: flow.OpportunityDraft,
    *,
    organization_id: int,
    row: Any,
) -> dict[str, Any]:
    item = decision_input.item
    old_row = conn.execute(
        "SELECT * FROM vkpi_event_opportunities WHERE canonical_key = ? AND organization_id = ?",
        (item.get("canonical_key"), organization_id),
    ).fetchone()
    if old_row is None:
        # canonical_key drift fallback: the reviewed catalog id is the stable
        # identity when a corrected date changes the derived canonical key.
        old_row = conn.execute(
            "SELECT * FROM vkpi_event_opportunities WHERE id = ? AND organization_id = ?",
            (item.get("id"), organization_id),
        ).fetchone()
        if old_row is not None:
            conn.execute(
                "UPDATE vkpi_event_opportunities SET canonical_key = ?, updated_at = NOW() "
                "WHERE id = ? AND organization_id = ?",
                (item.get("canonical_key"), item.get("id"), organization_id),
            )
    return row(old_row)


def _upsert_opportunity(
    conn: Any,
    decision: flow.OpportunityDecision,
    *,
    organization_id: int,
    catalog_version: Any,
    deps: flow.ImportDependencies,
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_event_opportunities
          (organization_id, id, canonical_key, source_id, external_event_key, lane, title, normalized_title,
           organizer, start_date, end_date, timezone, local_time_text, date_precision, venue,
           address, city, region, country_code, is_online, official_url, registration_url,
           event_status, decision_status, evidence_grade, verification_status, confidence,
           relevance_score, relevance_basis, viltrox_presence_status, viltrox_evidence_url,
           source_checked_at, first_seen_at, last_seen_at, last_verified_at, content_hash,
           metadata_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new',?,?,?,?,?,?,?,?, NOW(), ?,?,?,?::jsonb, NOW(), NOW())
        ON CONFLICT (organization_id, canonical_key) DO UPDATE SET
          source_id=excluded.source_id, external_event_key=excluded.external_event_key,
          lane=excluded.lane, title=excluded.title, normalized_title=excluded.normalized_title,
          organizer=excluded.organizer, start_date=excluded.start_date, end_date=excluded.end_date,
          timezone=excluded.timezone, local_time_text=excluded.local_time_text,
          date_precision=excluded.date_precision, venue=excluded.venue, address=excluded.address,
          city=excluded.city, region=excluded.region, country_code=excluded.country_code,
          is_online=excluded.is_online, official_url=excluded.official_url,
          registration_url=excluded.registration_url, event_status=excluded.event_status,
          decision_status=CASE
            WHEN vkpi_event_opportunities.decision_status='approved'
             AND COALESCE(vkpi_event_opportunities.content_hash, '')<>COALESCE(excluded.content_hash, '')
            THEN 'needs_review' ELSE vkpi_event_opportunities.decision_status END,
          decision_note=CASE
            WHEN vkpi_event_opportunities.decision_status='approved'
             AND COALESCE(vkpi_event_opportunities.content_hash, '')<>COALESCE(excluded.content_hash, '')
            THEN '' ELSE vkpi_event_opportunities.decision_note END,
          decision_by=CASE
            WHEN vkpi_event_opportunities.decision_status='approved'
             AND COALESCE(vkpi_event_opportunities.content_hash, '')<>COALESCE(excluded.content_hash, '')
            THEN NULL ELSE vkpi_event_opportunities.decision_by END,
          decision_at=CASE
            WHEN vkpi_event_opportunities.decision_status='approved'
             AND COALESCE(vkpi_event_opportunities.content_hash, '')<>COALESCE(excluded.content_hash, '')
            THEN NULL ELSE vkpi_event_opportunities.decision_at END,
          evidence_grade=excluded.evidence_grade, verification_status=excluded.verification_status,
          confidence=excluded.confidence, relevance_score=excluded.relevance_score,
          relevance_basis=excluded.relevance_basis,
          viltrox_presence_status=excluded.viltrox_presence_status,
          viltrox_evidence_url=excluded.viltrox_evidence_url,
          source_checked_at=excluded.source_checked_at, last_seen_at=NOW(),
          last_verified_at=excluded.last_verified_at, content_hash=excluded.content_hash,
          metadata_json=excluded.metadata_json, updated_at=NOW()
        """,
        flow.opportunity_upsert_params(
            decision,
            organization_id=organization_id,
            catalog_version=catalog_version,
            normalize_title=deps.normalize_title,
            json_dumps=deps.json_dumps,
        ),
    )


def _project_opportunity_id(
    conn: Any,
    decision: flow.OpportunityDecision,
    *,
    organization_id: int,
    row: Any,
) -> str:
    current = conn.execute(
        "SELECT id FROM vkpi_event_opportunities WHERE canonical_key = ? AND organization_id = ?",
        (decision.draft.item.get("canonical_key"), organization_id),
    ).fetchone()
    return str(row(current).get("id"))


def _store_observation(
    conn: Any,
    decision: flow.OpportunityDecision,
    *,
    organization_id: int,
    run_id: int,
    opportunity_id: str,
    deps: flow.ImportDependencies,
) -> tuple[Any, bool]:
    item = decision.draft.item
    observation_hash = observation_identity_hash(
        **flow.observation_identity_kwargs(decision)
    )
    observation = conn.execute(
        """
        INSERT INTO vkpi_event_source_observations
          (organization_id, source_id, run_id, opportunity_id, external_event_key, source_url, content_hash,
           observed_at, extracted_json, extractor)
        VALUES (?,?,?,?,?,?,?,?,?::jsonb,'manual_reviewed_v1')
        ON CONFLICT (organization_id, source_id, external_event_key, content_hash) DO NOTHING
        RETURNING id
        """,
        (
            organization_id,
            item.get("source_id"),
            run_id,
            opportunity_id,
            item.get("external_event_key") or "",
            item.get("official_url"),
            observation_hash,
            decision.draft.checked_at,
            deps.json_dumps(flow.observation_payload(decision)),
        ),
    ).fetchone()
    observation_id = deps.row(observation).get("id")
    if observation_id:
        return observation_id, True
    existing = conn.execute(
        "SELECT id FROM vkpi_event_source_observations WHERE organization_id=? AND source_id=? AND external_event_key=? AND content_hash=?",
        (
            organization_id,
            item.get("source_id"),
            item.get("external_event_key") or "",
            observation_hash,
        ),
    ).fetchone()
    return deps.row(existing).get("id"), False


def _record_change(
    conn: Any,
    decision: flow.OpportunityDecision,
    *,
    organization_id: int,
    opportunity_id: str,
    observation_id: Any,
    json_dumps: Any,
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_event_opportunity_changes
          (organization_id, opportunity_id, observation_id, change_kind, before_hash, after_hash, changed_fields)
        VALUES (?,?,?,?,?,?,?::jsonb)
        """,
        (
            organization_id,
            opportunity_id,
            observation_id,
            flow.change_kind(decision),
            str(decision.old.get("content_hash") or ""),
            decision.draft.content_hash,
            json_dumps(flow.change_fields(decision)),
        ),
    )


def _sync_dealer_host(
    conn: Any,
    decision: flow.OpportunityDecision,
    *,
    organization_id: int,
    opportunity_id: str,
    row: Any,
) -> None:
    location_key = str(
        decision.draft.item.get("dealer_stable_location_key") or ""
    ).strip()
    if not location_key:
        conn.execute(
            """
            DELETE FROM vkpi_event_opportunity_dealers
            WHERE organization_id=? AND opportunity_id=? AND relation_type='host'
            """,
            (organization_id, opportunity_id),
        )
        return
    dealer = conn.execute(
        """
        SELECT CASE
                 WHEN COUNT(DISTINCT dealer_id)=1 THEN MIN(dealer_id)
                 ELSE NULL
               END AS id
        FROM vkpi_dealer_identity_aliases
        WHERE organization_id=? AND stable_location_key=? AND verified_at IS NOT NULL
        """,
        (organization_id, location_key),
    ).fetchone()
    dealer_id = row(dealer).get("id")
    if dealer_id is None:
        raise ValueError(
            "dealer_stable_location_key must resolve to exactly one reviewed Dealer"
        )
    conn.execute(
        """
        DELETE FROM vkpi_event_opportunity_dealers
        WHERE organization_id=? AND opportunity_id=? AND relation_type='host' AND dealer_id<>?
        """,
        (organization_id, opportunity_id, dealer_id),
    )
    conn.execute(
        """
        INSERT INTO vkpi_event_opportunity_dealers(organization_id, opportunity_id, dealer_id, relation_type)
        VALUES (?,?,?,'host') ON CONFLICT DO NOTHING
        """,
        (organization_id, opportunity_id, dealer_id),
    )


def _import_opportunity(
    conn: Any,
    raw_item: dict[str, Any],
    *,
    organization_id: int,
    run_id: int,
    catalog_version: Any,
    stats: flow.ImportStats,
    deps: flow.ImportDependencies,
) -> None:
    draft = flow.prepare_opportunity(raw_item, deps.content_hash)
    old = _find_existing_opportunity(
        conn,
        draft,
        organization_id=organization_id,
        row=deps.row,
    )
    decision = flow.decide_opportunity(
        draft,
        old,
        changed_fields=deps.changed_fields,
        metadata_dict=_metadata_dict,
    )
    stats.record_opportunity(decision)
    _upsert_opportunity(
        conn,
        decision,
        organization_id=organization_id,
        catalog_version=catalog_version,
        deps=deps,
    )
    opportunity_id = _project_opportunity_id(
        conn,
        decision,
        organization_id=organization_id,
        row=deps.row,
    )
    observation_id, observation_inserted = _store_observation(
        conn,
        decision,
        organization_id=organization_id,
        run_id=run_id,
        opportunity_id=opportunity_id,
        deps=deps,
    )
    if observation_inserted:
        stats.observations += 1
    if decision.requires_change_record:
        _record_change(
            conn,
            decision,
            organization_id=organization_id,
            opportunity_id=opportunity_id,
            observation_id=observation_id,
            json_dumps=deps.json_dumps,
        )
        stats.changes += 1
    _sync_dealer_host(
        conn,
        decision,
        organization_id=organization_id,
        opportunity_id=opportunity_id,
        row=deps.row,
    )


def _finish_source_run(
    conn: Any,
    *,
    run_id: int,
    stats: flow.ImportStats,
    json_dumps: Any,
) -> None:
    conn.execute(
        """
        UPDATE vkpi_event_source_runs
        SET status='succeeded', finished_at=NOW(), inserted_count=?, updated_count=?,
            unchanged_count=?, metadata_json = metadata_json || ?::jsonb
        WHERE id=?
        """,
        (
            stats.inserted,
            stats.updated,
            stats.unchanged,
            json_dumps(
                {
                    "observations": stats.observations,
                    "changes": stats.changes,
                    "invalidated_approvals": stats.invalidated_approvals,
                }
            ),
            run_id,
        ),
    )


def _execute_import(
    conn: Any,
    *,
    organization_id: int,
    run_key: str,
    data: dict[str, Any],
    deps: flow.ImportDependencies,
) -> flow.ImportStats:
    stats = flow.ImportStats()
    run_id = _create_source_run(
        conn,
        organization_id=organization_id,
        run_key=run_key,
        data=data,
        deps=deps,
    )
    _upsert_sources(conn, data["sources"], deps)
    for item in data["opportunities"]:
        _import_opportunity(
            conn,
            item,
            organization_id=organization_id,
            run_id=run_id,
            catalog_version=data.get("catalog_version"),
            stats=stats,
            deps=deps,
        )
    _finish_source_run(
        conn,
        run_id=run_id,
        stats=stats,
        json_dumps=deps.json_dumps,
    )
    return stats


def import_reviewed_catalog(
    *,
    record_only: bool = True,
    organization_id: int = 1,
    deps: Any,
) -> dict[str, Any]:
    """Idempotently import the reviewed seed catalog; preview is the safe default."""
    runtime = flow.ImportDependencies.from_object(deps)
    preview = runtime.preview_reviewed_catalog()
    if record_only is not False:
        return preview
    flow.require_import_allowed(preview)
    conn = runtime.require_organization_schema()
    flow.require_import_tables(runtime.table_exists)
    org_id = runtime.organization_id(explicit=organization_id)
    data = runtime.load_reviewed_catalog()
    run_key = f"event-radar-seed-{data.get('catalog_version')}-{uuid.uuid4().hex[:8]}"
    try:
        stats = _execute_import(
            conn,
            organization_id=org_id,
            run_key=run_key,
            data=data,
            deps=runtime,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return flow.result_projection(
        preview,
        discovered=len(data["opportunities"]),
        run_key=run_key,
        stats=stats,
    )
