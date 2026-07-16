from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_dealers
from app.domains.commerce import dealer_directory_view, dealer_scrape


AS_OF = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _legacy_row(index: int, *, reviewed: bool = True) -> dict:
    return {
        "id": index,
        "name": f"Legacy Camera {index}",
        "address": f"{index} Main St",
        "city": "New York",
        "state": "NY",
        "country": "US",
        "lat": 40.7,
        "lng": -74.0,
        "source_status": "public_listing_verified" if reviewed else "unverified",
        "authorization_status": "needs_viltrox_confirmation",
        "source_checked_at": "2026-07-14T12:00:00Z",
        "brand_listing_url": "https://dealer.example/viltrox",
        "location_source_url": "https://dealer.example/store",
    }


def test_five_legacy_rows_keep_three_existing_geocoded_pins_without_truth_upgrade():
    source_rows = []
    for index in range(1, 6):
        row = _legacy_row(index)
        row.update(
            {
                "review_contract_version": 0,
                "reviewer_id": "",
                "publication_status": "published" if index <= 3 else "draft",
                "published_at": "2026-07-14T12:00:00Z" if index <= 3 else None,
                "published_by": (
                    "system:migration260_legacy_visibility" if index <= 3 else ""
                ),
            }
        )
        if index > 3:
            row["lat"] = None
            row["lng"] = None
        source_rows.append(row)
    rows = [
        dealer_directory_view.project_dealer(row, as_of=AS_OF)
        for row in source_rows
    ]

    assert len(rows) == 5
    assert sum(row["publication_status"] == "published" for row in rows) == 3
    assert sum(row["publication_status"] == "draft" for row in rows) == 2
    assert all(row["brand_codes"] == [] for row in rows)
    assert all(row["viltrox_deployment"]["status"] == "not_deployed" for row in rows)
    assert all(row["activity"]["status"] == "unknown" for row in rows)
    assert all(row["review_contract"]["status"] == "legacy_unverified" for row in rows)
    assert all(row["truth_status"]["candidate"] is True for row in rows)
    assert all(row["truth_status"]["viltrox_authorization"] == "pending" for row in rows)


def test_projection_exposes_multi_brand_rollout_and_activity_without_truth_upgrade():
    row = _legacy_row(1, reviewed=False)
    row.update(
        {
            "publication_status": "published",
            "published_at": "2026-07-15T14:00:00Z",
            "viltrox_deployment_status": "deployed",
            "viltrox_deployed_at": "2026-07-15T14:01:00Z",
            "viltrox_deployment_note": "display installed",
            "activity_status": "active",
            "activity_page_url": "https://dealer.example/events",
            "activity_checked_at": "2026-07-15T14:02:00Z",
            "next_activity_at": "2026-07-20T18:00:00Z",
            "website_url": "https://dealer.example/about",
            "social_links_json": [
                {"platform": "instagram", "url": "https://instagram.com/example"},
                {"platform": "youtube", "url": "https://youtube.com/@example"},
                {"platform": "unsafe", "url": "javascript:alert(1)"},
                {"platform": "data", "url": "data:text/html,unsafe"},
            ],
            "brand_relationships": [
                {
                    "brand_key": "sony",
                    "relationship_status": "official_directory_listed",
                    "authorization_status": "confirmed_by_brand",
                    "evidence_url": "https://electronics.sony.com/retailers",
                    "source_checked_at": "2026-07-15T13:00:00Z",
                },
                {
                    "brand_key": "viltrox",
                    "relationship_status": "retailer_observed",
                    "authorization_status": "unverified",
                    "evidence_url": "https://dealer.example/viltrox",
                    "source_checked_at": "2026-07-15T13:10:00Z",
                },
            ],
        }
    )

    projected = dealer_directory_view.project_dealer(row, as_of=AS_OF)

    assert projected["brand_codes"] == ["sony", "viltrox"]
    assert projected["publication_status"] == "published"
    assert projected["viltrox_deployment"]["status"] == "deployed"
    assert projected["viltrox_deployment"]["proves_authorization"] is False
    assert projected["viltrox_deployment"]["proves_inventory"] is False
    assert projected["activity"]["next_event_at"] == "2026-07-20T18:00:00Z"
    assert projected["website_url"] == "https://dealer.example/about"
    assert [link["platform"] for link in projected["social_links"]] == [
        "instagram",
        "youtube",
    ]
    assert projected["truth_status"]["candidate"] is True
    assert projected["truth_status"]["viltrox_authorization"] == "pending"
    assert projected["truth_status"]["current_inventory"] == "unknown"


