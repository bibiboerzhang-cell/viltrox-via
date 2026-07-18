"""External Event Radar service.

The radar is a source-backed opportunity layer.  It never treats a public page as
an approved internal event and never infers Viltrox authorization, current stock,
attendance, ROI, sales, or local impact.  Promotion to ``vkpi_events`` is an
explicit, atomic user action.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.access import scope
from app.domains.events import (
    radar_catalog_preview,
    radar_import,
    radar_quality,
    radar_queries,
    radar_summary,
)
from app.domains.platform import tenancy
from app.domains.source_passport_core import (
    as_utc as _passport_as_utc,
    freshness as _passport_freshness,
)
_CATALOG_PATH = Path(__file__).with_name("radar_seed_catalog.json")
_TABLE = "vkpi_event_opportunities"
_ALLOWED_DECISIONS = {"watching", "approved", "dismissed", "needs_review"}
_DECISION_TRANSITIONS = {
    "new": {"watching", "dismissed", "needs_review"},
    "watching": {"approved", "dismissed", "needs_review"},
    "approved": {"watching", "dismissed", "needs_review"},
    "dismissed": {"watching", "needs_review"},
    "needs_review": {"watching", "dismissed"},
    "promoted": set(),
}


class EventRadarStateConflict(ValueError):
    """The opportunity changed after this request read its decision state."""


class EventRadarSchemaUnavailable(RuntimeError):
    """The organization-scoped Event schema is not safe to use yet."""

    def __init__(self, code: str):
        self.code = str(code or "event_radar_schema_unavailable")
        super().__init__(self.code)


def _now_sql() -> str:
    return "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"


def _json_bind_sql() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _freshness_cas_sql() -> str:
    if is_postgres_runtime():
        return "COALESCE(last_verified_at, source_checked_at)::text=?"
    return "COALESCE(last_verified_at, source_checked_at)=?"


def _require_single_row(cursor: Any, *, action: str) -> None:
    affected = getattr(cursor, "rowcount", None)
    if affected != 1:
        raise EventRadarStateConflict(
            f"event opportunity state conflict during {action}; reload and retry"
        )


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


def _flag(value: Any) -> bool:
    """Compat BOOLEAN read-back: psycopg gives bool, the shim gives int 1/0.

    2026-07-18 体检修:`is not True` 判 int 1 恒失败,把全部机会的
    决策/批准/转 Event 动作面判死(前端同款 `=== true` 吃序列化的 1)。
    """
    if isinstance(value, bool):
        return value
    return value in (1, "1", "t", "true", "TRUE", "True")


def _normalize_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _organization_id(
    staff: dict[str, Any] | None = None,
    explicit: Any = None,
    conn: Any | None = None,
) -> int:
    """Resolve a trusted workspace without exception-to-org-1 fallback."""
    if explicit not in (None, ""):
        try:
            organization_id = int(explicit)
        except (TypeError, ValueError) as exc:
            raise scope.ScopeDenied("event organization context unavailable") from exc
        if organization_id <= 0:
            raise scope.ScopeDenied("event organization context unavailable")
        return organization_id
    return scope.event_organization_id(staff, conn)


def _require_organization_schema(conn: Any | None = None) -> Any:
    """Require migration 244 before any organization-scoped Radar SQL.

    The only statement allowed before this gate opens is the schema capability
    probe inside ``scope.event_has_organization_scope``.  A confirmed pre-244
    schema and a failed/ambiguous probe are deliberately different 503 causes.
    """
    db = conn or get_conn()
    try:
        organization_scoped = scope.event_has_organization_scope(db)
    except Exception as exc:
        raise EventRadarSchemaUnavailable("event_radar_schema_unavailable") from exc
    if not organization_scoped:
        raise EventRadarSchemaUnavailable("migration_244_pending")
    return db


def organization_id_for_staff(staff: dict[str, Any] | None = None) -> int:
    conn = _require_organization_schema()
    return _organization_id(staff, conn=conn)


def _require_actor_in_organization(conn, staff: dict[str, Any] | None, organization_id: int) -> int:
    """Require a real staff actor and prevent cross-organization writes.

    Organization membership predates some staff rows.  A legacy actor with no
    membership row may continue in the default org only; an actor explicitly
    assigned to another org can never fall back across tenants.
    """
    actor = scope.actor_staff_id(staff)
    if not actor:
        raise ValueError("staff identity required")
    if conn.execute("SELECT 1 FROM staff WHERE id=?", (actor,)).fetchone() is None:
        raise ValueError("staff identity not found")
    membership = conn.execute(
        "SELECT 1 FROM organization_members WHERE staff_id=? AND organization_id=?",
        (actor, int(organization_id)),
    ).fetchone()
    if membership is None:
        any_membership = conn.execute(
            "SELECT organization_id FROM organization_members WHERE staff_id=? ORDER BY id LIMIT 1",
            (actor,),
        ).fetchone()
        if any_membership is not None or int(organization_id) != tenancy.default_organization_id():
            raise ValueError("staff organization mismatch")
    return actor


def _content_hash(item: dict[str, Any]) -> str:
    material = {
        key: item.get(key)
        for key in (
            "canonical_key", "source_id", "external_event_key", "lane", "title", "organizer",
            "start_date", "end_date", "timezone", "local_time_text", "date_precision", "venue", "address", "city",
            "region", "country_code", "is_online", "official_url", "registration_url", "event_status",
            "evidence_grade", "verification_status", "viltrox_presence_status", "viltrox_evidence_url",
        )
    }
    dealer_location_key = str(item.get("dealer_stable_location_key") or "").strip()
    if dealer_location_key:
        material["dealer_stable_location_key"] = dealer_location_key
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def _verification_anchor(item: dict[str, Any]) -> Any:
    """Return the exact freshness value validated by approval/promotion CAS."""
    return (
        item.get("verification_anchor_token")
        or item.get("last_verified_at")
        or item.get("source_checked_at")
    )


def _opportunity_with_source(
    conn: Any,
    opportunity_id: str,
    organization_id: int,
    *,
    lock_truth: bool = False,
) -> Any:
    """Read one opportunity, locking source before opportunity on Postgres.

    Catalog import writes watch targets before opportunities.  Matching that
    order here avoids a source/opportunity deadlock while making source status,
    content, verification fields and the later promotion receipt one coherent
    mutation boundary.  SQLite has no row-level ``FOR UPDATE`` and continues to
    rely on the guarded UPDATE/CAS predicates below.
    """
    if lock_truth and is_postgres_runtime():
        seed = conn.execute(
            "SELECT source_id FROM vkpi_event_opportunities WHERE id=? AND organization_id=?",
            (str(opportunity_id), int(organization_id)),
        ).fetchone()
        if seed is None:
            return None
        locked_source_id = str(_row(seed).get("source_id") or "")
        source = conn.execute(
            "SELECT id,status,enabled FROM vkpi_event_watch_targets WHERE id=? FOR UPDATE",
            (locked_source_id,),
        ).fetchone()
        if source is None:
            raise EventRadarStateConflict("event opportunity source changed; reload and retry")
        row = conn.execute(
            """
            SELECT o.*, s.status AS source_status, s.enabled AS source_enabled,
                   COALESCE(o.last_verified_at, o.source_checked_at)::text AS verification_anchor_token
            FROM vkpi_event_opportunities o
            JOIN vkpi_event_watch_targets s ON s.id=o.source_id
            WHERE o.id=? AND o.organization_id=?
            FOR UPDATE OF o
            """,
            (str(opportunity_id), int(organization_id)),
        ).fetchone()
        if row is None:
            return None
        current_source_id = str(_row(row).get("source_id") or "")
        if current_source_id != locked_source_id:
            raise EventRadarStateConflict("event opportunity source changed; reload and retry")
        return row
    # 2026-07-18 体检修(M2):补齐与列表投影同形的 7 字段(source_kind/name/
    # catalog_url/requires_human_review + host dealer + promoted event),否则
    # 深查合并 {...row,...fresh} 时缺键不覆盖,刚晋级条目 converted_event_id 不刷新。
    return conn.execute(
        """
        SELECT o.*, s.name AS source_name, s.status AS source_status,
               s.enabled AS source_enabled,
               s.source_kind AS source_kind,
               s.canonical_url AS source_catalog_url,
               s.requires_human_review AS source_requires_human_review,
               (SELECT od.dealer_id FROM vkpi_event_opportunity_dealers od
                 WHERE od.opportunity_id=o.id AND od.organization_id=o.organization_id
                   AND od.relation_type='host' ORDER BY od.created_at ASC LIMIT 1) AS dealer_id,
               (SELECT d.name FROM vkpi_event_opportunity_dealers od
                  JOIN vkpi_dealers d ON d.id=od.dealer_id
                 WHERE od.opportunity_id=o.id AND od.organization_id=o.organization_id
                   AND od.relation_type='host' ORDER BY od.created_at ASC LIMIT 1) AS dealer_name,
               (SELECT p.event_id FROM vkpi_event_opportunity_promotions p
                 WHERE p.opportunity_id=o.id AND p.organization_id=o.organization_id) AS converted_event_id
        FROM vkpi_event_opportunities o
        JOIN vkpi_event_watch_targets s ON s.id=o.source_id
        WHERE o.id=? AND o.organization_id=?
        """,
        (str(opportunity_id), int(organization_id)),
    ).fetchone()


def _validate_approval_truth(item: dict[str, Any], *, action: str) -> Any:
    """Revalidate every execution truth field after the mutation locks."""
    if str(item.get("source_status") or "") != "active":
        raise ValueError("event opportunity source is not active")
    if not _flag(item.get("source_enabled")):
        raise ValueError("event opportunity source is disabled")
    if item.get("verification_status") != "verified" or item.get("event_status") != "scheduled":
        raise ValueError(f"only verified scheduled opportunities can be {action}")
    if not item.get("start_date") or not item.get("end_date"):
        raise ValueError("verified start_date and end_date required")
    anchor = _verification_anchor(item)
    if _freshness(anchor) != "current":
        raise ValueError("opportunity verification is not current")
    if (_calendar_date(item.get("end_date")) or date.min) < datetime.now(timezone.utc).date():
        raise ValueError(f"past opportunity cannot be {action}")
    return anchor


def load_reviewed_catalog() -> dict[str, Any]:
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data.get("sources"), list) or not isinstance(data.get("opportunities"), list):
        raise ValueError("event radar catalog must contain sources and opportunities arrays")
    return data


def preview_reviewed_catalog() -> dict[str, Any]:
    """Validate and summarize the bundled reviewed catalog without writing DB."""
    return radar_catalog_preview.build_preview(
        load_reviewed_catalog(),
        audit_event_catalog=radar_quality.audit_event_catalog,
    )


_SOURCE_COLUMNS = (
    "name", "source_kind", "country_code", "region", "timezone", "canonical_url", "discovery_url",
    "fetch_mode", "parser_profile", "evidence_grade", "priority_tier", "refresh_policy",
    "requires_human_review", "terms_robots_status", "status", "enabled", "metadata_json",
)

_OPPORTUNITY_FIELDS = (
    "source_id", "external_event_key", "lane", "title", "organizer", "start_date", "end_date",
    "timezone", "local_time_text", "date_precision", "venue", "address", "city", "region",
    "country_code", "is_online", "official_url", "registration_url", "event_status",
    "evidence_grade", "verification_status", "confidence", "relevance_score", "relevance_basis",
    "viltrox_presence_status", "viltrox_evidence_url",
    "dealer_stable_location_key",
)


def _changed_fields(existing: dict[str, Any], item: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for key in _OPPORTUNITY_FIELDS:
        old = existing.get(key)
        new = item.get(key)
        if key in {"start_date", "end_date"}:
            old = str(old or "")
            new = str(new or "")
        if str(old if old is not None else "") != str(new if new is not None else ""):
            changed.append(key)
    return changed


def import_reviewed_catalog(*, record_only: bool = True, organization_id: int = 1) -> dict[str, Any]:
    """Idempotently import the reviewed seed catalog; preview is the safe default."""
    return radar_import.import_reviewed_catalog(
        record_only=record_only,
        organization_id=organization_id,
        deps=SimpleNamespace(
            preview_reviewed_catalog=preview_reviewed_catalog,
            load_reviewed_catalog=load_reviewed_catalog,
            require_organization_schema=_require_organization_schema,
            table_exists=table_exists,
            organization_id=_organization_id,
            json_dumps=_json,
            row=_row,
            normalize_title=_normalize_title,
            content_hash=_content_hash,
            changed_fields=_changed_fields,
            source_columns=_SOURCE_COLUMNS,
        ),
    )


def quality_audit(
    *,
    as_of: datetime | None = None,
    stale_after_days: int = radar_quality.DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """Return the combined bundled Event/Dealer audit without DB or network.

    Importing ``dealer_scrape`` is delayed to avoid coupling the pure quality
    module to application/database helpers.  The called accessor returns copies
    of the in-process reviewed candidates and performs no I/O.
    """
    from app.domains.commerce import dealer_scrape

    return radar_quality.build_event_dealer_quality_audit(
        load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=as_of,
        stale_after_days=stale_after_days,
    )


def remediation_queue(
    *,
    as_of: datetime | None = None,
    stale_after_days: int = radar_quality.DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """Return actionable Event/Dealer evidence gaps without I/O or writes."""
    from app.domains.commerce import dealer_scrape

    return radar_quality.build_event_dealer_remediation_queue(
        load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=as_of,
        stale_after_days=stale_after_days,
    )


def _freshness(value: Any) -> str:
    if not value:
        return "unverified"
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400
        if days < -(1 / 24):
            return "unverified"
        if days <= 7:
            return "current"
        if days <= 30:
            return "aging"
        return "stale"
    except Exception:
        return "unverified"


def _passport_is_current(row: dict[str, Any], *, as_of: datetime) -> bool:
    """Re-evaluate a passport's freshness instead of trusting its write-time label."""
    if str(row.get("verification_status") or "") != "verified":
        return False
    if str(row.get("freshness_status_at_write") or "") != "fresh":
        return False
    stale_value = row.get("stale_after_days")
    if isinstance(stale_value, bool):
        return False
    try:
        stale_days = int(stale_value)
    except (TypeError, ValueError):
        return False
    if not 1 <= stale_days <= 3650:
        return False
    return _passport_freshness(
        row.get("verified_at"),
        as_of=as_of,
        stale_after_days=stale_days,
    )["status"] == "fresh"


