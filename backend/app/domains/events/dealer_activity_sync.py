"""Fail-closed Dealer activity feed -> Event candidate staging sync.

This module is the automated *candidate* boundary only.  It may fetch a
code-bound, explicitly activated public structured feed and persist pending
``vkpi_dealer_event_candidates`` rows.  It never writes ``vkpi_events``, Event
Radar opportunities, Dealer rows, Dealer/Event associations, or promotion
receipts.

Runtime network access requires all of the following, every run:

* the scheduler task is separately enabled;
* the US ``dealer_event`` watch target is active, enabled and due;
* an explicit, current activity-sync approval receipt is present in the watch
  target metadata;
* the workspace owns a current verified ``source_registry`` passport whose
  canonical URL matches the watch target; and
* :mod:`feed_adapters` accepts the exact code-owned feed URL/parser binding.

Tests inject captured fixtures.  The default entry point does not use the
network unless ``allow_network=True`` is supplied by the gated scheduler job.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db.connection import get_conn, table_exists
from app.domains.events import candidate_staging, feed_adapters, us_coverage_registry
from app.domains.events.dealer_activity_sync_runtime import (
    DealerActivitySourceBlocked,
    DealerActivitySyncUnavailable,
    FetchResult,
    fetch_public_feed,
    retry_delay,
    success_delay,
)
from app.domains.source_passport_core import REVIEWER_ID_RE, freshness
from app.domains.source_passport_urls import source_url_identity


TASK_KEY = "vkpi_dealer_activity_candidate_sync"
RUN_KIND = "dealer_activity_candidate_sync"
CLAIM_STATUS = "descriptive_only"
MAX_SOURCES_PER_RUN = 20
MAX_ERROR_TEXT = 240
CLAIM_LEASE_MINUTES = 60
STALE_RUN_HOURS = 2
DEFAULT_APPROVAL_STALE_DAYS = 90
ALLOWED_APPROVAL_STATUSES = frozenset({"approved"})
REQUIRED_TABLES = (
    "vkpi_event_watch_targets",
    "vkpi_event_source_runs",
    "vkpi_source_passports",
    "vkpi_dealer_event_candidates",
    "vkpi_candidate_field_evidence_links",
)


class SyncRepository(Protocol):
    def recover_stale_runs(
        self, *, as_of: datetime, organization_id: int
    ) -> int: ...

    def claim_due_sources(
        self, *, as_of: datetime, limit: int, organization_id: int
    ) -> list[dict[str, Any]]: ...

    def source_passport(
        self, *, source_id: str, organization_id: int
    ) -> dict[str, Any] | None: ...

    def renew_claim(
        self,
        *,
        source_id: str,
        claim_token: str,
        organization_id: int,
        as_of: datetime,
    ) -> bool: ...

    def create_run(
        self,
        *,
        source_id: str,
        claim_token: str,
        organization_id: int,
        as_of: datetime,
    ) -> int: ...

    def existing_candidate(
        self, *, source_id: str, source_entity_key: str, organization_id: int
    ) -> dict[str, Any] | None: ...

    def stage_candidate(
        self,
        payload: Mapping[str, Any],
        *,
        source_id: str,
        claim_token: str,
        organization_id: int,
    ) -> dict[str, Any]: ...

    def finish_source(
        self,
        *,
        source_id: str,
        claim_token: str,
        run_id: int,
        organization_id: int,
        as_of: datetime,
        status: str,
        counts: Mapping[str, int],
        http_status: int | None,
        error: str,
        metadata: Mapping[str, Any],
    ) -> bool: ...


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return value.astimezone(timezone.utc)


def _loads_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _error_text(exc: BaseException) -> str:
    detail = str(exc).replace("\n", " ").strip()
    return f"{exc.__class__.__name__}: {detail}"[:MAX_ERROR_TEXT]


def _registered_source(source_id: str) -> dict[str, Any]:
    for raw in us_coverage_registry.audit_registry().get("event_sources", []):
        if str(raw.get("id") or "") == source_id:
            return dict(raw)
    return {}


def _activation_receipt(source: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _loads_object(source.get("metadata_json"))
    receipt = metadata.get("activity_sync_approval")
    return dict(receipt) if isinstance(receipt, Mapping) else {}


def _source_descriptor(source: Mapping[str, Any]) -> dict[str, Any]:
    source_id = str(source.get("id") or "").strip()
    registered = _registered_source(source_id)
    receipt = _activation_receipt(source)
    return {
        **registered,
        "id": source_id,
        "name": source.get("name") or registered.get("name"),
        "publisher": registered.get("publisher") or receipt.get("publisher"),
        "source_kind": source.get("source_kind"),
        "country_code": source.get("country_code"),
        "timezone": source.get("timezone"),
        "canonical_url": source.get("canonical_url"),
        "parser_profile": source.get("parser_profile"),
        "status": source.get("status"),
        "enabled": source.get("enabled") is True,
        "requires_human_review": source.get("requires_human_review") is True,
        "terms_robots_status": source.get("terms_robots_status"),
        "feed_url": receipt.get("feed_url"),
        "terms_robots_reviewer_id": receipt.get("approved_by"),
        "terms_robots_reviewed_at": receipt.get("approved_at"),
        "direct_import_allowed": receipt.get("direct_import_allowed"),
    }


def _approval_gate(
    source: Mapping[str, Any], *, as_of: datetime, organization_id: int
) -> dict[str, Any]:
    receipt = _activation_receipt(source)
    reasons: list[str] = []
    status = str(receipt.get("status") or "").strip().casefold()
    if status not in ALLOWED_APPROVAL_STATUSES:
        reasons.append("activity_sync_not_approved")
    approver = str(receipt.get("approved_by") or "").strip()
    if not REVIEWER_ID_RE.fullmatch(approver):
        reasons.append("activity_sync_approver_invalid")
    approved_at = _timestamp(receipt.get("approved_at"))
    try:
        stale_days = int(
            receipt.get("stale_after_days") or DEFAULT_APPROVAL_STALE_DAYS
        )
    except (TypeError, ValueError):
        stale_days = 0
    if stale_days < 1 or stale_days > 365:
        reasons.append("activity_sync_approval_ttl_invalid")
    approval_freshness = freshness(
        approved_at,
        as_of=as_of,
        stale_after_days=stale_days or DEFAULT_APPROVAL_STALE_DAYS,
    )
    if approval_freshness["status"] != "fresh":
        reasons.append("activity_sync_approval_not_fresh")
    if receipt.get("candidate_generation_allowed") is not True:
        reasons.append("candidate_generation_not_approved")
    if receipt.get("direct_import_allowed") is not False:
        reasons.append("direct_import_must_remain_disabled")
    if receipt.get("automatic_promotion") is not False:
        reasons.append("automatic_promotion_must_remain_disabled")
    try:
        approved_org_id = int(receipt.get("organization_id"))
    except (TypeError, ValueError):
        approved_org_id = 0
    if approved_org_id != organization_id:
        reasons.append("activity_sync_workspace_not_approved")
    registry = _registered_source(str(source.get("id") or ""))
    if registry.get("candidate_generation_allowed") is False:
        reasons.append("registry_candidate_generation_blocked")
    return {
        "allowed": not reasons,
        "status": status or "unavailable",
        "approved_by": approver or None,
        "approved_at": approved_at.isoformat() if approved_at else None,
        "organization_id": approved_org_id or None,
        "freshness": approval_freshness,
        "reasons": reasons,
    }


def _passport_gate(
    source: Mapping[str, Any],
    passport: Mapping[str, Any] | None,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    row = dict(passport or {})
    reasons: list[str] = []
    source_id = str(source.get("id") or "")
    if not row:
        reasons.append("source_registry_passport_missing")
    if str(row.get("entity_type") or "") != "source_registry":
        reasons.append("source_registry_passport_type_invalid")
    if str(row.get("registry_source_id") or "") != source_id:
        reasons.append("source_registry_passport_identity_mismatch")
    if str(row.get("identity_status") or "") != "exact":
        reasons.append("source_registry_identity_not_exact")
    if str(row.get("publisher_tier") or "") in {"", "unknown"}:
        reasons.append("source_registry_publisher_tier_unverified")
    if str(row.get("verification_status") or "") != "verified":
        reasons.append("source_registry_passport_not_verified")
    if str(row.get("freshness_status_at_write") or "") != "fresh":
        reasons.append("source_registry_passport_write_not_fresh")
    if str(row.get("claim_status") or "") != CLAIM_STATUS:
        reasons.append("source_registry_passport_claim_invalid")
    if not row.get("reviewer_staff_id"):
        reasons.append("source_registry_passport_reviewer_missing")
    try:
        stale_days = int(row.get("stale_after_days") or 0)
    except (TypeError, ValueError):
        stale_days = 0
    current = freshness(
        row.get("verified_at"),
        as_of=as_of,
        stale_after_days=stale_days if stale_days > 0 else 1,
    )
    if stale_days < 1 or current["status"] != "fresh":
        reasons.append("source_registry_passport_not_current")
    source_url = source_url_identity(source.get("canonical_url"))
    passport_url = source_url_identity(row.get("canonical_url"))
    if (
        not source_url.get("valid")
        or not passport_url.get("valid")
        or source_url.get("canonical_url") != passport_url.get("canonical_url")
    ):
        reasons.append("source_registry_passport_url_mismatch")
    return {
        "allowed": not reasons,
        "passport_id": row.get("id"),
        "freshness": current,
        "reasons": reasons,
    }


def _candidate_is_current(candidate: Mapping[str, Any], *, as_of: datetime) -> bool:
    payload = candidate.get("candidate_payload")
    item = dict(payload) if isinstance(payload, Mapping) else {}
    end_text = str(item.get("end_date") or item.get("start_date") or "")
    try:
        end_date = date.fromisoformat(end_text)
    except ValueError:
        return False
    try:
        local_date = as_of.astimezone(
            ZoneInfo(str(item.get("timezone") or "UTC"))
        ).date()
    except ZoneInfoNotFoundError:
        return False
    return end_date >= local_date


def _normalized_content_sha(candidate: Mapping[str, Any]) -> str:
    payload = candidate.get("candidate_payload")
    item = dict(payload) if isinstance(payload, Mapping) else {}
    provenance = item.get("provenance")
    if not isinstance(provenance, Mapping):
        return ""
    value = str(provenance.get("normalized_content_sha256") or "")
    return value if len(value) == 64 else ""


def _existing_normalized_content_sha(existing: Mapping[str, Any] | None) -> str:
    if not existing:
        return ""
    payload = _loads_object(existing.get("candidate_payload_json"))
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        return ""
    value = str(provenance.get("normalized_content_sha256") or "")
    return value if len(value) == 64 else ""


def _stage_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "record_only": False,
        "source_registry_id": candidate.get("source_registry_id"),
        "source_entity_key": candidate.get("source_entity_key"),
        "source_url": candidate.get("source_url"),
        "stable_org_key": "",
        "stable_location_key": candidate.get("stable_location_key") or "",
        "candidate_payload": dict(candidate.get("candidate_payload") or {}),
    }


class PostgresSyncRepository:
    """Small SQL adapter over the existing migrations 243/244/248/257."""

    def __init__(self, connection: Any | None = None) -> None:
        self.conn = connection or get_conn()

    def _require_schema(self) -> None:
        try:
            ready = all(table_exists(name) for name in REQUIRED_TABLES)
        except Exception as exc:
            raise DealerActivitySyncUnavailable("dealer_activity_sync_schema_unavailable") from exc
        if not ready:
            raise DealerActivitySyncUnavailable("dealer_activity_sync_migration_pending")

    def recover_stale_runs(
        self, *, as_of: datetime, organization_id: int
    ) -> int:
        """Close orphaned receipts; never mutate source state or business rows."""
        self._require_schema()
        stale_before = as_of - timedelta(hours=STALE_RUN_HOURS)
        cursor = self.conn.execute(
            """
            UPDATE vkpi_event_source_runs
            SET status='failed',finished_at=?,error_count=error_count+1,
                error_class='stale_run_recovered',
                metadata_json=metadata_json || ?::jsonb
            WHERE organization_id=? AND run_kind=? AND status='running'
              AND started_at<?
            """,
            (
                as_of,
                json.dumps(
                    {
                        "candidate_only": True,
                        "business_rows_written": 0,
                        "recovery": "stale_run_receipt_closed",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                organization_id,
                RUN_KIND,
                stale_before,
            ),
        )
        self.conn.commit()
        return max(0, int(cursor.rowcount or 0))

    def claim_due_sources(
        self, *, as_of: datetime, limit: int, organization_id: int
    ) -> list[dict[str, Any]]:
        self._require_schema()
        rows = self.conn.execute(
            """
            SELECT id,name,source_kind,country_code,region,timezone,canonical_url,
                   parser_profile,requires_human_review,terms_robots_status,status,
                   enabled,refresh_policy,priority_tier,next_check_at,failure_count,
                   dealer_id,metadata_json
            FROM vkpi_event_watch_targets
            WHERE source_kind='dealer_event' AND country_code='US'
              AND status='active' AND COALESCE(enabled,FALSE)=TRUE
              AND (next_check_at IS NULL OR next_check_at<=?)
              AND (
                activity_sync_claim_expires_at IS NULL
                OR activity_sync_claim_expires_at<=?
              )
            ORDER BY priority_tier ASC,COALESCE(next_check_at,created_at) ASC,id ASC
            LIMIT ? FOR UPDATE SKIP LOCKED
            """,
            (as_of, as_of, limit),
        ).fetchall()
        claimed = [dict(row) for row in rows]
        lease_until = as_of + timedelta(minutes=CLAIM_LEASE_MINUTES)
        for row in claimed:
            token = uuid.uuid4().hex
            cursor = self.conn.execute(
                """
                UPDATE vkpi_event_watch_targets
                SET next_check_at=?,activity_sync_claim_token=?,
                    activity_sync_claim_organization_id=?,activity_sync_claimed_at=?,
                    activity_sync_claim_expires_at=?,updated_at=NOW()
                WHERE id=? AND status='active' AND COALESCE(enabled,FALSE)=TRUE
                  AND (
                    activity_sync_claim_expires_at IS NULL
                    OR activity_sync_claim_expires_at<=?
                  )
                """,
                (
                    lease_until,
                    token,
                    organization_id,
                    as_of,
                    lease_until,
                    row["id"],
                    as_of,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise DealerActivitySyncUnavailable("source_claim_race")
            row["activity_sync_claim_token"] = token
            row["activity_sync_claim_organization_id"] = organization_id
            row["activity_sync_claim_expires_at"] = lease_until
        self.conn.commit()
        return claimed

    def renew_claim(
        self,
        *,
        source_id: str,
        claim_token: str,
        organization_id: int,
        as_of: datetime,
    ) -> bool:
        lease_until = as_of + timedelta(minutes=CLAIM_LEASE_MINUTES)
        cursor = self.conn.execute(
            """
            UPDATE vkpi_event_watch_targets
            SET activity_sync_claim_expires_at=?,updated_at=NOW()
            WHERE id=? AND activity_sync_claim_token=?
              AND activity_sync_claim_organization_id=?
              AND activity_sync_claim_expires_at>?
            """,
            (lease_until, source_id, claim_token, organization_id, as_of),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0) == 1

    def source_passport(
        self, *, source_id: str, organization_id: int
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id,entity_type,registry_source_id,canonical_url,identity_status,
                   publisher_tier,verification_status,freshness_status_at_write,
                   verified_at,stale_after_days,reviewer_staff_id,claim_status
            FROM vkpi_source_passports
            WHERE organization_id=? AND entity_type='source_registry'
              AND registry_source_id=? LIMIT 1
            """,
            (organization_id, source_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def stage_candidate(
        self,
        payload: Mapping[str, Any],
        *,
        source_id: str,
        claim_token: str,
        organization_id: int,
    ) -> dict[str, Any]:
        return candidate_staging.stage_candidate_with_source_claim(
            payload, candidate_type="event_opportunity", organization_id=organization_id,
            source_id=source_id, claim_token=claim_token, connection=self.conn,
        )

    def create_run(
        self,
        *,
        source_id: str,
        claim_token: str,
        organization_id: int,
        as_of: datetime,
    ) -> int:
        run_key = f"dealer-activity-candidate-{source_id}-{uuid.uuid4().hex}"
        row = self.conn.execute(
            """
            INSERT INTO vkpi_event_source_runs
              (organization_id,run_key,source_id,run_kind,status,record_only,
               started_at,metadata_json)
            VALUES (?, ?, ?, ?, 'running', FALSE, ?, ?::jsonb)
            RETURNING id
            """,
            (
                organization_id,
                run_key,
                source_id,
                RUN_KIND,
                as_of,
                json.dumps(
                    {
                        "candidate_only": True,
                        "automatic_promotion": False,
                        "business_rows_written": 0,
                        "claim_token": claim_token,
                        "claim_organization_id": organization_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        ).fetchone()
        self.conn.commit()
        if row is None:
            raise DealerActivitySyncUnavailable("source_run_receipt_not_created")
        return int(dict(row)["id"])

    def existing_candidate(
        self, *, source_id: str, source_entity_key: str, organization_id: int
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id,content_sha256,candidate_payload_json,review_status,
                   promotion_gate_status,updated_at
            FROM vkpi_dealer_event_candidates
            WHERE organization_id=? AND candidate_type='event_opportunity'
              AND source_registry_id=? AND source_entity_key=? LIMIT 1
            """,
            (organization_id, source_id, source_entity_key),
        ).fetchone()
        return dict(row) if row is not None else None

    def finish_source(
        self,
        *,
        source_id: str,
        claim_token: str,
        run_id: int,
        organization_id: int,
        as_of: datetime,
        status: str,
        counts: Mapping[str, int],
        http_status: int | None,
        error: str,
        metadata: Mapping[str, Any],
    ) -> bool:
        success = status == "succeeded"
        source_row = self.conn.execute(
            "SELECT failure_count,refresh_policy FROM vkpi_event_watch_targets WHERE id=?",
            (source_id,),
        ).fetchone()
        source_values = dict(source_row) if source_row is not None else {}
        next_check = as_of + (
            success_delay(source_values.get("refresh_policy"))
            if success
            else retry_delay(source_values.get("failure_count"))
        )
        if success:
            source_cursor = self.conn.execute(
                """
                UPDATE vkpi_event_watch_targets
                SET last_checked_at=?,last_success_at=?,next_check_at=?,
                    failure_count=0,last_error='',activity_sync_claim_token='',
                    activity_sync_claim_organization_id=NULL,
                    activity_sync_claimed_at=NULL,activity_sync_claim_expires_at=NULL,
                    updated_at=NOW()
                WHERE id=? AND activity_sync_claim_token=?
                  AND activity_sync_claim_organization_id=?
                  AND activity_sync_claim_expires_at>?
                """,
                (
                    as_of,
                    as_of,
                    next_check,
                    source_id,
                    claim_token,
                    organization_id,
                    as_of,
                ),
            )
        else:
            source_cursor = self.conn.execute(
                """
                UPDATE vkpi_event_watch_targets
                SET last_checked_at=?,next_check_at=?,failure_count=failure_count+1,
                    last_error=?,activity_sync_claim_token='',
                    activity_sync_claim_organization_id=NULL,
                    activity_sync_claimed_at=NULL,activity_sync_claim_expires_at=NULL,
                    updated_at=NOW()
                WHERE id=? AND activity_sync_claim_token=?
                  AND activity_sync_claim_organization_id=?
                  AND activity_sync_claim_expires_at>?
                """,
                (
                    as_of,
                    next_check,
                    error[:MAX_ERROR_TEXT],
                    source_id,
                    claim_token,
                    organization_id,
                    as_of,
                ),
            )
        fenced = int(source_cursor.rowcount or 0) != 1
        terminal_status = "failed" if fenced else status
        terminal_error = "source_claim_fenced" if fenced else error[:MAX_ERROR_TEXT]
        self.conn.execute(
            """
            UPDATE vkpi_event_source_runs
            SET status=?,finished_at=?,http_status=?,discovered_count=?,
                inserted_count=?,updated_count=?,unchanged_count=?,error_count=?,
                error_class=?,metadata_json=?::jsonb
            WHERE organization_id=? AND id=? AND status='running'
            """,
            (
                terminal_status,
                as_of,
                http_status,
                int(counts.get("discovered") or 0),
                int(counts.get("created") or 0),
                int(counts.get("restaged") or 0),
                int(counts.get("unchanged") or 0),
                int(counts.get("errors") or 0),
                terminal_error,
                json.dumps(
                    {
                        **dict(metadata),
                        "claim_fenced": fenced,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                organization_id,
                run_id,
            ),
        )
        self.conn.commit()
        return not fenced


def _sync_one_source(
    source: Mapping[str, Any],
    *,
    repository: SyncRepository,
    payload_fetcher: Callable[[Mapping[str, Any], Mapping[str, Any]], FetchResult],
    organization_id: int,
    as_of: datetime,
) -> dict[str, Any]:
    source_id = str(source.get("id") or "")
    claim_token = str(source.get("activity_sync_claim_token") or "")
    if len(claim_token) != 32:
        return {
            "source_id": source_id,
            "status": "failed",
            "run_id": None,
            "counts": {"errors": 1},
            "error": "source_claim_token_missing",
            "network_accessed": False,
            "business_rows_written": 0,
        }
    run_id = repository.create_run(
        source_id=source_id,
        claim_token=claim_token,
        organization_id=organization_id,
        as_of=as_of,
    )
    counts = {
        "discovered": 0,
        "created": 0,
        "restaged": 0,
        "unchanged": 0,
        "expired": 0,
        "duplicates": 0,
        "rejected": 0,
        "errors": 0,
    }
    http_status: int | None = None
    network_accessed = False
    stage_errors: list[str] = []

    def finish(
        *, status: str, error: str, metadata: Mapping[str, Any]
    ) -> bool:
        return repository.finish_source(
            source_id=source_id,
            claim_token=claim_token,
            run_id=run_id,
            organization_id=organization_id,
            as_of=_as_utc(None),
            status=status,
            counts=counts,
            http_status=http_status,
            error=error,
            metadata=metadata,
        )

    try:
        approval = _approval_gate(
            source, as_of=as_of, organization_id=organization_id
        )
        passport = _passport_gate(
            source,
            repository.source_passport(
                source_id=source_id, organization_id=organization_id
            ),
            as_of=as_of,
        )
        descriptor = _source_descriptor(source)
        adapter_preflight = feed_adapters.source_fetch_preflight(descriptor)
        gate_reasons = [
            *approval["reasons"],
            *passport["reasons"],
            *adapter_preflight["reasons"],
        ]
        if gate_reasons:
            error = "source_blocked:" + ",".join(sorted(set(gate_reasons)))
            counts["errors"] = 1
            finished = finish(
                status="failed",
                error=error,
                metadata={
                    "candidate_only": True,
                    "automatic_promotion": False,
                    "business_rows_written": 0,
                    "approval_gate": approval,
                    "passport_gate": passport,
                    "feed_gate": adapter_preflight,
                    "network_accessed": False,
                },
            )
            return {
                "source_id": source_id,
                "status": "blocked" if finished else "failed",
                "run_id": run_id,
                "counts": counts,
                "reasons": sorted(set(gate_reasons)),
                "error": "" if finished else "source_claim_fenced",
                "network_accessed": False,
                "business_rows_written": 0,
            }

        if not repository.renew_claim(
            source_id=source_id,
            claim_token=claim_token,
            organization_id=organization_id,
            as_of=_as_utc(None),
        ):
            raise DealerActivitySourceBlocked("source_claim_fenced")
        if payload_fetcher is fetch_public_feed:
            # A transport failure after the socket opens still counts as a
            # network attempt even when no FetchResult can be returned.
            network_accessed = True
        fetched = payload_fetcher(descriptor, adapter_preflight)
        http_status = int(fetched.http_status)
        network_accessed = bool(fetched.network_accessed)
        if fetched.coverage_status != "complete":
            raise DealerActivitySourceBlocked(
                f"feed_coverage_{fetched.coverage_status or 'unproven'}"
            )
        adapted = feed_adapters.adapt_feed_to_candidates(
            descriptor,
            fetched.payload,
            observed_at=as_of,
            organization_id=organization_id,
        )
        counts["discovered"] = int(adapted["counts"]["parsed_items"])
        counts["duplicates"] = int(adapted["counts"]["duplicate_items"])
        counts["rejected"] = int(adapted["counts"]["rejected_items"])
        for index, candidate in enumerate(adapted["candidates"]):
            if index % 25 == 0 and not repository.renew_claim(
                source_id=source_id,
                claim_token=claim_token,
                organization_id=organization_id,
                as_of=_as_utc(None),
            ):
                raise DealerActivitySourceBlocked("source_claim_fenced")
            if not _candidate_is_current(candidate, as_of=as_of):
                counts["expired"] += 1
                continue
            existing = repository.existing_candidate(
                source_id=source_id,
                source_entity_key=str(candidate["source_entity_key"]),
                organization_id=organization_id,
            )
            normalized_sha = _normalized_content_sha(candidate)
            if normalized_sha and normalized_sha == _existing_normalized_content_sha(existing):
                counts["unchanged"] += 1
                continue
            try:
                staged = repository.stage_candidate(
                    _stage_payload(candidate), source_id=source_id,
                    claim_token=claim_token, organization_id=organization_id,
                )
            except Exception as exc:
                counts["errors"] += 1
                if len(stage_errors) < 10:
                    stage_errors.append(_error_text(exc))
                continue
            if staged.get("created") is True:
                counts["created"] += 1
            elif staged.get("restaged") is True:
                counts["restaged"] += 1
            else:
                counts["unchanged"] += 1

        partial = bool(counts["rejected"] or counts["errors"])
        run_status = "partial" if partial else "succeeded"
        error = (
            f"partial:rejected={counts['rejected']},stage_errors={counts['errors']};"
            f"first={stage_errors[0] if stage_errors else 'feed_item_rejected'}"
            if partial
            else ""
        )
        finished = finish(
            status=run_status,
            error=error,
            metadata={
                "candidate_only": True,
                "automatic_promotion": False,
                "business_rows_written": 0,
                "network_accessed": network_accessed,
                "coverage_status": fetched.coverage_status,
                "pages_fetched": fetched.pages_fetched,
                "approval_gate": {"allowed": True},
                "passport_gate": {"allowed": True, "passport_id": passport["passport_id"]},
                "feed_gate": {"allowed": True},
                "expired_candidates_suppressed": counts["expired"],
                "cross_source_merge": False,
                "cross_source_dedupe": "human_review_only",
                "candidate_lifecycle_reconciliation": "new_or_changed_rows_only",
                "deletion_cancellation_reconciliation": False,
                "stage_errors": stage_errors,
            },
        )
        return {
            "source_id": source_id,
            "status": run_status if finished else "failed",
            "run_id": run_id,
            "counts": counts,
            "error": "" if finished else "source_claim_fenced",
            "network_accessed": network_accessed,
            "business_rows_written": 0,
        }
    except Exception as exc:
        counts["errors"] += 1
        error = _error_text(exc)
        finished = finish(
            status="failed",
            error=error,
            metadata={
                "candidate_only": True,
                "automatic_promotion": False,
                "business_rows_written": 0,
                "network_accessed": network_accessed,
                "error": error,
            },
        )
        return {
            "source_id": source_id,
            "status": "failed",
            "run_id": run_id,
            "counts": counts,
            "error": error if finished else "source_claim_fenced",
            "network_accessed": network_accessed,
            "business_rows_written": 0,
        }


def run_dealer_activity_candidate_sync(
    *,
    organization_id: Any = None,
    as_of: datetime | None = None,
    limit: Any = MAX_SOURCES_PER_RUN,
    allow_network: bool = False,
    repository: SyncRepository | None = None,
    payload_fetcher: Callable[[Mapping[str, Any], Mapping[str, Any]], FetchResult]
    | None = None,
) -> dict[str, Any]:
    """Sync due, approved Dealer feeds into pending candidate staging only."""
    if payload_fetcher is None and not allow_network:
        return {
            "status": "blocked",
            "reason": "network_authority_required",
            "sources_claimed": 0,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "network_accessed": False,
            "automatic_promotion": False,
            "claim_status": CLAIM_STATUS,
        }
    org_id = _positive_int(organization_id, "organization_id")
    now = _as_utc(as_of)
    safe_limit = min(MAX_SOURCES_PER_RUN, _positive_int(limit, "limit"))
    repo = repository or PostgresSyncRepository()
    fetcher = payload_fetcher or fetch_public_feed
    stale_runs_recovered = repo.recover_stale_runs(
        as_of=now, organization_id=org_id
    )
    sources = repo.claim_due_sources(
        as_of=now, limit=safe_limit, organization_id=org_id
    )
    results = [
        _sync_one_source(
            source,
            repository=repo,
            payload_fetcher=fetcher,
            organization_id=org_id,
            as_of=now,
        )
        for source in sources
    ]
    failed = sum(1 for row in results if row["status"] in {"blocked", "failed", "partial"})
    candidate_writes = sum(
        int(row["counts"].get("created") or 0)
        + int(row["counts"].get("restaged") or 0)
        for row in results
    )
    return {
        "status": "empty" if not results else "degraded" if failed else "ok",
        "organization_id": org_id,
        "as_of": now.isoformat(),
        "stale_runs_recovered": stale_runs_recovered,
        "sources_claimed": len(results),
        "sources_succeeded": len(results) - failed,
        "sources_failed_or_partial": failed,
        "candidate_rows_written": candidate_writes,
        "business_rows_written": 0,
        "network_accessed": any(row["network_accessed"] for row in results),
        "automatic_promotion": False,
        "results": results,
        "claim_status": CLAIM_STATUS,
    }


__all__ = [
    "CLAIM_STATUS",
    "DealerActivitySourceBlocked",
    "DealerActivitySyncUnavailable",
    "FetchResult",
    "MAX_SOURCES_PER_RUN",
    "PostgresSyncRepository",
    "TASK_KEY",
    "fetch_public_feed",
    "run_dealer_activity_candidate_sync",
]