def test_brand_contract_allows_custom_vendor_and_rejects_manual_confirmation():
    custom = dealer_scrape._normalize_brand_relationships(
        ["blackmagic_design", "canon"], actor_id=9
    )
    assert [row["brand_key"] for row in custom] == ["blackmagic_design", "canon"]
    assert all(row["authorization_status"] == "unverified" for row in custom)

    for brand_key, evidence_url in (
        ("nikon", "https://www.nikonusa.com/where-to-buy/example"),
        ("sony", "https://electronics.sony.com/retailers"),
        ("canon", "https://dealer.example/canon"),
    ):
        with pytest.raises(ValueError, match="authorization_status must be one of"):
            dealer_scrape._normalize_brand_relationships(
                [
                    {
                        "brand_key": brand_key,
                        "relationship_status": "official_directory_listed",
                        "authorization_status": "confirmed_by_brand",
                        "evidence_url": evidence_url,
                        "source_checked_at": "2026-07-15T12:00:00Z",
                    }
                ],
                actor_id=9,
            )

    with pytest.raises(ValueError, match="authorization_status must be one of"):
        dealer_scrape._normalize_brand_relationships(
            [
                {
                    "brand_key": "sony",
                    "relationship_status": "declared",
                    "authorization_status": "confirmed_by_brand",
                }
            ],
            actor_id=9,
        )


def test_social_links_are_bounded_normalized_and_require_absolute_urls():
    links = dealer_scrape._normalize_social_links(
        [
            {"platform": "Instagram", "url": "https://instagram.com/dealer"},
            {"platform": "YouTube", "url": "https://youtube.com/@dealer"},
            {"platform": "X", "url": "https://x.com/dealer"},
        ]
    )
    assert links == [
        {"platform": "instagram", "url": "https://instagram.com/dealer"},
        {"platform": "x", "url": "https://x.com/dealer"},
        {"platform": "youtube", "url": "https://youtube.com/@dealer"},
    ]
    with pytest.raises(ValueError, match="absolute http"):
        dealer_scrape._normalize_social_links(
            [{"platform": "instagram", "url": "javascript:alert(1)"}]
        )
    with pytest.raises(ValueError, match="at most 12"):
        dealer_scrape._normalize_social_links(
            [
                {"platform": f"p{index}", "url": f"https://example.com/{index}"}
                for index in range(13)
            ]
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-125,24,-66,50", (-125.0, 24.0, -66.0, 50.0)),
        (None, None),
    ],
)
def test_bbox_parser(raw, expected):
    assert dealer_scrape.parse_bbox(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["-125,24,-66", "west,24,-66,50", "20,24,-66,50", "-125,55,-66,50"],
)
def test_bbox_parser_rejects_invalid_or_wrapped_boxes(raw):
    with pytest.raises(ValueError):
        dealer_scrape.parse_bbox(raw)


def test_filter_sql_uses_brand_publication_and_bbox_without_join_duplication():
    sql, params, normalized = dealer_scrape._dealer_filter_sql(
        state=None,
        city=None,
        channel="all",
        evidence_status="all",
        product_evidence="all",
        authorization="all",
        brand="Canon",
        published_only=True,
        bbox=(-125.0, 24.0, -66.0, 50.0),
        map_management_enforced=True,
    )

    assert "EXISTS (SELECT 1 FROM vkpi_dealer_brand_relationships" in sql
    assert "publication_status = 'published'" in sql
    assert "lat BETWEEN ? AND ?" in sql
    assert "lng BETWEEN ? AND ?" in sql
    assert params == ["canon", 24.0, 50.0, -125.0, -66.0]
    assert normalized["brand"] == "canon"


