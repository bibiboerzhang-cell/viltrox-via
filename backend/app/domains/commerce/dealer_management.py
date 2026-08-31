"""Dealer map-management validation, filtering, and mutation helpers.

``dealer_scrape`` remains the stable public facade.  The facade passes its
database and function seams into this module so existing callers and hermetic
tests can continue to replace ``get_conn``, ``get_dealer`` and related hooks.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from app.domains.commerce.dealer_directory_view import (
    DEALER_AUTHORIZATION_FILTERS,
    DEALER_CHANNEL_FILTERS,
    DEALER_EVIDENCE_FILTERS,
    DEALER_PRODUCT_FILTERS,
    REVIEWED_DEALER_PERSISTENCE_VERSION,
    validate_filter,
)


BRAND_TABLE = "vkpi_dealer_brand_relationships"
BRAND_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
BRAND_RELATIONSHIP_STATUSES = frozenset(
    {"unverified", "declared", "retailer_observed", "official_directory_listed"}
)
BRAND_AUTHORIZATION_STATUSES = frozenset({"unverified"})
VILTROX_DEPLOYMENT_STATUSES = frozenset(
    {"not_deployed", "planned", "deployed", "paused"}
)
ACTIVITY_STATUSES = frozenset({"unknown", "none_observed", "active"})


def str_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def http_url_or_none(value: Any, *, field: str) -> str | None:
    text = str_or_none(value)
    if text is None:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute http(s) URL")
    return text


def normalize_brand_key(value: Any) -> str:
    brand_key = str(value or "").strip().casefold().replace(" ", "_")
    if not BRAND_KEY_RE.fullmatch(brand_key):
        raise ValueError("brand must use 2-64 lowercase letters, numbers, '_' or '-'")
    return brand_key


def parse_bbox(value: Any) -> tuple[float, float, float, float] | None:
    """Parse ``west,south,east,north`` without accepting wrapped boxes."""
    text = str_or_none(value)
    if text is None:
        return None
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    try:
        west, south, east, north = (float(part) for part in parts)
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox must contain four numeric coordinates") from exc
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("bbox longitude must be between -180 and 180")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("bbox latitude must be between -90 and 90")
    if west > east or south > north:
        raise ValueError("bbox must satisfy west <= east and south <= north")
    return west, south, east, north


def staff_ref(actor_id: Any) -> str:
    try:
        numeric = int(actor_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("staff identity is required") from exc
    if numeric <= 0:
        raise ValueError("staff identity is required")
    return f"staff_{numeric}"


def iso_datetime_or_none(value: Any, *, field: str) -> str | None:
    text = str_or_none(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return text


def bounded_text(value: Any, *, field: str, maximum: int) -> str | None:
    text = str_or_none(value)
    if text is not None and len(text) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return text


def normalize_brand_relationships(
    values: Any,
    *,
    actor_id: Any,
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("brands must be an array")
    reviewer_id = staff_ref(actor_id)
    by_brand: dict[str, dict[str, Any]] = {}
    for raw in values:
        if isinstance(raw, str):
            brand_key = normalize_brand_key(raw)
            relationship = {
                "brand_key": brand_key,
                "relationship_status": "declared",
                "authorization_status": "unverified",
                "evidence_url": None,
                "source_checked_at": None,
                "reviewer_id": reviewer_id,
            }
        elif isinstance(raw, dict):
            brand_key = normalize_brand_key(raw.get("brand_key") or raw.get("brand"))
            relationship_status = (
                str_or_none(raw.get("relationship_status")) or "unverified"
            ).lower()
            if relationship_status not in BRAND_RELATIONSHIP_STATUSES:
                raise ValueError(
                    "brand relationship_status must be one of: "
                    + ", ".join(sorted(BRAND_RELATIONSHIP_STATUSES))
                )
            authorization_status = (
                str_or_none(raw.get("authorization_status")) or "unverified"
            ).lower()
            if authorization_status not in BRAND_AUTHORIZATION_STATUSES:
                raise ValueError(
                    "brand authorization_status must be one of: "
                    + ", ".join(sorted(BRAND_AUTHORIZATION_STATUSES))
                )
            relationship = {
                "brand_key": brand_key,
                "relationship_status": relationship_status,
                "authorization_status": authorization_status,
                "evidence_url": http_url_or_none(
                    raw.get("evidence_url"), field="brand evidence_url"
                ),
                "source_checked_at": iso_datetime_or_none(
                    raw.get("source_checked_at"), field="brand source_checked_at"
                ),
                "reviewer_id": reviewer_id,
            }
        else:
            raise ValueError("each brands item must be a string or object")
        by_brand[brand_key] = relationship
    return [by_brand[key] for key in sorted(by_brand)]


def normalize_social_links(values: Any) -> list[dict[str, str]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("social_links must be an array")
    if len(values) > 12:
        raise ValueError("social_links supports at most 12 entries")
    by_platform: dict[str, dict[str, str]] = {}
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("each social_links item must be an object")
        platform = str(raw.get("platform") or "").strip().casefold()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", platform):
            raise ValueError(
                "social_links platform must use 1-32 lowercase letters, "
                "numbers, '_' or '-'"
            )
        url = http_url_or_none(raw.get("url"), field="social_links.url")
        if url is None:
            raise ValueError("social_links.url is required")
        by_platform[platform] = {"platform": platform, "url": url}
    return [by_platform[key] for key in sorted(by_platform)]


def validate_new_dealer_management_fields_impl(
    changes: dict[str, Any],
    *,
    actor_id: Any,
    require_map_management: Callable[[], None],
) -> None:
    require_map_management()
    if "website_url" in changes:
        http_url_or_none(changes.get("website_url"), field="website_url")
    if "social_links" in changes:
        normalize_social_links(changes.get("social_links"))
    if "brands" in changes:
        normalize_brand_relationships(changes.get("brands"), actor_id=actor_id)
    if "viltrox_deployment" in changes:
        deployment = changes.get("viltrox_deployment")
        if not isinstance(deployment, dict):
            raise ValueError("viltrox_deployment must be an object")
        status = (str_or_none(deployment.get("status")) or "not_deployed").lower()
        if status not in VILTROX_DEPLOYMENT_STATUSES:
            raise ValueError(
                "viltrox_deployment.status must be one of: "
                + ", ".join(sorted(VILTROX_DEPLOYMENT_STATUSES))
            )
        if status == "paused":
            raise ValueError("a new dealer cannot start with a paused deployment")
        bounded_text(
            deployment.get("note"), field="viltrox_deployment.note", maximum=2000
        )
    if "activity" in changes:
        activity = changes.get("activity")
        if not isinstance(activity, dict):
            raise ValueError("activity must be an object")
        status = (str_or_none(activity.get("status")) or "unknown").lower()
        if status not in ACTIVITY_STATUSES:
            raise ValueError(
                "activity.status must be one of: "
                + ", ".join(sorted(ACTIVITY_STATUSES))
            )
        http_url_or_none(activity.get("page_url"), field="activity.page_url")
        iso_datetime_or_none(activity.get("checked_at"), field="activity.checked_at")
        iso_datetime_or_none(
            activity.get("next_event_at"), field="activity.next_event_at"
        )
        bounded_text(activity.get("note"), field="activity.note", maximum=2000)


def dealer_filter_sql(
    *,
    state: str | None,
    city: str | None,
    channel: str,
    evidence_status: str,
    product_evidence: str,
    authorization: str,
    brand: str | None = None,
    published_only: bool = False,
    bbox: tuple[float, float, float, float] | None = None,
    review_contract_enforced: bool = False,
    map_management_enforced: bool = False,
) -> tuple[str, list[Any], dict[str, Any]]:
    normalized = {
        "channel": validate_filter(channel, DEALER_CHANNEL_FILTERS, field="channel"),
        "evidence_status": validate_filter(
            evidence_status, DEALER_EVIDENCE_FILTERS, field="evidence_status"
        ),
        "product_evidence": validate_filter(
            product_evidence, DEALER_PRODUCT_FILTERS, field="product_evidence"
        ),
        "authorization": validate_filter(
            authorization, DEALER_AUTHORIZATION_FILTERS, field="authorization"
        ),
    }
    clauses: list[str] = []
    params: list[Any] = []
    st = str_or_none(state)
    if st:
        clauses.append("UPPER(TRIM(state)) = ?")
        params.append(st.upper())
    ct = str_or_none(city)
    if ct:
        clauses.append("LOWER(TRIM(city)) = LOWER(?)")
        params.append(ct)
    brand_key = normalize_brand_key(brand) if str_or_none(brand) else None
    normalized["brand"] = brand_key
    normalized["published_only"] = bool(published_only)
    normalized["bbox"] = bbox
    if brand_key:
        if map_management_enforced:
            clauses.append(
                f"EXISTS (SELECT 1 FROM {BRAND_TABLE} dealer_brand "
                "WHERE dealer_brand.dealer_id = vkpi_dealers.id "
                "AND dealer_brand.brand_key = ?)"
            )
            params.append(brand_key)
        else:
            clauses.append("1 = 0")
    if published_only and map_management_enforced:
        clauses.append("publication_status = 'published'")
    if bbox is not None:
        west, south, east, north = bbox
        clauses.extend(("lat BETWEEN ? AND ?", "lng BETWEEN ? AND ?"))
        params.extend((south, north, west, east))
    if normalized["evidence_status"] == "public_listing_verified":
        clauses.append("source_status = 'public_listing_verified'")
        if review_contract_enforced:
            clauses.append(
                f"review_contract_version = {REVIEWED_DEALER_PERSISTENCE_VERSION}"
            )
    elif normalized["evidence_status"] == "candidate":
        candidate_sql = "(source_status IS NULL OR source_status <> 'public_listing_verified')"
        if review_contract_enforced:
            candidate_sql = (
                f"({candidate_sql} OR review_contract_version <> "
                f"{REVIEWED_DEALER_PERSISTENCE_VERSION})"
            )
        clauses.append(candidate_sql)
    if normalized["product_evidence"] == "available":
        clauses.append("NULLIF(TRIM(brand_listing_url), '') IS NOT NULL")
    elif normalized["product_evidence"] == "missing":
        clauses.append("NULLIF(TRIM(brand_listing_url), '') IS NULL")
    if normalized["authorization"] == "confirmed":
        clauses.append("1 = 0")
    offline_sql = (
        "NULLIF(TRIM(address), '') IS NOT NULL AND "
        "NULLIF(TRIM(city), '') IS NOT NULL AND "
        "NULLIF(TRIM(state), '') IS NOT NULL"
    )
    online_sql = "NULLIF(TRIM(brand_listing_url), '') IS NOT NULL"
    if normalized["channel"] == "offline_location":
        clauses.append(f"({offline_sql})")
    elif normalized["channel"] == "online_product_page":
        clauses.append(online_sql)
    elif normalized["channel"] == "both":
        clauses.extend((f"({offline_sql})", online_sql))
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where_sql, params, normalized


def _append_basic_dealer_fields(
    changes: dict[str, Any], assignments: list[str], params: list[Any]
) -> None:
    basic_text_fields = {
        "name": 240,
        "address": 500,
        "city": 160,
        "state": 2,
        "country": 2,
        "source": 240,
        "postal_code": 32,
        "phone": 80,
        "contact_email": 320,
        "store_hours": 1000,
        "public_services": 2000,
        "verification_note": 2000,
    }
    for field, maximum in basic_text_fields.items():
        if field not in changes:
            continue
        text = bounded_text(changes.get(field), field=field, maximum=maximum)
        if field in {"name", "address"} and text is None:
            raise ValueError(f"{field} is required")
        if field in {"state", "country"} and text is not None:
            text = text.upper()
            if not re.fullmatch(r"[A-Z]{2}", text):
                raise ValueError(f"{field} must be a two-letter code")
        assignments.append(f"{field} = ?")
        params.append(text)


def _append_dealer_coordinates(
    changes: dict[str, Any],
    assignments: list[str],
    params: list[Any],
    *,
    current: Any,
    row_get: Callable[[Any, str, Any], Any],
    clean_lat: Callable[[Any], float | None],
    clean_lng: Callable[[Any], float | None],
) -> None:
    for field, cleaner in (("lat", clean_lat), ("lng", clean_lng)):
        if field not in changes:
            continue
        raw_value = changes.get(field)
        coordinate = cleaner(raw_value)
        if raw_value not in (None, "") and coordinate is None:
            raise ValueError(f"{field} is outside the valid coordinate range")
        if row_get(current, "publication_status", None) == "published" and coordinate is None:
            raise ValueError("unpublish the dealer before clearing map coordinates")
        assignments.append(f"{field} = ?")
        params.append(coordinate)


def _append_dealer_links(
    changes: dict[str, Any], assignments: list[str], params: list[Any]
) -> None:
    for field in ("location_source_url", "brand_listing_url"):
        if field in changes:
            assignments.append(f"{field} = ?")
            params.append(http_url_or_none(changes.get(field), field=field))
    if "website_url" in changes:
        assignments.append("website_url = ?")
        params.append(http_url_or_none(changes.get("website_url"), field="website_url"))
    if "social_links" in changes:
        assignments.append("social_links_json = ?::jsonb")
        params.append(
            json.dumps(
                normalize_social_links(changes.get("social_links")),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )


def _append_viltrox_deployment(
    changes: dict[str, Any],
    assignments: list[str],
    params: list[Any],
    *,
    current: Any,
    actor_ref: str,
    row_get: Callable[[Any, str, Any], Any],
) -> None:
    if "viltrox_deployment" not in changes:
        return
    deployment = changes.get("viltrox_deployment")
    if not isinstance(deployment, dict):
        raise ValueError("viltrox_deployment must be an object")
    status = (str_or_none(deployment.get("status")) or "not_deployed").lower()
    if status not in VILTROX_DEPLOYMENT_STATUSES:
        raise ValueError(
            "viltrox_deployment.status must be one of: "
            + ", ".join(sorted(VILTROX_DEPLOYMENT_STATUSES))
        )
    if status == "paused" and row_get(current, "viltrox_deployed_at", None) in (None, ""):
        raise ValueError("viltrox_deployment cannot be paused before deployment")
    note = bounded_text(
        deployment.get("note"), field="viltrox_deployment.note", maximum=2000
    ) or ""
    assignments.extend(("viltrox_deployment_status = ?", "viltrox_deployment_note = ?"))
    params.extend((status, note))
    if status == "deployed":
        assignments.extend(
            (
                "viltrox_deployed_at = COALESCE(viltrox_deployed_at, NOW())",
                "viltrox_deployed_by = ?",
            )
        )
        params.append(actor_ref)


def _append_dealer_activity(
    changes: dict[str, Any], assignments: list[str], params: list[Any]
) -> None:
    if "activity" not in changes:
        return
    activity = changes.get("activity")
    if not isinstance(activity, dict):
        raise ValueError("activity must be an object")
    status = (str_or_none(activity.get("status")) or "unknown").lower()
    if status not in ACTIVITY_STATUSES:
        raise ValueError(
            "activity.status must be one of: " + ", ".join(sorted(ACTIVITY_STATUSES))
        )
    page_url = http_url_or_none(activity.get("page_url"), field="activity.page_url")
    checked_at = iso_datetime_or_none(
        activity.get("checked_at"), field="activity.checked_at"
    )
    next_event_at = iso_datetime_or_none(
        activity.get("next_event_at"), field="activity.next_event_at"
    )
    note = bounded_text(activity.get("note"), field="activity.note", maximum=2000) or ""
    assignments.extend(
        (
            "activity_status = ?",
            "activity_page_url = ?",
            "activity_checked_at = ?",
            "next_activity_at = ?",
            "activity_note = ?",
        )
    )
    params.extend((status, page_url, checked_at, next_event_at, note))


def _persist_dealer_update(
    conn: Any,
    normalized_id: int,
    assignments: list[str],
    params: list[Any],
    brands: list[dict[str, Any]] | None,
    *,
    commit: bool,
) -> None:
    assignments.append("updated_at = NOW()")
    try:
        conn.execute(
            "UPDATE vkpi_dealers SET " + ", ".join(assignments) + " WHERE id = ?",
            params + [normalized_id],
        )
        if brands is not None:
            conn.execute(f"DELETE FROM {BRAND_TABLE} WHERE dealer_id = ?", (normalized_id,))
            for relationship in brands:
                conn.execute(
                    f"""
                    INSERT INTO {BRAND_TABLE}
                      (dealer_id, brand_key, relationship_status,
                       authorization_status, evidence_url, source_checked_at,
                       reviewer_id, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?, NOW(), NOW())
                    """,
                    (
                        normalized_id,
                        relationship["brand_key"],
                        relationship["relationship_status"],
                        relationship["authorization_status"],
                        relationship["evidence_url"],
                        relationship["source_checked_at"],
                        relationship["reviewer_id"],
                    ),
                )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()
        raise


def update_dealer_impl(
    dealer_id: Any,
    changes: dict[str, Any],
    *,
    actor_id: Any,
    connection: Any | None,
    commit: bool,
    return_entity: bool,
    require_map_management: Callable[[], None],
    get_conn: Callable[[], Any],
    get_dealer: Callable[[Any], dict[str, Any]],
    row_get: Callable[[Any, str, Any], Any],
    clean_lat: Callable[[Any], float | None],
    clean_lng: Callable[[Any], float | None],
) -> dict[str, Any]:
    require_map_management()
    try:
        normalized_id = int(dealer_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("dealer id must be a positive integer") from exc
    if normalized_id <= 0:
        raise ValueError("dealer id must be a positive integer")
    if not isinstance(changes, dict):
        raise ValueError("changes must be an object")
    actor_ref = staff_ref(actor_id)
    conn = connection or get_conn()
    current = conn.execute(
        """
        SELECT id, publication_status, viltrox_deployed_at
        FROM vkpi_dealers WHERE id = ? LIMIT 1
        """,
        (normalized_id,),
    ).fetchone()
    if current is None:
        raise LookupError("dealer not found")

    assignments: list[str] = []
    params: list[Any] = []
    _append_basic_dealer_fields(changes, assignments, params)
    _append_dealer_coordinates(
        changes,
        assignments,
        params,
        current=current,
        row_get=row_get,
        clean_lat=clean_lat,
        clean_lng=clean_lng,
    )
    _append_dealer_links(changes, assignments, params)
    _append_viltrox_deployment(
        changes,
        assignments,
        params,
        current=current,
        actor_ref=actor_ref,
        row_get=row_get,
    )
    _append_dealer_activity(changes, assignments, params)

    brands = None
    if "brands" in changes:
        brands = normalize_brand_relationships(changes.get("brands"), actor_id=actor_id)
    _persist_dealer_update(
        conn,
        normalized_id,
        assignments,
        params,
        brands,
        commit=commit,
    )
    if return_entity:
        return get_dealer(normalized_id)
    return {"id": normalized_id}


def create_managed_dealer_impl(
    payload: dict[str, Any],
    managed_fields: dict[str, Any],
    *,
    actor_id: Any,
    validate_new_fields: Callable[..., None],
    get_conn: Callable[[], Any],
    upsert_dealer: Callable[..., dict[str, Any]],
    update_dealer: Callable[..., dict[str, Any]],
    get_dealer: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    validate_new_fields(managed_fields, actor_id=actor_id)
    conn = get_conn()
    create_payload = dict(payload)
    create_payload["_create_only"] = True
    try:
        created = upsert_dealer(create_payload, _connection=conn, _commit=False)
        dealer_id = created.get("id")
        if dealer_id in (None, ""):
            raise RuntimeError("dealer create did not return an id")
        update_dealer(
            dealer_id,
            managed_fields,
            actor_id=actor_id,
            _connection=conn,
            _commit=False,
            _return_entity=False,
        )
        conn.commit()
    except Exception:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        raise
    return get_dealer(dealer_id)


def set_dealer_publication_impl(
    dealer_id: Any,
    *,
    published: bool,
    actor_id: Any,
    require_map_management: Callable[[], None],
    get_conn: Callable[[], Any],
    get_dealer: Callable[[Any], dict[str, Any]],
    row_get: Callable[[Any, str, Any], Any],
) -> dict[str, Any]:
    require_map_management()
    try:
        normalized_id = int(dealer_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("dealer id must be a positive integer") from exc
    if normalized_id <= 0:
        raise ValueError("dealer id must be a positive integer")
    actor_ref = staff_ref(actor_id)
    conn = get_conn()
    current = conn.execute(
        """
        SELECT id, name, address, city, state, lat, lng
        FROM vkpi_dealers WHERE id = ? LIMIT 1
        """,
        (normalized_id,),
    ).fetchone()
    if current is None:
        raise LookupError("dealer not found")
    if published:
        required = ("name", "address", "city", "state", "lat", "lng")
        missing = [field for field in required if row_get(current, field, None) in (None, "")]
        if missing:
            raise ValueError("dealer map publication requires: " + ", ".join(missing))
        conn.execute(
            """
            UPDATE vkpi_dealers
            SET publication_status = 'published',
                published_at = COALESCE(published_at, NOW()),
                published_by = ?, updated_at = NOW()
            WHERE id = ?
            """,
            (actor_ref, normalized_id),
        )
    else:
        conn.execute(
            """
            UPDATE vkpi_dealers
            SET publication_status = 'draft', updated_at = NOW()
            WHERE id = ?
            """,
            (normalized_id,),
        )
    conn.commit()
    return get_dealer(normalized_id)
