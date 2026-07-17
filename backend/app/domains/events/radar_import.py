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


def import_reviewed_catalog(*, record_only: bool = True, organization_id: int = 1, deps: Any) -> dict[str, Any]:
    """Idempotently import the reviewed seed catalog; preview is the safe default."""
    preview_reviewed_catalog = deps.preview_reviewed_catalog
    load_reviewed_catalog = deps.load_reviewed_catalog
    _require_organization_schema = deps.require_organization_schema
    table_exists = deps.table_exists
    _organization_id = deps.organization_id
    _json = deps.json_dumps
    _row = deps.row
    _normalize_title = deps.normalize_title
    _content_hash = deps.content_hash
    _changed_fields = deps.changed_fields
    _SOURCE_COLUMNS = deps.source_columns

    preview = preview_reviewed_catalog()
    if record_only is not False:
        return preview
    if not preview["ok"] or not preview["quality_contract"]["import_gate"]["allowed"]:
        raise ValueError("event radar catalog validation failed")
    conn = _require_organization_schema()
    for required in (
        "vkpi_event_watch_targets", "vkpi_event_source_runs", "vkpi_event_opportunities",
        "vkpi_event_source_observations", "vkpi_event_opportunity_changes",
        "vkpi_event_opportunity_dealers", "vkpi_dealers", "vkpi_dealer_identity_aliases",
    ):
        if not table_exists(required):
            raise RuntimeError("event radar migrations 243/244 are not applied")

    org_id = _organization_id(explicit=organization_id)
    data = load_reviewed_catalog()
    run_key = f"event-radar-seed-{data.get('catalog_version')}-{uuid.uuid4().hex[:8]}"
    inserted = 0
    updated = 0
    unchanged = 0
    observations = 0
    changes = 0
    invalidated_approvals = 0
    try:
        run_row = conn.execute(
            """
            INSERT INTO vkpi_event_source_runs
              (organization_id, run_key, run_kind, status, record_only, discovered_count, metadata_json)
            VALUES (?, ?, 'reviewed_seed', 'running', FALSE, ?, ?::jsonb)
            RETURNING id
            """,
            (org_id, run_key, len(data["opportunities"]), _json({"catalog_version": data.get("catalog_version")})),
        ).fetchone()
        run_id = int(_row(run_row).get("id"))

        for source in data["sources"]:
            source_checked_at = source.get("source_checked_at")
            source_review_evidence = {
                "reviewer_id": source.get("reviewer_id"),
                "evidence_scope": source.get("evidence_scope"),
                "value_status": source.get("value_status"),
                "checked_at": source_checked_at,
                "observed_at": source_checked_at,
                "source_url": source.get("canonical_url"),
                "review_status": "quality_contract_accepted",
            }
            values = []
            for key in _SOURCE_COLUMNS:
                if key == "metadata_json":
                    raw_metadata = source.get(key)
                    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                    metadata["review_evidence"] = source_review_evidence
                    values.append(_json(metadata))
                elif key == "parser_profile":
                    values.append(source.get(key) or "manual_reviewed_v1")
                elif key == "terms_robots_status":
                    values.append(source.get(key) or "unknown")
                elif key == "enabled":
                    # 目录是人工审阅过的:status=active 即默认启用(显式 enabled:false 仍尊重)。
                    # 此前缺省 False 使 72 个目录来源全部禁用,21 条已入库机会被
                    # fail-closed 过滤整体隐藏(2026-07-17 线上实证)。
                    values.append(bool(source.get(key, True)) and str(source.get("status") or "active") == "active")
                elif key == "requires_human_review":
                    values.append(bool(source.get(key, False)))
                elif key == "priority_tier":
                    values.append(int(source.get(key, 2)))
                else:
                    values.append(source.get(key) or "")
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
                (source.get("id"), *values, source_checked_at),
            )

        for item in data["opportunities"]:
            item = dict(item)
            opportunity_checked_at = item.get("source_checked_at")
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
            opportunity_review_evidence = {
                "reviewer_id": item.get("reviewer_id"),
                "evidence_scope": item.get("evidence_scope"),
                "value_status": item.get("value_status"),
                "checked_at": opportunity_checked_at,
                "observed_at": opportunity_checked_at,
                "source_url": item.get("official_url"),
                "review_status": "quality_contract_accepted",
            }
            item_hash = _content_hash(item)
            old_row = conn.execute(
                "SELECT * FROM vkpi_event_opportunities WHERE canonical_key = ? AND organization_id = ?",
                (item.get("canonical_key"), org_id),
            ).fetchone()
            if old_row is None:
                # canonical_key 漂移兜底(2026-07-17 重放实证):目录内容修订(如日期
                # 更正)会换 canonical_key,按 key 找不到同 id 旧行→INSERT 撞 PK。
                # 回落到目录身份键(id)找旧行,并先把 canonical_key 归位到新值,
                # 之后仍走原 canonical upsert(审计/失效语义不变)。
                old_row = conn.execute(
                    "SELECT * FROM vkpi_event_opportunities WHERE id = ? AND organization_id = ?",
                    (item.get("id"), org_id),
                ).fetchone()
                if old_row is not None:
                    conn.execute(
                        "UPDATE vkpi_event_opportunities SET canonical_key = ?, updated_at = NOW() "
                        "WHERE id = ? AND organization_id = ?",
                        (item.get("canonical_key"), item.get("id"), org_id),
                    )
            old = _row(old_row)
            old_for_comparison = dict(old)
            if old:
                old_for_comparison["dealer_stable_location_key"] = _metadata_dict(
                    old.get("metadata_json")
                ).get("dealer_stable_location_key", old.get("dealer_stable_location_key"))
            changed_fields = _changed_fields(old_for_comparison, item) if old else []
            hash_changed = bool(old) and str(old.get("content_hash") or "") != item_hash
            approval_invalidated = bool(
                hash_changed and str(old.get("decision_status") or "") == "approved"
            )
            if not old:
                inserted += 1
            elif hash_changed:
                updated += 1
            else:
                unchanged += 1
            if approval_invalidated:
                invalidated_approvals += 1

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
                (
                    org_id, item.get("id"), item.get("canonical_key"), item.get("source_id"), item.get("external_event_key"),
                    item.get("lane"), item.get("title"), _normalize_title(item.get("title")), item.get("organizer") or "",
                    item.get("start_date") or None, item.get("end_date") or None, item.get("timezone") or "UTC",
                    item.get("local_time_text") or "", item.get("date_precision"), item.get("venue") or "",
                    item.get("address") or "", item.get("city") or "", item.get("region") or "",
                    item.get("country_code") or "", bool(item.get("is_online")), item.get("official_url"),
                    item.get("registration_url") or "", item.get("event_status"), item.get("evidence_grade"),
                    item.get("verification_status"), float(item.get("confidence") or 0), item.get("relevance_score"),
                    item.get("relevance_basis") or "", item.get("viltrox_presence_status"),
                    item.get("viltrox_evidence_url") or "", opportunity_checked_at,
                    opportunity_checked_at, opportunity_checked_at, item_hash,
                    _json(
                        {
                            "catalog_version": data.get("catalog_version"),
                            "dealer_match_name": item.get("dealer_match_name") or "",
                            "dealer_stable_location_key": item.get("dealer_stable_location_key") or "",
                            "review_evidence": opportunity_review_evidence,
                            "viltrox_evidence": item.get("viltrox_evidence") or {},
                        }
                    ),
                ),
            )
            current = conn.execute(
                "SELECT id FROM vkpi_event_opportunities WHERE canonical_key = ? AND organization_id = ?",
                (item.get("canonical_key"), org_id),
            ).fetchone()
            opportunity_id = str(_row(current).get("id"))
            observation_payload = {
                **item,
                "source_url": item.get("official_url"),
                "observed_at": opportunity_checked_at,
                "review_status": "quality_contract_accepted",
            }
            observation_hash = observation_identity_hash(
                opportunity_content_hash=item_hash,
                source_url=item.get("official_url"),
                observed_at=opportunity_checked_at,
                review_status="quality_contract_accepted",
                reviewer_id=item.get("reviewer_id"),
                evidence_scope=item.get("evidence_scope"),
                value_status=item.get("value_status"),
                dealer_stable_location_key=item.get("dealer_stable_location_key"),
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
                    org_id, item.get("source_id"), run_id, opportunity_id, item.get("external_event_key") or "",
                    item.get("official_url"), observation_hash, opportunity_checked_at,
                    _json(observation_payload),
                ),
            ).fetchone()
            observation_id = _row(observation).get("id")
            if observation_id:
                observations += 1
            else:
                observation_id = _row(conn.execute(
                    "SELECT id FROM vkpi_event_source_observations WHERE organization_id=? AND source_id=? AND external_event_key=? AND content_hash=?",
                    (
                        org_id,
                        item.get("source_id"),
                        item.get("external_event_key") or "",
                        observation_hash,
                    ),
                ).fetchone()).get("id")
            if not old or hash_changed:
                ledger_fields = ["initial_observation"] if not old else list(changed_fields)
                if approval_invalidated and "decision_status" not in ledger_fields:
                    ledger_fields.append("decision_status")
                conn.execute(
                    """
                    INSERT INTO vkpi_event_opportunity_changes
                      (organization_id, opportunity_id, observation_id, change_kind, before_hash, after_hash, changed_fields)
                    VALUES (?,?,?,?,?,?,?::jsonb)
                    """,
                    (
                        org_id,
                        opportunity_id,
                        observation_id,
                        (
                            "discovered"
                            if not old
                            else "approval_invalidated"
                            if approval_invalidated
                            else "source_update"
                        ),
                        str(old.get("content_hash") or ""), item_hash,
                        _json(ledger_fields),
                    ),
                )
                changes += 1

            dealer_location_key = str(item.get("dealer_stable_location_key") or "").strip()
            if dealer_location_key:
                dealer = conn.execute(
                    """
                    SELECT CASE
                             WHEN COUNT(DISTINCT dealer_id)=1 THEN MIN(dealer_id)
                             ELSE NULL
                           END AS id
                    FROM vkpi_dealer_identity_aliases
                    WHERE organization_id=? AND stable_location_key=? AND verified_at IS NOT NULL
                    """,
                    (org_id, dealer_location_key),
                ).fetchone()
                dealer_id = _row(dealer).get("id")
                if dealer_id is None:
                    raise ValueError(
                        "dealer_stable_location_key must resolve to exactly one reviewed Dealer"
                    )
                conn.execute(
                    """
                    DELETE FROM vkpi_event_opportunity_dealers
                    WHERE organization_id=? AND opportunity_id=? AND relation_type='host' AND dealer_id<>?
                    """,
                    (org_id, opportunity_id, dealer_id),
                )
                conn.execute(
                    """
                    INSERT INTO vkpi_event_opportunity_dealers(organization_id, opportunity_id, dealer_id, relation_type)
                    VALUES (?,?,?,'host') ON CONFLICT DO NOTHING
                    """,
                    (org_id, opportunity_id, dealer_id),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM vkpi_event_opportunity_dealers
                    WHERE organization_id=? AND opportunity_id=? AND relation_type='host'
                    """,
                    (org_id, opportunity_id),
                )

        conn.execute(
            """
            UPDATE vkpi_event_source_runs
            SET status='succeeded', finished_at=NOW(), inserted_count=?, updated_count=?,
                unchanged_count=?, metadata_json = metadata_json || ?::jsonb
            WHERE id=?
            """,
            (
                inserted,
                updated,
                unchanged,
                _json(
                    {
                        "observations": observations,
                        "changes": changes,
                        "invalidated_approvals": invalidated_approvals,
                    }
                ),
                run_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        **preview,
        "record_only": False,
        "discovered": len(data["opportunities"]),
        "run_key": run_key,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "observations_inserted": observations,
        "changes_inserted": changes,
        "invalidated_approvals": invalidated_approvals,
    }