def test_coverage_separates_actual_published_pins_from_evidence_locations(monkeypatch):
    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class CoverageConn:
        def execute(self, sql, _params=()):
            normalized = " ".join(str(sql).split())
            if "FROM vkpi_dealers" in normalized:
                return Rows(
                    [
                        {
                            "id": 1,
                            "state": "CA",
                            "country": "US",
                            "lat": 34.1,
                            "lng": -118.2,
                            "brand_listing_url": None,
                            "source_status": "unverified",
                            "authorization_status": "needs_viltrox_confirmation",
                            "source_checked_at": None,
                            "phone": None,
                            "contact_email": None,
                            "store_hours": None,
                            "public_services": None,
                            "publication_status": "published",
                        },
                        {
                            "id": 2,
                            "state": "NY",
                            "country": "US",
                            "lat": 40.7,
                            "lng": -74.0,
                            "brand_listing_url": "https://dealer.example/viltrox",
                            "source_status": "public_listing_verified",
                            "authorization_status": "needs_viltrox_confirmation",
                            "source_checked_at": "2026-07-14T12:00:00Z",
                            "phone": None,
                            "contact_email": None,
                            "store_hours": None,
                            "public_services": None,
                            "publication_status": "draft",
                        },
                    ]
                )
            raise AssertionError(normalized)

    monkeypatch.setattr(
        dealer_scrape,
        "table_exists",
        lambda name: name == "vkpi_dealers",
    )
    monkeypatch.setattr(
        dealer_scrape,
        "_dealer_table_columns",
        lambda: set(dealer_directory_view.MANAGED_DEALER_DURABLE_FIELDS),
    )
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: CoverageConn())

    result = dealer_scrape.dealer_coverage_summary(
        organization_id=1,
        as_of=AS_OF,
    )

    assert result["published_map_pins"] == 1
    assert result["located"] == 1
    assert result["evidence_qualified_locations"] == 1
    matrix = result["us_jurisdiction_matrix"]
    assert matrix["published_map_pin_counts_by_state_dc"] == {"CA": 1}
    assert matrix["map_eligible_counts_by_state_dc"] == {"CA": 1}
    assert matrix["evidence_qualified_counts_by_state_dc"] == {"NY": 1}
    assert matrix["located_counts_by_state_dc"] == {"NY": 1}
    assert result["claim_boundaries"]["map_publication_proves_authorization"] is False


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _ManagementConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple | list]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        frozen = tuple(params)
        self.calls.append((normalized, frozen))
        if normalized.startswith("SELECT id, publication_status"):
            return _Result({"id": 7, "publication_status": "draft"})
        if normalized.startswith("SELECT id, name, address"):
            return _Result(
                {
                    "id": 7,
                    "name": "Manual Camera",
                    "address": "7 Main St",
                    "city": "Boston",
                    "state": "MA",
                    "lat": 42.36,
                    "lng": -71.06,
                }
            )
        return _Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_update_replaces_brands_and_keeps_viltrox_deployment_operational(monkeypatch):
    conn = _ManagementConn()
    monkeypatch.setattr(dealer_scrape, "_require_map_management", lambda: None)
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)
    monkeypatch.setattr(
        dealer_scrape,
        "get_dealer",
        lambda dealer_id: {"id": dealer_id, "publication_status": "draft"},
    )

    result = dealer_scrape.update_dealer(
        7,
        {
            "brands": ["canon", "sony", "custom_optics"],
            "viltrox_deployment": {"status": "deployed", "note": "demo wall"},
            "activity": {
                "status": "active",
                "page_url": "https://dealer.example/events",
                "checked_at": "2026-07-15T12:00:00Z",
                "next_event_at": "2026-07-20T18:00:00Z",
                "note": "workshop",
            },
            "website_url": "https://dealer.example",
            "social_links": [
                {"platform": "instagram", "url": "https://instagram.com/dealer"}
            ],
        },
        actor_id=5,
    )

    assert result["id"] == 7
    assert conn.commits == 1
    sql = "\n".join(call[0] for call in conn.calls)
    assert "viltrox_deployment_status = ?" in sql
    assert "viltrox_deployed_by = ?" in sql
    assert "activity_page_url = ?" in sql
    assert "website_url = ?" in sql
    assert "social_links_json = ?::jsonb" in sql
    assert "DELETE FROM vkpi_dealer_brand_relationships" in sql
    assert sql.count("INSERT INTO vkpi_dealer_brand_relationships") == 3
    assert any("staff_5" in params for _, params in conn.calls)