def _calendar_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def list_opportunities(
    *,
    limit: int = 100,
    offset: int = 0,
    lane: str | None = None,
    source_kind: str | None = None,
    decision_status: str | None = None,
    evidence_status: str | None = None,
    time_window: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    start_from: str | None = None,
    start_to: str | None = None,
    include_past: bool = False,
    organization_id: int = 1,
) -> dict[str, Any]:
    conn = _require_organization_schema()
    if not table_exists(_TABLE):
        return radar_queries.migration_pending_page(limit=limit, offset=offset)
    org_id = _organization_id(explicit=organization_id)
    return radar_queries.list_opportunities(
        conn,
        table_name=_TABLE,
        organization_id=org_id,
        limit=limit,
        offset=offset,
        lane=lane,
        source_kind=source_kind,
        decision_status=decision_status,
        evidence_status=evidence_status,
        time_window=time_window,
        country=country,
        region=region,
        city=city,
        start_from=start_from,
        start_to=start_to,
        include_past=include_past,
        loads=_loads,
        freshness=_freshness,
    )


def get_opportunity(
    opportunity_id: str,
    *,
    organization_id: int = 1,
) -> dict[str, Any] | None:
    """Read one opportunity fresh, scoped to the caller's organization.

    Single-event deep lookup (M2): reuses ``_opportunity_with_source`` so the
    single-row read applies the same source join and organization scope as the
    list query, then normalizes the row the same way list rows are normalized.
    Returns ``None`` when the id is unknown or belongs to another organization.
    """
    conn = _require_organization_schema()
    if not table_exists(_TABLE):
        return None
    org_id = _organization_id(explicit=organization_id)
    row = _opportunity_with_source(conn, str(opportunity_id), org_id)
    if row is None:
        return None
    item = dict(row)
    item["metadata_json"] = _loads(item.get("metadata_json"), {})
    item["source_enabled"] = _flag(item.get("source_enabled"))
    item["source_requires_human_review"] = _flag(item.get("source_requires_human_review"))
    item["freshness_status"] = _freshness(
        item.get("last_verified_at") or item.get("source_checked_at")
    )
    item["is_source_backed_lead"] = True
    item["business_outcome_status"] = "unmeasured"
    changes_row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_event_opportunity_changes "
        "WHERE opportunity_id=? AND organization_id=?",
        (str(opportunity_id), int(org_id)),
    ).fetchone()
    item["change_count"] = int((dict(changes_row) if changes_row else {}).get("n") or 0)
    return item


