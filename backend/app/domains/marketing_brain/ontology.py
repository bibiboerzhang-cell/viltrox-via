"""Marketing Brain (3) -- unified marketing ontology schema.

Pure type / schema module. ZERO runtime side effects, ZERO DB access, ZERO
imports of DB / network. It only defines dataclasses describing the canonical
marketing entities and small pure helpers to map real DB rows onto them.

The canonical entities are:

    Brand      -- the company/brand owning a product line (Viltrox).
    Product    -- a sellable SKU / product launch brief.
    Audience   -- an audience segment estimate.
    Creator    -- a KOL / content creator (from the self-owned KOL pool).
    Content    -- a published post / media asset.
    Campaign   -- an industry project / event grouping work + budget.
    Result     -- a measured outcome (views/likes/leads/roi ...).
    Evidence   -- a captured proof artifact (message/shipment/asset/...).

Field names are aligned to REAL columns of existing tables (grep-verified):

    Creator   <- vkpi_kol_pool                (migration 039)
    Product   <- vkpi_product_launches        (migration 037)
                 + vkpi_product_cost_catalog  (migration 025)
    Campaign  <- vkpi_industry_projects       (migration 041)
                 + vkpi_events                 (migration 122)
    Content   <- vkpi_content_posts           (migration 035)
    Result    <- vkpi_events (roi/leads/videos) + content post metrics
    Evidence  <- vkpi_messages / vkpi_shipments / vkpi_content_assets (035)

Repository traps respected here:
  * BOOLEAN columns read back as int 1/0 via the compat layer -> use _as_bool.
  * *_json columns are TEXT holding JSON (TEXT default '[]' / '{}') on some
    tables and native JSONB on others -> _as_json tolerates both str and obj.
  * RED LINE: this module NEVER writes viltrox_fit_score; it only carries the
    value read from a row as a plain readonly attribute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Mapping, Optional

__all__ = [
    "Brand",
    "Product",
    "Audience",
    "Creator",
    "Content",
    "Campaign",
    "Result",
    "Evidence",
]


# --------------------------------------------------------------------------- #
# Coercion helpers (pure, defensive, no side effects)
# --------------------------------------------------------------------------- #
def _row_to_mapping(row: Any) -> Dict[str, Any]:
    """Normalize a DB row (dict-like / sqlite Row / Mapping) into a plain dict.

    Tolerates None -> {} so from_row never raises on an empty fetch.
    """
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return dict(row)
    # sqlite3.Row and many compat rows support dict(row) but are not Mapping.
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _get(m: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """First present key wins; tolerant of schema aliases across tables."""
    for k in keys:
        if k in m and m[k] is not None:
            return m[k]
    return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    """BOOLEAN compat trap: reads back as int 1/0, or 't'/'f', or real bool."""
    if value is True or value is False:
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "t", "true", "yes", "y")
    return bool(value)


def _as_json(value: Any, default: Any) -> Any:
    """Parse *_json columns: TEXT-holding-JSON or native JSONB (already obj)."""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return default


def _as_json_list(value: Any) -> List[Any]:
    parsed = _as_json(value, [])
    return parsed if isinstance(parsed, list) else []


def _as_json_obj(value: Any) -> Dict[str, Any]:
    parsed = _as_json(value, {})
    return parsed if isinstance(parsed, dict) else {}


# --------------------------------------------------------------------------- #
# Ontology entities
# --------------------------------------------------------------------------- #
@dataclass
class Brand:
    """The brand / company that owns product lines. No dedicated table; this is
    a derived ontology node keyed by brand name (e.g. observed via
    vkpi_kol_pool_brand_links.brand)."""

    name: str = ""
    aliases: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> "Brand":
        m = _row_to_mapping(row)
        return cls(
            name=_as_str(_get(m, "brand", "name")),
            aliases=_as_json_list(_get(m, "aliases_json", "aliases")),
            metadata=_as_json_obj(_get(m, "metadata_json", "metadata")),
        )


@dataclass
class Product:
    """A sellable product / launch brief.

    Aligned to vkpi_product_launches (037) with cost fields from
    vkpi_product_cost_catalog (025)."""

    id: Optional[int] = None
    launch_uid: str = ""
    name: str = ""
    product_sku: str = ""
    product_name: str = ""
    category: str = ""
    target_market: str = ""
    target_platforms: List[Any] = field(default_factory=list)
    target_audience: Dict[str, Any] = field(default_factory=dict)
    competitor_products: List[Any] = field(default_factory=list)
    goals: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    status: str = "draft"
    # cost catalog (cents, currency) -- optional join
    unit_cost_cents: Optional[int] = None
    currency: str = "USD"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> "Product":
        m = _row_to_mapping(row)
        return cls(
            id=_as_int(_get(m, "id")),
            launch_uid=_as_str(_get(m, "launch_uid")),
            name=_as_str(_get(m, "name")),
            product_sku=_as_str(_get(m, "product_sku")),
            product_name=_as_str(_get(m, "product_name")),
            category=_as_str(_get(m, "category")),
            target_market=_as_str(_get(m, "target_market")),
            target_platforms=_as_json_list(_get(m, "target_platforms_json")),
            target_audience=_as_json_obj(_get(m, "target_audience_json")),
            competitor_products=_as_json_list(_get(m, "competitor_products_json")),
            goals=_as_json_obj(_get(m, "goals_json")),
            constraints=_as_json_obj(_get(m, "constraints_json")),
            status=_as_str(_get(m, "status"), "draft"),
            unit_cost_cents=_as_int(_get(m, "unit_cost_cents")),
            currency=_as_str(_get(m, "currency"), "USD"),
            metadata=_as_json_obj(_get(m, "metadata_json")),
        )


@dataclass
class Audience:
    """An audience segment estimate. Derived from the audience_estimated_json
    blob on vkpi_kol_pool / target_audience_json on launches."""

    label: str = ""
    geo: List[Any] = field(default_factory=list)
    age_bands: List[Any] = field(default_factory=list)
    interests: List[Any] = field(default_factory=list)
    estimate: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> "Audience":
        m = _row_to_mapping(row)
        est = _as_json_obj(_get(m, "audience_estimated_json", "target_audience_json"))
        return cls(
            label=_as_str(_get(m, "label", default=est.get("label", ""))),
            geo=_as_json_list(_get(m, "geo_json")) or list(est.get("geo", []) or []),
            age_bands=_as_json_list(_get(m, "age_bands_json"))
            or list(est.get("age_bands", []) or []),
            interests=_as_json_list(_get(m, "interests_json"))
            or list(est.get("interests", []) or []),
            estimate=est,
        )


@dataclass
class Creator:
    """A KOL / content creator. Aligned to vkpi_kol_pool (migration 039)."""

    id: Optional[int] = None
    pool_uid: str = ""
    platform: str = ""
    handle: str = ""
    profile_url: str = ""
    display_name: str = ""
    avatar_url: str = ""
    bio: str = ""
    country: str = ""
    language: str = ""
    email: str = ""
    other_contacts: List[Any] = field(default_factory=list)
    followers: Optional[int] = None
    following: Optional[int] = None
    posts_count: Optional[int] = None
    avg_views: Optional[int] = None
    avg_likes: Optional[int] = None
    avg_comments: Optional[int] = None
    avg_shares: Optional[int] = None
    engagement_rate: Optional[float] = None
    primary_topic: str = ""
    secondary_topics: List[Any] = field(default_factory=list)
    content_style: str = ""
    production_quality: str = ""
    audience_estimated: Dict[str, Any] = field(default_factory=dict)
    brand_collaborations: List[Any] = field(default_factory=list)
    # READONLY mirror of fit score -- ontology never computes/writes it.
    viltrox_fit_score: Optional[float] = None
    viltrox_fit_reason: str = ""
    recommended_product_lines: List[Any] = field(default_factory=list)
    sync_status: str = "imported"
    source_type: str = "manual"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> "Creator":
        m = _row_to_mapping(row)
        return cls(
            id=_as_int(_get(m, "id")),
            pool_uid=_as_str(_get(m, "pool_uid")),
            platform=_as_str(_get(m, "platform")),
            handle=_as_str(_get(m, "handle")),
            profile_url=_as_str(_get(m, "profile_url")),
            display_name=_as_str(_get(m, "display_name")),
            avatar_url=_as_str(_get(m, "avatar_url")),
            bio=_as_str(_get(m, "bio")),
            country=_as_str(_get(m, "country")),
            language=_as_str(_get(m, "language")),
            email=_as_str(_get(m, "email")),
            other_contacts=_as_json_list(_get(m, "other_contacts_json")),
            followers=_as_int(_get(m, "followers")),
            following=_as_int(_get(m, "following")),
            posts_count=_as_int(_get(m, "posts_count")),
            avg_views=_as_int(_get(m, "avg_views")),
            avg_likes=_as_int(_get(m, "avg_likes")),
            avg_comments=_as_int(_get(m, "avg_comments")),
            avg_shares=_as_int(_get(m, "avg_shares")),
            engagement_rate=_as_float(_get(m, "engagement_rate")),
            primary_topic=_as_str(_get(m, "primary_topic")),
            secondary_topics=_as_json_list(_get(m, "secondary_topics_json")),
            content_style=_as_str(_get(m, "content_style")),
            production_quality=_as_str(_get(m, "production_quality")),
            audience_estimated=_as_json_obj(_get(m, "audience_estimated_json")),
            brand_collaborations=_as_json_list(_get(m, "brand_collaborations_json")),
            viltrox_fit_score=_as_float(_get(m, "viltrox_fit_score")),
            viltrox_fit_reason=_as_str(_get(m, "viltrox_fit_reason")),
            recommended_product_lines=_as_json_list(
                _get(m, "recommended_product_lines_json")
            ),
            sync_status=_as_str(_get(m, "sync_status"), "imported"),
            source_type=_as_str(_get(m, "source_type"), "manual"),
        )


@dataclass
class Content:
    """A published post / media asset. Aligned to vkpi_content_posts (035)."""

    id: Optional[int] = None
    project_id: Optional[int] = None
    kol_id: Optional[int] = None
    platform: str = ""
    post_url: str = ""
    title: str = ""
    thumbnail_url: str = ""
    content_type: str = ""
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    rights_status: str = "unknown"
    ad_usage_allowed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> "Content":
        m = _row_to_mapping(row)
        return cls(
            id=_as_int(_get(m, "id")),
            project_id=_as_int(_get(m, "project_id")),
            kol_id=_as_int(_get(m, "kol_id")),
            platform=_as_str(_get(m, "platform")),
            post_url=_as_str(_get(m, "post_url")),
            title=_as_str(_get(m, "title")),
            thumbnail_url=_as_str(_get(m, "thumbnail_url")),
            content_type=_as_str(_get(m, "content_type")),
            views=_as_int(_get(m, "views")) or 0,
            likes=_as_int(_get(m, "likes")) or 0,
            comments=_as_int(_get(m, "comments")) or 0,
            shares=_as_int(_get(m, "shares")) or 0,
            rights_status=_as_str(_get(m, "rights_status"), "unknown"),
            ad_usage_allowed=_as_bool(_get(m, "ad_usage_allowed")),
            metadata=_as_json_obj(_get(m, "metadata_json")),
        )


@dataclass
class Campaign:
    """A campaign grouping: industry project (041) or event (122).

    `kind` records which source backed this node so callers can round-trip."""

    id: Optional[Any] = None
    kind: str = "project"  # 'project' | 'event'
    uid: str = ""
    name: str = ""
    description: str = ""
    project_type: str = ""
    status: str = ""
    owner_staff_id: Optional[int] = None
    is_active: bool = True
    # event-flavored fields
    start_date: str = ""
    end_date: str = ""
    budget_total: Optional[int] = None
    team_ids: List[Any] = field(default_factory=list)
    related_project_ids: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_project_row(cls, row: Any) -> "Campaign":
        m = _row_to_mapping(row)
        return cls(
            id=_as_int(_get(m, "id")),
            kind="project",
            uid=_as_str(_get(m, "project_uid")),
            name=_as_str(_get(m, "name")),
            description=_as_str(_get(m, "description")),
            project_type=_as_str(_get(m, "project_type")),
            status=_as_str(_get(m, "status")),
            owner_staff_id=_as_int(_get(m, "owner_staff_id")),
            is_active=_as_bool(_get(m, "is_active", default=True)),
            metadata=_as_json_obj(_get(m, "metadata_json")),
        )

    @classmethod
    def from_event_row(cls, row: Any) -> "Campaign":
        m = _row_to_mapping(row)
        return cls(
            id=_get(m, "id"),  # event id is VARCHAR
            kind="event",
            uid=_as_str(_get(m, "id")),
            name=_as_str(_get(m, "title")),
            description=_as_str(_get(m, "note")),
            project_type=_as_str(_get(m, "type_key")),
            status=_as_str(_get(m, "status")),
            owner_staff_id=_as_int(_get(m, "owner_id")),
            start_date=_as_str(_get(m, "start_date")),
            end_date=_as_str(_get(m, "end_date")),
            budget_total=_as_int(_get(m, "budget_total")),
            team_ids=_as_json_list(_get(m, "team_ids")),
            related_project_ids=_as_json_list(_get(m, "related_project_ids")),
            metadata=_as_json_obj(_get(m, "budget_json")),
        )

    # Default from_row maps the project shape (the canonical campaign table).
    @classmethod
    def from_row(cls, row: Any) -> "Campaign":
        return cls.from_project_row(row)


@dataclass
class Result:
    """A measured outcome for a campaign / event. Aligned to the result columns
    on vkpi_events (roi/leads/videos/health_score) -- generic enough to also
    hold rolled-up content metrics."""

    subject_kind: str = "event"  # 'event' | 'content' | 'campaign'
    subject_id: Optional[Any] = None
    roi: Optional[float] = None
    leads: Optional[int] = None
    videos: Optional[int] = None
    health_score: Optional[int] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    retrospective: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_event_row(cls, row: Any) -> "Result":
        m = _row_to_mapping(row)
        return cls(
            subject_kind="event",
            subject_id=_get(m, "id"),
            roi=_as_float(_get(m, "roi")),
            leads=_as_int(_get(m, "leads")),
            videos=_as_int(_get(m, "videos")),
            health_score=_as_int(_get(m, "health_score")),
            retrospective=_as_str(_get(m, "retrospective")),
        )

    @classmethod
    def from_content_row(cls, row: Any) -> "Result":
        m = _row_to_mapping(row)
        return cls(
            subject_kind="content",
            subject_id=_as_int(_get(m, "id")),
            views=_as_int(_get(m, "views")),
            likes=_as_int(_get(m, "likes")),
            comments=_as_int(_get(m, "comments")),
            shares=_as_int(_get(m, "shares")),
        )

    @classmethod
    def from_row(cls, row: Any) -> "Result":
        return cls.from_event_row(row)


@dataclass
class Evidence:
    """A captured proof artifact. Aligned to vkpi_messages / vkpi_shipments /
    vkpi_content_assets (035) -- evidence_url is the common spine."""

    kind: str = "message"  # 'message' | 'shipment' | 'asset' | 'deliverable'
    id: Optional[int] = None
    project_id: Optional[int] = None
    kol_id: Optional[int] = None
    evidence_url: str = ""
    source: str = ""
    direction: str = ""
    snippet: str = ""
    status: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_message_row(cls, row: Any) -> "Evidence":
        m = _row_to_mapping(row)
        return cls(
            kind="message",
            id=_as_int(_get(m, "id")),
            project_id=_as_int(_get(m, "project_id")),
            kol_id=_as_int(_get(m, "kol_id")),
            evidence_url=_as_str(_get(m, "evidence_url")),
            source=_as_str(_get(m, "source")),
            direction=_as_str(_get(m, "direction")),
            snippet=_as_str(_get(m, "snippet") or _get(m, "body")),
            metadata=_as_json_obj(_get(m, "metadata_json")),
        )

    @classmethod
    def from_shipment_row(cls, row: Any) -> "Evidence":
        m = _row_to_mapping(row)
        return cls(
            kind="shipment",
            id=_as_int(_get(m, "id")),
            project_id=_as_int(_get(m, "project_id")),
            evidence_url=_as_str(_get(m, "evidence_url")),
            status=_as_str(_get(m, "status")),
            metadata=_as_json_obj(_get(m, "metadata_json")),
        )

    @classmethod
    def from_row(cls, row: Any) -> "Evidence":
        return cls.from_message_row(row)