def test_publish_requires_complete_coordinates_and_never_updates_evidence(monkeypatch):
    conn = _ManagementConn()
    monkeypatch.setattr(dealer_scrape, "_require_map_management", lambda: None)
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)
    monkeypatch.setattr(
        dealer_scrape,
        "get_dealer",
        lambda dealer_id: {
            "id": dealer_id,
            "publication_status": "published",
            "source_status": "unverified",
            "authorization_status": "needs_viltrox_confirmation",
        },
    )

    result = dealer_scrape.set_dealer_publication(7, published=True, actor_id=5)

    assert result["source_status"] == "unverified"
    update_sql = next(sql for sql, _ in conn.calls if sql.startswith("UPDATE vkpi_dealers"))
    assert "publication_status = 'published'" in update_sql
    assert "source_status" not in update_sql
    assert "authorization_status" not in update_sql


def test_publish_rejects_incomplete_map_location(monkeypatch):
    class IncompleteConn(_ManagementConn):
        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).split())
            if normalized.startswith("SELECT id, name, address"):
                self.calls.append((normalized, tuple(params)))
                return _Result(
                    {
                        "id": 7,
                        "name": "Incomplete Camera",
                        "address": "7 Main St",
                        "city": "Boston",
                        "state": "MA",
                        "lat": None,
                        "lng": None,
                    }
                )
            return super().execute(sql, params)

    conn = IncompleteConn()
    monkeypatch.setattr(dealer_scrape, "_require_map_management", lambda: None)
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="lat, lng"):
        dealer_scrape.set_dealer_publication(7, published=True, actor_id=5)
    assert not any(sql.startswith("UPDATE vkpi_dealers") for sql, _ in conn.calls)


def test_unpublish_removes_pin_without_changing_evidence(monkeypatch):
    conn = _ManagementConn()
    monkeypatch.setattr(dealer_scrape, "_require_map_management", lambda: None)
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)
    monkeypatch.setattr(
        dealer_scrape,
        "get_dealer",
        lambda dealer_id: {
            "id": dealer_id,
            "publication_status": "draft",
            "source_status": "public_listing_verified",
            "authorization_status": "needs_viltrox_confirmation",
        },
    )

    result = dealer_scrape.set_dealer_publication(7, published=False, actor_id=5)

    assert result["publication_status"] == "draft"
    update_sql = next(sql for sql, _ in conn.calls if sql.startswith("UPDATE vkpi_dealers"))
    assert "publication_status = 'draft'" in update_sql
    assert "source_status" not in update_sql
    assert "authorization_status" not in update_sql


def test_patch_rejects_unsafe_urls_and_naive_activity_timestamps(monkeypatch):
    conn = _ManagementConn()
    monkeypatch.setattr(dealer_scrape, "_require_map_management", lambda: None)
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="absolute http"):
        dealer_scrape.update_dealer(
            7,
            {"website_url": "javascript:alert(1)"},
            actor_id=5,
        )
    with pytest.raises(ValueError, match="include a timezone"):
        dealer_scrape.update_dealer(
            7,
            {
                "activity": {
                    "status": "active",
                    "page_url": "https://dealer.example/events",
                    "checked_at": "2026-07-15T12:00:00",
                }
            },
            actor_id=5,
        )
    assert not any(sql.startswith("UPDATE vkpi_dealers") for sql, _ in conn.calls)


def test_router_forwards_new_location_filters_and_rejects_bad_bbox(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "list_dealer_pins",
        lambda **kwargs: captured.update(kwargs) or [],
    )

    response = vkpi_dealers.dealer_locations_route(
        state="ca",
        city=None,
        channel="all",
        evidence_status="all",
        product_evidence="all",
        authorization="all",
        brand="Sony",
        published_only=True,
        bbox="-125,24,-66,50",
        staff={"organization_id": 1},
    )

    assert captured["brand"] == "Sony"
    assert captured["published_only"] is True
    assert captured["bbox"] == (-125.0, 24.0, -66.0, 50.0)
    assert response["brand"] == "sony"

    with pytest.raises(HTTPException) as error:
        vkpi_dealers.dealer_locations_route(
            state=None,
            city=None,
            channel="all",
            evidence_status="all",
            product_evidence="all",
            authorization="all",
            brand=None,
            published_only=True,
            bbox="invalid",
            staff={"organization_id": 1},
        )
    assert error.value.status_code == 400


