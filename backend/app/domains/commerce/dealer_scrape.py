"""V-KPI Dealer Map 公开零售商候选导入 + 落库。

迁移 144(vkpi_dealers,UNIQUE(name,address))。本模块只产地理数据 —— name /
address / city / state / lat / lng / source。无 touches_v6_fit、无评分;与 KOL Pool /
评分域物理隔离,绝不碰 viltrox_fit_score / rule_v0。

核心约束(有界、合规、不滥抓):
- scrape_dealers_enqueue(limit, record_only, source):单批 HARD CAP <= _MAX_BATCH(20)。
  record_only=True(默认)=纯预检；record_only=False=导入已人工核验的公开候选。
- 候选必须同时有零售商官方品牌/产品页与官方门店地址页；这只能证明“公开在售 +
  门店存在”，不能证明 Viltrox 官方授权，authorization_status 永远单独记录。
- upsert_dealer(payload):INSERT ... ON CONFLICT (name,address) DO UPDATE,幂等。
- geocode 复用既有 city_coords.resolve_city(),不引新 geocoder;解析不到 → lat/lng
  留 NULL 待补,行仍落库。
- 仅持久化导入落 scrape 审计；record_only 预检绝不访问或写入数据库。

DB 全走 get_conn() + '?' 占位 + 显式 conn.commit();SQL 禁裸 %。
"""
from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.commerce.dealer_coverage_repository import (
    dealer_coverage_summary as _dealer_coverage_summary,
)
from app.domains.commerce.dealer_directory_view import (
    DEALER_AUTHORIZATION_FILTERS,
    DEALER_CHANNEL_FILTERS,
    DEALER_EVIDENCE_FILTERS,
    DEALER_PRODUCT_FILTERS,
    MANAGED_DEALER_DURABLE_FIELDS,
    REVIEWED_DEALER_DURABLE_FIELDS,
    REVIEWED_DEALER_PERSISTENCE_VERSION,
    build_dealer_pins,
    dealer_matches,
    project_dealer,
    reviewed_persistence_contract,
    validate_filter,
)
from app.domains.commerce.dealer_import_workflow import (
    record_scrape_audit_impl,
    scrape_dealers_enqueue_impl,
)
from app.domains.commerce.dealer_management import (
    bounded_text as _bounded_text,
    create_managed_dealer_impl,
    dealer_filter_sql as _dealer_filter_sql,
    http_url_or_none as _http_url_or_none,
    iso_datetime_or_none as _iso_datetime_or_none,
    normalize_brand_key as _normalize_brand_key,
    normalize_brand_relationships as _normalize_brand_relationships,
    normalize_social_links as _normalize_social_links,
    parse_bbox,
    set_dealer_publication_impl,
    staff_ref as _staff_ref,
    str_or_none as _str_or_none,
    update_dealer_impl,
    validate_new_dealer_management_fields_impl,
)
from app.domains.dashboard.city_coords import resolve_city

logger = get_logger(__name__)

# 单批硬上限 —— 任何 limit 都被 clamp 到 [1, _MAX_BATCH],杜绝全美 blast。
_MAX_BATCH = 20
# 每条请求之间的限速(秒),真跑路径用,礼貌且不滥抓。
_SLEEP_BETWEEN = 0.5
_AUDIT_TABLE = "vkpi_dealers_scrape_audit"
_BRAND_TABLE = "vkpi_dealer_brand_relationships"


class DealerAlreadyExists(ValueError):
    """A create-only request matched an existing name/address identity."""

    def __init__(self, dealer_id: Any):
        self.dealer_id = dealer_id
        super().__init__("dealer already exists; use PATCH to edit it")


