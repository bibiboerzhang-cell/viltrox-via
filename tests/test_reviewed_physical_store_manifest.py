from __future__ import annotations

from app.domains.commerce import reviewed_physical_store_manifest as reviewed
from app.domains.commerce.dealer_directory_view import build_dealer_pins, project_dealer


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, manifest):
        self.rows = {
            (row["name"].casefold(), row["address"].casefold()): {
                "id": index,
                "name": row["name"],
                "address": row["address"],
                "city": row["city"],
                "state": row["state"],
                "postal_code": row["postal_code"],
                "phone": row["phone"],
                "website_url": row["website_url"],
                "location_source_url": row["location_source_url"],
                "brand_listing_url": row["brand_listing_url"],
                "lat": None,
                "lng": None,
                "publication_status": "draft",
                "location_verification_contract_version": 0,
                "canonical_location_status": "pending",
                "physical_store_status": "pending",
                "google_place_verification_status": "pending",
            }
            for index, row in enumerate(manifest["stores"], start=1)
        }
        self.mutations = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if "information_schema.columns" in normalized:
            return _Result([{"column_name": name} for name in reviewed.REQUIRED_COLUMNS])
        if normalized.startswith("SELECT id,name,address"):
            key = (str(params[0]).casefold(), str(params[1]).casefold())
            row = self.rows.get(key)
            return _Result([row] if row else [])
        if normalized.startswith("UPDATE vkpi_dealers") or normalized.startswith(
            "INSERT INTO vkpi_dealer_brand_relationships"
        ):
            self.mutations.append((normalized, tuple(params)))
            return _Result()
        raise AssertionError(normalized)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _runtime(monkeypatch):
    monkeypatch.setattr(reviewed, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(reviewed, "table_exists", lambda _name: True)


def test_bounded_manifest_is_exact_and_google_pending():
    manifest = reviewed.load_manifest()

    assert len(manifest["stores"]) == 5
    assert {row["coordinates"]["provider"] for row in manifest["stores"]} == {
        "us_census_geocoder"
    }
    assert manifest["google_places_status"] == "pending"
    assert all("google_place_id" not in row for row in manifest["stores"])
    assert all(row["phone"] and row["website_url"].startswith("https://") for row in manifest["stores"])
    assert all(
        brand["authorization_status"] == "unverified"
        for row in manifest["stores"]
        for brand in row["brand_relationships"]
    )
    assert set(manifest["excluded_brand_directories"]) == {"canon", "sony"}


def test_default_application_is_zero_write_dry_run(monkeypatch):
    _runtime(monkeypatch)
    manifest = reviewed.load_manifest()
    conn = _Conn(manifest)

    result = reviewed.apply_manifest(
        manifest,
        actor_id=7,
        connection=conn,
    )

    assert result["mode"] == "dry_run"
    assert result["store_count"] == 5
    assert result["database_writes"] == 0
    assert result["google_places_status"] == "pending"
    assert conn.mutations == []
    assert conn.commits == 0


def test_explicit_publish_updates_five_rows_and_never_writes_google_evidence(monkeypatch):
    _runtime(monkeypatch)
    manifest = reviewed.load_manifest()
    conn = _Conn(manifest)

    result = reviewed.apply_manifest(
        manifest,
        actor_id=7,
        publish=True,
        connection=conn,
    )

    updates = [sql for sql, _ in conn.mutations if sql.startswith("UPDATE vkpi_dealers")]
    assert result["mode"] == "published"
    assert result["published_count"] == 5
    assert len(updates) == 5
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert all("google_place_verification_status='pending'" in sql for sql in updates)
    assert all("google_place_id=NULL" in sql for sql in updates)
    assert all("google_place_evidence_json='{}'::jsonb" in sql for sql in updates)
    assert all("phone=?" in sql and "website_url=?" in sql for sql in updates)


def test_projection_exposes_census_provenance_and_pending_google_without_blocking_map():
    row = {
        "id": 1,
        "name": "B&H Photo Video · NYC SuperStore",
        "address": "420 9th Ave",
        "city": "New York",
        "state": "NY",
        "lat": 40.753308871719,
        "lng": -73.996272449882,
        "source_status": "public_listing_verified",
        "source_id": "bhphoto_store_page",
        "stable_org_key": "dealer_org_12345678",
        "stable_location_key": "dealer_loc_12345678",
        "reviewer_id": "staff_7",
        "reviewed_at": "2026-07-16T16:00:00Z",
        "review_contract_version": 1,
        "source_checked_at": "2026-07-16T16:00:00Z",
        "location_source_url": "https://www.bhphotovideo.com/find/HelpCenter/StoreInfo.jsp",
        "brand_listing_url": "https://www.bhphotovideo.com/c/browse/viltrox/ci/58790",
        "evidence_json": {
            "claim_status": "descriptive_only",
            "source": {
                "source_id": "bhphoto_store_page",
                "source_url": "https://www.bhphotovideo.com/find/HelpCenter/StoreInfo.jsp",
                "reviewer_id": "staff_7",
                "value_status": "observed",
            },
            "product": {
                "source_url": "https://www.bhphotovideo.com/c/browse/viltrox/ci/58790",
                "value_status": "observed",
            },
            "coordinate": {
                "provider": "us_census_geocoder",
                "match_level": "exact_address",
                "value_status": "observed",
                "google_derived": False,
            },
        },
        "publication_status": "published",
        "published_at": "2026-07-16T16:00:00Z",
        "location_verification_contract_version": 1,
        "canonical_location_status": "official_site_verified",
        "canonical_location_checked_at": "2026-07-16T16:00:00Z",
        "physical_store_status": "verified_physical_store",
        "physical_store_checked_at": "2026-07-16T16:00:00Z",
        "physical_store_verification_note": "Official page reviewed.",
        "google_place_verification_status": "pending",
        "google_place_id": None,
        "google_maps_url": None,
        "google_place_checked_at": None,
    }

    projected = project_dealer(row)
    verification = projected["location_verification"]

    assert verification["coordinate"]["provider"] == "us_census_geocoder"
    assert verification["coordinate"]["provenance_valid"] is True
    assert verification["google_place_cross_check"]["status"] == "pending"
    assert verification["google_place_cross_check"]["canonical_source"] is False
    assert verification["map_eligible"] is True
    assert len(build_dealer_pins([projected])) == 1