def test_manual_create_always_returns_full_dealer_shape(monkeypatch):
    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "upsert_dealer",
        lambda _payload: {"ok": True, "id": 41, "source_status": "unverified"},
    )
    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "get_dealer",
        lambda dealer_id: {
            "id": dealer_id,
            "name": "Manual Camera",
            "brand_codes": [],
            "publication_status": "draft",
            "viltrox_deployment": {"status": "not_deployed"},
            "activity": {"status": "unknown"},
        },
    )

    result = vkpi_dealers.create_dealer_route(
        {
            "name": "Manual Camera",
            "address": "41 Main St",
            "city": "Boston",
            "state": "MA",
            "lat": 42.36,
            "lng": -71.06,
        },
        staff={"id": 5, "role": "manager"},
    )

    assert result["id"] == 41
    assert result["publication_status"] == "draft"
    assert result["viltrox_deployment"]["status"] == "not_deployed"


def test_create_only_conflict_returns_409_and_never_runs_update(monkeypatch):
    class ConflictConn:
        def execute(self, sql, _params=()):
            normalized = " ".join(str(sql).split())
            if normalized.startswith("SELECT id FROM vkpi_dealers"):
                return _Result({"id": 77})
            raise AssertionError("conflict path must not execute dealer upsert")

    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: ConflictConn())

    with pytest.raises(HTTPException) as error:
        vkpi_dealers.create_dealer_route(
            {
                "name": "Existing Camera",
                "address": "77 Main St",
                "city": "Boston",
                "state": "MA",
            },
            staff={"id": 5, "role": "manager"},
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "dealer_already_exists"
    assert error.value.detail["dealer_id"] == 77


def test_manual_create_forwards_website_and_social_links_to_managed_edit(monkeypatch):
    captured = {}
    def create(payload, changes, *, actor_id):
        captured.update(
            {"payload": payload, "changes": changes, "actor_id": actor_id}
        )
        return {
            "id": 42,
            "website_url": changes["website_url"],
            "social_links": changes["social_links"],
            "publication_status": "draft",
        }

    monkeypatch.setattr(vkpi_dealers.dealer_scrape, "create_managed_dealer", create)
    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "validate_new_dealer_management_fields",
        lambda _changes, *, actor_id: captured.update({"validated_by": actor_id}),
    )

    result = vkpi_dealers.create_dealer_route(
        {
            "name": "Social Camera",
            "address": "42 Main St",
            "city": "Boston",
            "state": "MA",
            "lat": 42.36,
            "lng": -71.06,
            "website_url": "https://dealer.example",
            "social_links": [
                {"platform": "instagram", "url": "https://instagram.com/dealer"}
            ],
        },
        staff={"id": 5, "role": "manager"},
    )

    assert result["id"] == 42
    assert captured["actor_id"] == 5
    assert captured["validated_by"] == 5
    assert captured["payload"]["_create_only"] is True
    assert captured["changes"]["website_url"] == "https://dealer.example"
    assert captured["changes"]["social_links"][0]["platform"] == "instagram"


def test_managed_create_rolls_back_base_row_when_managed_update_fails(monkeypatch):
    class TransactionConn:
        committed = 0
        rolled_back = 0

        def commit(self):
            self.committed += 1

        def rollback(self):
            self.rolled_back += 1

    conn = TransactionConn()
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)
    monkeypatch.setattr(
        dealer_scrape,
        "validate_new_dealer_management_fields",
        lambda _changes, *, actor_id: None,
    )
    monkeypatch.setattr(
        dealer_scrape,
        "upsert_dealer",
        lambda _payload, **_kwargs: {"id": 43},
    )

    def fail_update(*_args, **_kwargs):
        raise RuntimeError("managed update failed")

    monkeypatch.setattr(dealer_scrape, "update_dealer", fail_update)

    with pytest.raises(RuntimeError, match="managed update failed"):
        dealer_scrape.create_managed_dealer(
            {"name": "Atomic Camera", "address": "43 Main St"},
            {"publication_status": "draft"},
            actor_id=5,
        )

    assert conn.committed == 0
    assert conn.rolled_back == 1