# 2026-07-13 逐页核验的最小候选集。只保留双来源链完整的条目；旧的 20 条静态
# 地址中包含已关闭/错城/无法证明在售的记录，已全部移除，不再作为“抓取回退”。
_REVIEWED_PUBLIC_RETAILERS: list[dict[str, Any]] = [
    {
        "name": "B&H Photo Video · NYC SuperStore",
        "address": "420 9th Ave", "city": "New York", "state": "NY", "postal_code": "10001", "country": "US",
        "phone": "212-444-6615",
        "store_hours": "Mon-Thu 10am-7pm; Fri 10am-2pm; Sat closed; Sun 10am-6pm",
        "public_services": "Camera retail; store pickup; expert advice",
        "brand_listing_url": "https://www.bhphotovideo.com/c/browse/viltrox/ci/58790",
        "location_source_url": "https://www.bhphotovideo.com/find/HelpCenter/StoreInfo.jsp",
    },
    {
        "name": "Adorama · NYC Store",
        "address": "42 W 18th St", "city": "New York", "state": "NY", "postal_code": "10011", "country": "US",
        "phone": "212-741-0063",
        "store_hours": "Mon-Thu 10am-7pm; Fri 10am-5pm; Sat closed; Sun 10am-5pm",
        "public_services": "Camera retail; store pickup; expert advice; trade-in",
        "brand_listing_url": "https://www.adorama.com/brands/Viltrox",
        "location_source_url": "https://www.adorama.com/g/nyc-store",
    },
    {
        "name": "Samy's Camera · Los Angeles",
        "address": "431 S Fairfax Ave", "city": "Los Angeles", "state": "CA", "postal_code": "90036", "country": "US",
        "phone": "323-938-2420", "contact_email": "lacamera@samys.com",
        "store_hours": "Mon-Sat 10am-6pm; Sun closed",
        "public_services": "Camera sales; video sales; photo classes; rentals",
        "brand_listing_url": "https://www.samys.com/p/Video-Monitors--Viewfinders/DC-X3/Viltrox-6-in.-Touchscreen-HDMI/SDI-On-Camera-Monitor/268996.html",
        "location_source_url": "https://www.samys.com/stores",
    },
    {
        "name": "Samy's Camera · Orange County",
        "address": "3309B S Bristol St", "city": "Santa Ana", "state": "CA", "postal_code": "92704", "country": "US",
        "phone": "714-557-9400", "contact_email": "infosa@samys.com",
        "store_hours": "Mon-Sat 10am-6:30pm; Sun closed",
        "public_services": "Camera sales; video sales; photo classes; rentals",
        "brand_listing_url": "https://www.samys.com/p/Video-Monitors--Viewfinders/DC-X3/Viltrox-6-in.-Touchscreen-HDMI/SDI-On-Camera-Monitor/268996.html",
        "location_source_url": "https://www.samys.com/stores",
    },
    {
        "name": "Samy's Camera · Pasadena",
        "address": "1759 E Colorado Blvd", "city": "Pasadena", "state": "CA", "postal_code": "91106", "country": "US",
        "phone": "626-796-3300", "contact_email": "pasadena@samys.com",
        "store_hours": "Mon-Sat 10am-6:30pm; Sun closed",
        "public_services": "Camera sales; video sales; photo classes; rentals",
        "brand_listing_url": "https://www.samys.com/p/Video-Monitors--Viewfinders/DC-X3/Viltrox-6-in.-Touchscreen-HDMI/SDI-On-Camera-Monitor/268996.html",
        "location_source_url": "https://www.samys.com/stores",
    },
]


def _clean_lat(value: Any) -> float | None:
    """合法纬度才返回(兜底,镜像表级 CHECK lat BETWEEN -90 AND 90)。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if -90.0 <= f <= 90.0 else None


def _clean_lng(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if -180.0 <= f <= 180.0 else None


def _geocode(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """复用 city_coords.resolve_city(US, city, state, address) → (lat,lng) 或 (None,None)。"""
    if payload.get("lat") not in (None, "") and payload.get("lng") not in (None, ""):
        return _clean_lat(payload.get("lat")), _clean_lng(payload.get("lng"))
    found = resolve_city(
        "US",
        payload.get("city"),
        payload.get("state"),
        payload.get("address"),
        payload.get("name"),
    )
    if not found:
        return None, None
    return _clean_lat(found.get("lat")), _clean_lng(found.get("lng"))


def _clamp_limit(limit: Any) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = _MAX_BATCH
    return max(1, min(n, _MAX_BATCH))


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _dealer_table_columns() -> set[str]:
    """Inspect the active Dealer schema without mutating it."""
    if not table_exists("vkpi_dealers"):
        return set()
    conn = get_conn()
    try:
        if is_postgres_runtime():
            rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name=?
                """,
                ("vkpi_dealers",),
            ).fetchall()
            return {
                str(_row_get(row, "column_name") or "").strip()
                for row in rows
                if str(_row_get(row, "column_name") or "").strip()
            }
        rows = conn.execute("PRAGMA table_info(vkpi_dealers)").fetchall()
        return {
            str(_row_get(row, "name", _row_get(row, 1, "")) or "").strip()
            for row in rows
            if str(_row_get(row, "name", _row_get(row, 1, "")) or "").strip()
        }
    except Exception:
        logger.warning("dealer reviewed persistence schema inspection failed", exc_info=True)
        return set()