def summary(
    *,
    organization_id: int = 1,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    conn = _require_organization_schema()
    if not table_exists(_TABLE):
        return {
            "coverage_claim": "migration_pending", "global_complete": False,
            "sources": {"total": 0}, "opportunities": {"total": 0},
        }
    org_id = _organization_id(explicit=organization_id)
    now = _passport_as_utc(as_of)
    return radar_summary.build_summary(
        conn,
        organization_id=org_id,
        as_of=now,
        table_exists=table_exists,
        row=_row,
        freshness=_freshness,
        passport_is_current=_passport_is_current,
    )


def source_health(*, limit: int = 200) -> dict[str, Any]:
    if not table_exists("vkpi_event_watch_targets"):
        return {"items": [], "count": 0, "coverage_claim": "migration_pending"}
    safe_limit = max(1, min(int(limit or 200), 500))
    rows = get_conn().execute(
        """
        SELECT id,name,source_kind,country_code,region,timezone,canonical_url,fetch_mode,
               evidence_grade,priority_tier,refresh_policy,requires_human_review,
               terms_robots_status,status,enabled,last_checked_at,last_success_at,next_check_at,
               failure_count,last_error,dealer_id,updated_at
        FROM vkpi_event_watch_targets
        ORDER BY priority_tier ASC, source_kind ASC, country_code ASC, name ASC LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    items = [dict(row) for row in rows]
    return {
        "items": items,
        "count": len(items),
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "global_complete": False,
    }


def list_changes(opportunity_id: str, *, limit: int = 100, organization_id: int = 1) -> dict[str, Any]:
    conn = _require_organization_schema()
    if not table_exists("vkpi_event_opportunity_changes"):
        return {"items": [], "count": 0}
    safe_limit = max(1, min(int(limit or 100), 500))
    org_id = _organization_id(explicit=organization_id)
    rows = conn.execute(
        """
        SELECT id,opportunity_id,observation_id,change_kind,before_hash,after_hash,changed_fields,detected_at
        FROM vkpi_event_opportunity_changes WHERE opportunity_id=? AND organization_id=?
        ORDER BY detected_at DESC,id DESC LIMIT ?
        """,
        (str(opportunity_id), org_id, safe_limit),
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        item["changed_fields"] = _loads(item.get("changed_fields"), [])
    return {"items": items, "count": len(items)}


def decide(opportunity_id: str, *, decision_status: str, note: str = "", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    decision = str(decision_status or "").strip()
    if decision not in _ALLOWED_DECISIONS:
        raise ValueError("unsupported decision_status")
    conn = _require_organization_schema()
    org_id = _organization_id(staff, conn=conn)
    try:
        existing = _opportunity_with_source(
            conn,
            str(opportunity_id),
            org_id,
            lock_truth=decision == "approved",
        )
        if not existing:
            raise LookupError("event opportunity not found")
        item = _row(existing)
        if str(item.get("source_status") or "") != "active":
            raise ValueError("event opportunity source is not active")
        if not _flag(item.get("source_enabled")):
            raise ValueError("event opportunity source is disabled")
        current = str(item.get("decision_status") or "new")
        if current == "promoted":
            raise ValueError("promoted opportunity decision is immutable")
        actor = _require_actor_in_organization(conn, staff, org_id)
        verification_anchor = None
        if decision == "approved":
            receipt = conn.execute(
                "SELECT event_id FROM vkpi_event_opportunity_promotions WHERE opportunity_id=? AND organization_id=?",
                (str(opportunity_id), org_id),
            ).fetchone()
            if receipt is not None:
                raise EventRadarStateConflict("promotion receipt already exists; reload and retry")
            verification_anchor = _validate_approval_truth(item, action="approved")
        if current == decision:
            conn.commit()
            return {"item": item, "idempotent": True}
        if decision not in _DECISION_TRANSITIONS.get(current, set()):
            raise ValueError(f"unsupported decision transition: {current} -> {decision}")
        timestamp = _now_sql()
        approval_cas = ""
        approval_params: list[Any] = []
        if decision == "approved":
            approval_cas = f"""
              AND source_id=?
              AND verification_status='verified'
              AND event_status='scheduled'
              AND start_date IS NOT NULL AND end_date IS NOT NULL
              AND end_date >= CURRENT_DATE
              AND {_freshness_cas_sql()}
            """
            approval_params = [str(item.get("source_id") or ""), verification_anchor]
        cursor = conn.execute(
            f"""
            UPDATE vkpi_event_opportunities
            SET decision_status=?, decision_note=?, decision_by=?,
                decision_at={timestamp}, updated_at={timestamp}
            WHERE id=? AND organization_id=? AND decision_status=?
              AND content_hash=?
              {approval_cas}
              AND EXISTS (
                SELECT 1 FROM vkpi_event_watch_targets source
                WHERE source.id=vkpi_event_opportunities.source_id
                  AND source.status='active'
                  AND COALESCE(source.enabled,FALSE)=TRUE
              )
              AND NOT EXISTS (
                SELECT 1 FROM vkpi_event_opportunity_promotions p
                WHERE p.opportunity_id=vkpi_event_opportunities.id
                  AND p.organization_id=vkpi_event_opportunities.organization_id
              )
            """,
            (
                decision,
                str(note or "")[:2000],
                actor,
                str(opportunity_id),
                org_id,
                current,
                str(item.get("content_hash") or ""),
                *approval_params,
            ),
        )
        _require_single_row(cursor, action=f"decision {current} -> {decision}")
        row = conn.execute(
            "SELECT * FROM vkpi_event_opportunities WHERE id=? AND organization_id=?",
            (str(opportunity_id), org_id),
        ).fetchone()
        conn.commit()
        return {"item": dict(row)}
    except Exception:
        conn.rollback()
        raise


def promote(opportunity_id: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Atomically create one internal Event and one immutable promotion link."""
    conn = _require_organization_schema()
    org_id = _organization_id(staff, conn=conn)
    try:
        opportunity = _opportunity_with_source(
            conn,
            str(opportunity_id),
            org_id,
            lock_truth=True,
        )
        if not opportunity:
            raise LookupError("event opportunity not found")
        item = dict(opportunity)
        actor = _require_actor_in_organization(conn, staff, org_id)
        previous = conn.execute(
            "SELECT event_id,promoted_at FROM vkpi_event_opportunity_promotions WHERE opportunity_id=? AND organization_id=?",
            (str(opportunity_id), org_id),
        ).fetchone()
        if previous:
            current = conn.execute(
                "SELECT * FROM vkpi_event_opportunities WHERE id=? AND organization_id=?",
                (str(opportunity_id), org_id),
            ).fetchone()
            if str(_row(current).get("decision_status") or "") != "promoted":
                raise EventRadarStateConflict(
                    "promotion receipt exists but opportunity state is not promoted"
                )
            prior = dict(previous)
            conn.commit()
            return {
                "ok": True,
                "idempotent": True,
                "event_id": prior.get("event_id"),
                "promoted_at": prior.get("promoted_at"),
            }
        if item.get("decision_status") != "approved":
            raise ValueError("opportunity must be approved before promotion")
        verification_anchor = _validate_approval_truth(item, action="promoted")

        timestamp = _now_sql()
        freshness_cas = _freshness_cas_sql()
        claim = conn.execute(
            f"""
            UPDATE vkpi_event_opportunities
            SET decision_status='promoted', decision_by=?,
                decision_at={timestamp}, updated_at={timestamp}
            WHERE id=? AND organization_id=? AND decision_status='approved'
              AND content_hash=?
              AND source_id=?
              AND verification_status='verified'
              AND event_status='scheduled'
              AND start_date IS NOT NULL AND end_date IS NOT NULL
              AND end_date >= CURRENT_DATE
              AND {freshness_cas}
              AND EXISTS (
                SELECT 1 FROM vkpi_event_watch_targets source
                WHERE source.id=vkpi_event_opportunities.source_id
                  AND source.status='active'
                  AND COALESCE(source.enabled,FALSE)=TRUE
              )
              AND NOT EXISTS (
                SELECT 1 FROM vkpi_event_opportunity_promotions p
                WHERE p.opportunity_id=vkpi_event_opportunities.id
                  AND p.organization_id=vkpi_event_opportunities.organization_id
              )
            """,
            (
                actor,
                str(opportunity_id),
                org_id,
                str(item.get("content_hash") or ""),
                str(item.get("source_id") or ""),
                verification_anchor,
            ),
        )
        if getattr(claim, "rowcount", None) != 1:
            concurrent = conn.execute(
                """
                SELECT o.decision_status,p.event_id,p.promoted_at
                FROM vkpi_event_opportunities o
                LEFT JOIN vkpi_event_opportunity_promotions p
                  ON p.opportunity_id=o.id AND p.organization_id=o.organization_id
                WHERE o.id=? AND o.organization_id=?
                """,
                (str(opportunity_id), org_id),
            ).fetchone()
            concurrent_item = _row(concurrent)
            if concurrent_item.get("decision_status") == "promoted" and concurrent_item.get("event_id"):
                conn.commit()
                return {
                    "ok": True,
                    "idempotent": True,
                    "event_id": concurrent_item.get("event_id"),
                    "promoted_at": concurrent_item.get("promoted_at"),
                }
            _require_single_row(claim, action="promotion claim")

        event_id = f"evt_radar_{uuid.uuid4().hex[:20]}"
        lane = str(item.get("lane") or "")
        type_key = "tradeshow" if lane == "major_expo" else "other"
        source_note = (
            "由活动雷达人工批准后转入。外部来源仅证明活动公开信息；不代表 Viltrox 已参展、"
            "Dealer 已授权、库存可售、ROI、到场或销售效果。登记的发布者自有公开入口: " + str(item.get("official_url") or "")
        )
        snapshot = {
            key: item.get(key)
            for key in (
                "organization_id", "id", "canonical_key", "source_id", "source_status", "lane", "title", "organizer", "start_date", "end_date",
                "timezone", "local_time_text", "venue", "address", "city", "region", "country_code",
                "official_url", "registration_url", "evidence_grade", "verification_status", "last_verified_at",
            )
        }
        json_bind = _json_bind_sql()
        conn.execute(
            f"""
            INSERT INTO vkpi_events
              (organization_id,id,title,type_key,status,health_score,note,start_date,end_date,location_name,
               location_city,location_country,budget_total,budget_json,owner_id,team_ids,
               related_project_ids,invited_kols_json,created_at,updated_at)
            VALUES (?,?,?,?, 'planning',NULL,?,?,?,?,?,?,0,{json_bind},?,{json_bind},{json_bind},{json_bind},{timestamp},{timestamp})
            """,
            (
                org_id, event_id, item.get("title"), type_key, source_note, item.get("start_date"), item.get("end_date"),
                item.get("venue") or item.get("address") or "", item.get("city") or "", item.get("country_code") or "",
                _json({}), actor, _json([actor]), _json([]), _json([]),
            ),
        )
        conn.execute(
            f"""
            INSERT INTO vkpi_event_opportunity_promotions(organization_id,opportunity_id,event_id,promoted_by,source_snapshot_json)
            VALUES (?,?,?,?,{json_bind})
            """,
            (org_id, str(opportunity_id), event_id, actor, _json(snapshot)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "idempotent": False, "event_id": event_id, "item": snapshot}

__all__ = [
    "load_reviewed_catalog", "preview_reviewed_catalog", "import_reviewed_catalog", "list_opportunities", "summary", "source_health", "quality_audit", "remediation_queue", "list_changes", "decide", "promote", "organization_id_for_staff", "EventRadarStateConflict", "EventRadarSchemaUnavailable",
]