def reviewed_persistence_status() -> dict[str, Any]:
    """Return the active DB's read-only reviewed-Dealer persistence status."""
    # The no-argument call is always fail-closed in production, but preserves
    # the existing pure seam used by hermetic tests and emergency overrides.
    contract = reviewed_persistence_contract()
    if not contract.get("supported"):
        contract = reviewed_persistence_contract(_dealer_table_columns())
    return {
        **contract,
        "read_only": True,
        "database_accessed": True,
        "business_rows_written": 0,
    }


def dealer_map_management_status() -> dict[str, Any]:
    """Return migration-260 readiness without mutating the active database."""
    columns = _dealer_table_columns()
    missing = sorted(MANAGED_DEALER_DURABLE_FIELDS.difference(columns))
    brand_table_ready = table_exists(_BRAND_TABLE)
    return {
        "supported": not missing and brand_table_ready,
        "status": "ready" if not missing and brand_table_ready else "migration_required",
        "missing_dealer_fields": missing,
        "brand_relationship_table_ready": brand_table_ready,
        "publication_is_authorization": False,
        "viltrox_deployment_is_authorization": False,
        "viltrox_deployment_is_inventory": False,
    }


def _require_map_management() -> None:
    readiness = dealer_map_management_status()
    if not readiness["supported"]:
        raise ValueError("dealer map management migration is required")


def validate_new_dealer_management_fields(
    changes: dict[str, Any],
    *,
    actor_id: Any,
) -> None:
    """Validate managed create fields before the base draft row is written."""
    validate_new_dealer_management_fields_impl(
        changes,
        actor_id=actor_id,
        require_map_management=_require_map_management,
    )


def _load_brand_relationships(dealer_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not dealer_ids or not table_exists(_BRAND_TABLE):
        return {}
    placeholders = ",".join("?" for _ in dealer_ids)
    rows = get_conn().execute(
        f"""
        SELECT dealer_id, brand_key, relationship_status, authorization_status,
               evidence_url, source_checked_at
        FROM {_BRAND_TABLE}
        WHERE dealer_id IN ({placeholders})
        ORDER BY dealer_id, brand_key
        """,
        dealer_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        dealer_id = int(_row_get(row, "dealer_id", 0) or 0)
        if dealer_id <= 0:
            continue
        grouped.setdefault(dealer_id, []).append(
            {
                "brand_key": _row_get(row, "brand_key"),
                "relationship_status": _row_get(row, "relationship_status"),
                "authorization_status": _row_get(row, "authorization_status"),
                "evidence_url": _row_get(row, "evidence_url"),
                "source_checked_at": _row_get(row, "source_checked_at"),
            }
        )
    return grouped


def _attach_brand_relationships(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dealer_ids = [int(row["id"]) for row in rows if row.get("id") not in (None, "")]
    grouped = _load_brand_relationships(dealer_ids)
    for row in rows:
        try:
            dealer_id = int(row.get("id") or 0)
        except (TypeError, ValueError):
            dealer_id = 0
        row["brand_relationships"] = grouped.get(dealer_id, [])
    return rows


def _reviewed_evidence_receipt(
    payload: dict[str, Any],
) -> tuple[str, str, str, str, str]:
    """Validate and serialize one bounded descriptive review receipt."""
    from app.domains.events import radar_quality

    # Re-audit the exact row at the final write boundary.  A caller-supplied
    # "prevalidated" flag would make this gate forgeable.
    quality = radar_quality.audit_dealer_candidates([payload])
    if not isinstance(quality, dict) or not bool(
        (quality.get("import_gate") or {}).get("allowed")
    ):
        raise ValueError("reviewed dealer evidence contract is not import eligible")

    source_id = _str_or_none(payload.get("source_id")) or ""
    stable_org_key = _str_or_none(payload.get("stable_org_key")) or ""
    stable_location_key = _str_or_none(payload.get("stable_location_key")) or ""
    reviewer_id = _str_or_none(payload.get("reviewer_id")) or ""
    evidence = {
        "claim_status": "descriptive_only",
        "source": {
            "source_id": source_id,
            "source_url": _str_or_none(payload.get("location_source_url")),
            "observed_at": _str_or_none(payload.get("source_checked_at")),
            "reviewer_id": reviewer_id,
            "evidence_scope": _str_or_none(payload.get("evidence_scope")),
            "value_status": _str_or_none(payload.get("value_status")),
        },
        "product": (
            deepcopy(payload.get("viltrox_product_evidence"))
            if isinstance(payload.get("viltrox_product_evidence"), dict)
            else {}
        ),
        "contact": (
            deepcopy(payload.get("contact_evidence"))
            if isinstance(payload.get("contact_evidence"), dict)
            else {}
        ),
        "social": (
            deepcopy(payload.get("social_evidence"))
            if isinstance(payload.get("social_evidence"), dict)
            else {}
        ),
        "activity": (
            deepcopy(payload.get("activity_evidence"))
            if isinstance(payload.get("activity_evidence"), dict)
            else {}
        ),
    }
    return (
        source_id,
        stable_org_key,
        stable_location_key,
        reviewer_id,
        json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


# ── 单条幂等 upsert ──────────────────────────────────────────────────────
def upsert_dealer(
    payload: dict[str, Any],
    *,
    ingest_class: str = "manual_reference",
    _connection: Any | None = None,
    _commit: bool = True,
) -> dict[str, Any]:
    """以 (name,address) 为键幂等 upsert 一个经销商。

    geocode 复用 resolve_city();解析不到 → lat/lng 写 NULL(待补)。
    返回 {ok, name, address, geocoded}。name/address 缺失 → ValueError。
    """
    payload = payload or {}
    create_only = payload.get("_create_only") is True
    name = _str_or_none(payload.get("name"))
    address = _str_or_none(payload.get("address"))
    if not name:
        raise ValueError("dealer name is required")
    if not address:
        raise ValueError("dealer address is required")

    city = _str_or_none(payload.get("city"))
    state = _str_or_none(payload.get("state"))
    source = _str_or_none(payload.get("source"))
    country = _str_or_none(payload.get("country")) or "US"
    brand_listing_url = _http_url_or_none(
        payload.get("brand_listing_url"), field="brand_listing_url"
    )
    location_source_url = _http_url_or_none(
        payload.get("location_source_url"), field="location_source_url"
    )
    ingest_class = str(ingest_class or "manual_reference").strip().lower()
    reviewed_public_listing = ingest_class == "reviewed_public_listing"
    # Public product/location evidence and Viltrox authorization are separate.
    # Caller-controlled fields can never self-promote either truth state.
    source_status = "public_listing_verified" if reviewed_public_listing else "unverified"
    authorization_status = "needs_viltrox_confirmation"
    verification_note = _str_or_none(payload.get("verification_note")) or ""
    postal_code = _str_or_none(payload.get("postal_code"))
    phone = _str_or_none(payload.get("phone"))
    contact_email = _str_or_none(payload.get("contact_email"))
    store_hours = _str_or_none(payload.get("store_hours"))
    public_services = _str_or_none(payload.get("public_services"))
    lat, lng = _geocode(payload)

    review_columns = ""
    review_values = ""
    review_updates = ""
    review_returning = ""
    review_params: tuple[Any, ...] = ()
    if reviewed_public_listing:
        persistence = reviewed_persistence_status()
        if not persistence["supported"]:
            raise ValueError(
                "dealer reviewed persistence schema unavailable: "
                + str(persistence.get("reason") or "migration_required")
            )
        source_id, stable_org_key, stable_location_key, reviewer_id, evidence_json = (
            _reviewed_evidence_receipt(payload)
        )
        review_columns = (
            ", source_id, stable_org_key, stable_location_key, reviewer_id, "
            "reviewed_at, evidence_json, review_contract_version"
        )
        review_values = ", ?, ?, ?, ?, NOW(), ?::jsonb, ?"
        review_updates = """,
            source_id = excluded.source_id,
            stable_org_key = excluded.stable_org_key,
            stable_location_key = excluded.stable_location_key,
            reviewer_id = excluded.reviewer_id,
            reviewed_at = excluded.reviewed_at,
            evidence_json = excluded.evidence_json,
            review_contract_version = excluded.review_contract_version"""
        review_returning = ", review_contract_version"
        review_params = (
            source_id,
            stable_org_key,
            stable_location_key,
            reviewer_id,
            evidence_json,
            REVIEWED_DEALER_PERSISTENCE_VERSION,
        )

    conn = _connection or get_conn()
    existing_row = conn.execute(
        "SELECT id FROM vkpi_dealers WHERE name = ? AND address = ? LIMIT 1",
        (name, address),
    ).fetchone()
    existed = existing_row is not None
    if create_only and existed:
        raise DealerAlreadyExists(_row_get(existing_row, "id"))
    persisted = conn.execute(
        f"""
        INSERT INTO vkpi_dealers
          (name, address, city, state, lat, lng, source, country,
           brand_listing_url, location_source_url, source_status,
           authorization_status, source_checked_at, verification_note,
           postal_code, phone, contact_email, store_hours, public_services
           {review_columns}, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                {review_values}, NOW())
        ON CONFLICT (name, address) DO UPDATE SET
            city = COALESCE(excluded.city, vkpi_dealers.city),
            state = COALESCE(excluded.state, vkpi_dealers.state),
            lat = COALESCE(excluded.lat, vkpi_dealers.lat),
            lng = COALESCE(excluded.lng, vkpi_dealers.lng),
            source = COALESCE(excluded.source, vkpi_dealers.source),
            country = COALESCE(excluded.country, vkpi_dealers.country),
            brand_listing_url = COALESCE(excluded.brand_listing_url, vkpi_dealers.brand_listing_url),
            location_source_url = COALESCE(excluded.location_source_url, vkpi_dealers.location_source_url),
            source_status = CASE
                WHEN vkpi_dealers.source_status = 'public_listing_verified'
                 AND excluded.source_status = 'unverified'
                THEN vkpi_dealers.source_status ELSE excluded.source_status END,
            authorization_status = CASE
                WHEN vkpi_dealers.source_status = 'public_listing_verified'
                 AND excluded.source_status = 'unverified'
                THEN vkpi_dealers.authorization_status ELSE excluded.authorization_status END,
            source_checked_at = CASE
                WHEN vkpi_dealers.source_status = 'public_listing_verified'
                 AND excluded.source_status = 'unverified'
                THEN vkpi_dealers.source_checked_at ELSE excluded.source_checked_at END,
            verification_note = CASE
                WHEN vkpi_dealers.source_status = 'public_listing_verified'
                 AND excluded.source_status = 'unverified'
                THEN vkpi_dealers.verification_note ELSE excluded.verification_note END,
            postal_code = COALESCE(excluded.postal_code, vkpi_dealers.postal_code),
            phone = COALESCE(excluded.phone, vkpi_dealers.phone),
            contact_email = COALESCE(excluded.contact_email, vkpi_dealers.contact_email),
            store_hours = COALESCE(excluded.store_hours, vkpi_dealers.store_hours),
            public_services = COALESCE(excluded.public_services, vkpi_dealers.public_services)
            {review_updates}
        RETURNING id, source_status, authorization_status, source_checked_at,
                  verification_note{review_returning}
        """,
        (
            name, address, city, state, lat, lng, source, country,
            brand_listing_url, location_source_url, source_status,
            authorization_status,
            _str_or_none(payload.get("source_checked_at")) if reviewed_public_listing else None,
            verification_note,
            postal_code, phone, contact_email, store_hours, public_services,
        ) + review_params,
    ).fetchone()
    if _commit:
        conn.commit()
    persisted_source_status = _str_or_none(_row_get(persisted, "source_status")) or source_status
    persisted_authorization = (
        _str_or_none(_row_get(persisted, "authorization_status")) or authorization_status
    )
    persisted_note_value = _row_get(persisted, "verification_note", verification_note)
    persisted_note = str(persisted_note_value or "")
    return {
        "ok": True,
        "id": _row_get(persisted, "id"),
        "name": name,
        "address": address,
        "inserted": not existed,
        "geocoded": lat is not None and lng is not None,
        "source_status": persisted_source_status,
        "authorization_status": persisted_authorization,
        "verification_note": persisted_note,
        "review_contract_version": int(
            _row_get(persisted, "review_contract_version", 0) or 0
        ),
    }


# ── 只读列出 ─────────────────────────────────────────────────────────────
def list_dealers(
    limit: int = 100,
    state: str | None = None,
    *,
    city: str | None = None,
    channel: str = "all",
    evidence_status: str = "all",
    product_evidence: str = "all",
    authorization: str = "all",
    brand: str | None = None,
    published_only: bool = False,
    bbox: tuple[float, float, float, float] | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List Dealer rows with truth-bounded filters and projections.

    ``online_product_page`` means a public retailer URL was recorded.  It does
    not mean current stock or a completed sale.  ``public_listing_verified``
    and official Viltrox authorization evidence remain independent filters.
    """
    if not table_exists("vkpi_dealers"):
        return []
    table_columns = _dealer_table_columns()
    review_contract_enforced = REVIEWED_DEALER_DURABLE_FIELDS.issubset(table_columns)
    map_management_enforced = (
        MANAGED_DEALER_DURABLE_FIELDS.issubset(table_columns)
        and table_exists(_BRAND_TABLE)
    )
    reviewed_metadata_columns = (
        "source_id",
        "stable_org_key",
        "stable_location_key",
        "reviewer_id",
        "reviewed_at",
        "evidence_json",
        "review_contract_version",
    )
    reviewed_select = (
        ", " + ", ".join(reviewed_metadata_columns)
        if review_contract_enforced
        else ""
    )
    managed_select = (
        ", " + ", ".join(sorted(MANAGED_DEALER_DURABLE_FIELDS))
        if map_management_enforced
        else ""
    )
    safe_limit = max(1, min(int(limit or 100), 5000))
    safe_offset = max(0, int(offset or 0))
    where_sql, params, normalized = _dealer_filter_sql(
        state=state,
        city=city,
        channel=channel,
        evidence_status=evidence_status,
        product_evidence=product_evidence,
        authorization=authorization,
        brand=brand,
        published_only=published_only,
        bbox=bbox,
        review_contract_enforced=review_contract_enforced,
        map_management_enforced=map_management_enforced,
    )

    # SQL performs bounded pagination; the pure projection remains the single
    # source of display semantics and a final fail-closed check.
    params.extend((safe_limit, safe_offset))
    rows = get_conn().execute(
        f"""
        SELECT id, name, address, city, state, lat, lng, source, country,
               brand_listing_url, location_source_url, source_status,
               authorization_status, source_checked_at, verification_note,
               postal_code, phone, contact_email, store_hours, public_services,
               created_at{reviewed_select}{managed_select}
        FROM vkpi_dealers
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()
    raw_rows = _attach_brand_relationships([dict(row) for row in rows])
    projected = [project_dealer(row) for row in raw_rows]
    filtered = [
        row
        for row in projected
        if dealer_matches(
            row,
            state=state,
            city=city,
            channel=normalized["channel"],
            evidence_status=normalized["evidence_status"],
            product_evidence=normalized["product_evidence"],
            authorization=normalized["authorization"],
        )
    ]
    return filtered


def get_dealer(dealer_id: Any) -> dict[str, Any]:
    """Return one Dealer with brand, publication, deployment and activity data."""
    try:
        normalized_id = int(dealer_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("dealer id must be a positive integer") from exc
    if normalized_id <= 0:
        raise ValueError("dealer id must be a positive integer")
    if not table_exists("vkpi_dealers"):
        raise LookupError("dealer not found")
    table_columns = _dealer_table_columns()
    review_contract_enforced = REVIEWED_DEALER_DURABLE_FIELDS.issubset(table_columns)
    map_management_enforced = MANAGED_DEALER_DURABLE_FIELDS.issubset(table_columns)
    reviewed_select = (
        ", "
        + ", ".join(
            (
                "source_id",
                "stable_org_key",
                "stable_location_key",
                "reviewer_id",
                "reviewed_at",
                "evidence_json",
                "review_contract_version",
            )
        )
        if review_contract_enforced
        else ""
    )
    managed_select = (
        ", " + ", ".join(sorted(MANAGED_DEALER_DURABLE_FIELDS))
        if map_management_enforced
        else ""
    )
    row = get_conn().execute(
        f"""
        SELECT id, name, address, city, state, lat, lng, source, country,
               brand_listing_url, location_source_url, source_status,
               authorization_status, source_checked_at, verification_note,
               postal_code, phone, contact_email, store_hours, public_services,
               created_at{reviewed_select}{managed_select}
        FROM vkpi_dealers
        WHERE id = ?
        LIMIT 1
        """,
        (normalized_id,),
    ).fetchone()
    if row is None:
        raise LookupError("dealer not found")
    raw = _attach_brand_relationships([dict(row)])[0]
    return project_dealer(raw)


def update_dealer(
    dealer_id: Any,
    changes: dict[str, Any],
    *,
    actor_id: Any,
    _connection: Any | None = None,
    _commit: bool = True,
    _return_entity: bool = True,
) -> dict[str, Any]:
    """Manager edit with one transaction and fail-closed truth boundaries."""
    return update_dealer_impl(
        dealer_id,
        changes,
        actor_id=actor_id,
        connection=_connection,
        commit=_commit,
        return_entity=_return_entity,
        require_map_management=_require_map_management,
        get_conn=get_conn,
        get_dealer=get_dealer,
        row_get=_row_get,
        clean_lat=_clean_lat,
        clean_lng=_clean_lng,
    )


def create_managed_dealer(
    payload: dict[str, Any],
    managed_fields: dict[str, Any],
    *,
    actor_id: Any,
) -> dict[str, Any]:
    """Create the base row and managed extensions in one database transaction."""
    return create_managed_dealer_impl(
        payload,
        managed_fields,
        actor_id=actor_id,
        validate_new_fields=validate_new_dealer_management_fields,
        get_conn=get_conn,
        upsert_dealer=upsert_dealer,
        update_dealer=update_dealer,
        get_dealer=get_dealer,
    )


def set_dealer_publication(
    dealer_id: Any,
    *,
    published: bool,
    actor_id: Any,
) -> dict[str, Any]:
    """Publish/unpublish a map pin without upgrading any evidence status."""
    return set_dealer_publication_impl(
        dealer_id,
        published=published,
        actor_id=actor_id,
        require_map_management=_require_map_management,
        get_conn=get_conn,
        get_dealer=get_dealer,
        row_get=_row_get,
    )


def count_dealers(
    *,
    state: str | None = None,
    city: str | None = None,
    channel: str = "all",
    evidence_status: str = "all",
    product_evidence: str = "all",
    authorization: str = "all",
    brand: str | None = None,
    published_only: bool = False,
    bbox: tuple[float, float, float, float] | None = None,
) -> int:
    """Return the total matching Dealer rows independently of page size."""
    if not table_exists("vkpi_dealers"):
        return 0
    table_columns = _dealer_table_columns()
    review_contract_enforced = REVIEWED_DEALER_DURABLE_FIELDS.issubset(
        table_columns
    )
    map_management_enforced = (
        MANAGED_DEALER_DURABLE_FIELDS.issubset(table_columns)
        and table_exists(_BRAND_TABLE)
    )
    where_sql, params, _normalized = _dealer_filter_sql(
        state=state,
        city=city,
        channel=channel,
        evidence_status=evidence_status,
        product_evidence=product_evidence,
        authorization=authorization,
        brand=brand,
        published_only=published_only,
        bbox=bbox,
        review_contract_enforced=review_contract_enforced,
        map_management_enforced=map_management_enforced,
    )
    row = get_conn().execute(
        f"SELECT COUNT(*) AS n FROM vkpi_dealers {where_sql}",
        params,
    ).fetchone()
    return int(_row_get(row, "n", 0) or 0)


def list_dealer_pins(
    color: str = "#10b981",
    *,
    state: str | None = None,
    city: str | None = None,
    channel: str = "all",
    evidence_status: str = "all",
    product_evidence: str = "all",
    authorization: str = "all",
    brand: str | None = None,
    published_only: bool = True,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict[str, Any]]:
    """Return map pins with explicit publication and evidence truth.

    After migration 260, manager-published manual rows may appear as pins but
    remain visibly ``candidate/unverified``.  Before migration 260 this keeps
    the legacy behavior and returns reviewed public listings only.
    """
    evidence_value = validate_filter(
        evidence_status, DEALER_EVIDENCE_FILTERS, field="evidence_status"
    )
    table_columns = _dealer_table_columns()
    map_management_enforced = (
        MANAGED_DEALER_DURABLE_FIELDS.issubset(table_columns)
        and table_exists(_BRAND_TABLE)
    )
    # Legacy schemas have no explicit manager publication receipt; preserve
    # the earlier reviewed-only rule.  Migration 260 makes candidate map
    # publication explicit and independently auditable.
    if not map_management_enforced and evidence_value == "candidate":
        return []
    page_size = 5000
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = list_dealers(
            limit=page_size,
            offset=offset,
            state=state,
            city=city,
            channel=channel,
            evidence_status=(
                evidence_value if map_management_enforced else "public_listing_verified"
            ),
            product_evidence=product_evidence,
            authorization=authorization,
            brand=brand,
            published_only=published_only,
            bbox=bbox,
        )
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return build_dealer_pins(rows, color=color)


def dealer_coverage_summary(
    *,
    organization_id: int = 1,
    stale_after_days: int = 30,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return a truth-bounded coverage summary for registered Dealer rows."""
    return _dealer_coverage_summary(
        organization_id=organization_id,
        stale_after_days=stale_after_days,
        as_of_value=as_of,
        get_conn=get_conn,
        table_exists=table_exists,
        table_columns=_dealer_table_columns,
        clean_lat=_clean_lat,
        clean_lng=_clean_lng,
    )


# ── 审计(record-only,容错) ──────────────────────────────────────────────
def _record_scrape_audit(
    *,
    source: str,
    requested: int,
    inserted: int,
    skipped: int,
    geocoded: int,
    pending_geocode: int,
    record_only: bool,
    errors: list[Any],
) -> None:
    """落一行 scrape run 审计。缺表静默 return;任何失败仅 warning,绝不拖垮主流程。"""
    record_scrape_audit_impl(
        source=source,
        requested=requested,
        inserted=inserted,
        skipped=skipped,
        geocoded=geocoded,
        pending_geocode=pending_geocode,
        record_only=record_only,
        errors=errors,
        audit_table=_AUDIT_TABLE,
        table_exists=table_exists,
        get_conn=get_conn,
        logger=logger,
    )


# ── 有界抓取 enqueue ─────────────────────────────────────────────────────
def reviewed_candidates() -> list[dict[str, Any]]:
    """Return defensive copies of the bundled candidates; never perform I/O."""
    return deepcopy(_REVIEWED_PUBLIC_RETAILERS)


def reviewed_candidates_quality_audit() -> dict[str, Any]:
    """Run the production-domain read-only quality contract over current rows."""
    from app.domains.events import radar_quality

    return radar_quality.audit_dealer_candidates(reviewed_candidates())


def reviewed_candidates_remediation_queue() -> dict[str, Any]:
    """Return deterministic Dealer evidence work items without DB or network."""
    from app.domains.events import radar_quality

    return radar_quality.build_dealer_remediation_queue(reviewed_candidates())


def _fetch_candidates(source: str, limit: int) -> list[dict[str, Any]]:
    """返回已人工核验的公开候选(<= limit)，本函数不发网络请求。"""
    del source
    capped = _clamp_limit(limit)
    return reviewed_candidates()[:capped]


def scrape_dealers_enqueue(
    limit: int = _MAX_BATCH,
    record_only: bool = True,
    source: str | None = None,
) -> dict[str, Any]:
    """有界触发经销商抓取(单批 HARD CAP <= 20)。

    record_only=True(默认):纯预检 —— 只产 plan,绝不发任何网络请求、绝不写库。
      返回 {ok, source, requested, inserted=0, skipped, geocoded(plan), pending_geocode(plan),
            record_only=True, plan:[...], errors:[]}。
    record_only=False:真跑 —— 逐条 upsert_dealer()(幂等),每条之间 time.sleep() 限速,
      geocode 走 resolve_city()。重复跑同一批只更新、不重复计(UNIQUE(name,address))。

    仅 record_only=False 的持久化导入路径落 scrape 审计。
    """
    return scrape_dealers_enqueue_impl(
        limit,
        record_only,
        source,
        str_or_none=_str_or_none,
        clamp_limit=_clamp_limit,
        fetch_candidates=_fetch_candidates,
        geocode=_geocode,
        reviewed_persistence_contract=reviewed_persistence_contract,
        reviewed_persistence_status=reviewed_persistence_status,
        upsert_dealer=upsert_dealer,
        sleep=time.sleep,
        sleep_between=_SLEEP_BETWEEN,
        record_scrape_audit=_record_scrape_audit,
        logger=logger,
    )
