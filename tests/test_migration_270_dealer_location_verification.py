from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/270_vkpi_dealer_location_verification.sql"
DOWN = ROOT / "migrations/270_vkpi_dealer_location_verification_down.sql"


def _sql(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_migration_270_separates_store_google_and_product_truth():
    sql = _sql(UP)

    assert "add column if not exists canonical_location_status" in sql
    assert "add column if not exists physical_store_status" in sql
    assert "add column if not exists google_place_verification_status" in sql
    assert "google_place_verification_status text not null default 'pending'" in sql
    assert "product/brand evidence remains in brand_listing_url" in sql
    assert "google never becomes the canonical address source" in sql
    assert "nikonusa\\.com|nikon\\.com|canon\\.com|sony\\.com|godox\\.com" in sql
    assert "nullif(trim(location_source_url), '') is not null" in sql
    assert "nullif(trim(google_place_id), '') is not null" in sql
    assert "insert into vkpi_dealers" not in sql
    assert "google_place_id =" not in sql


def test_migration_270_fails_closed_for_legacy_map_rows():
    sql = _sql(UP)

    assert "update vkpi_dealers set publication_status = 'draft'" in sql
    assert "location_verification_contract_version = 1" in sql
    assert "canonical_location_status = 'official_site_verified'" in sql
    assert "physical_store_status = 'verified_physical_store'" in sql
    assert "nullif(trim(physical_store_verification_note), '') is not null" in sql
    assert "published_by ~ '^staff_" in sql
    assert "system:migration260_legacy_visibility" not in sql.split(
        "add constraint chk_vkpi_dealer_published_receipt", 1
    )[1]


def test_migration_270_down_is_scoped_and_never_republishes_rows():
    down = _sql(DOWN)

    assert "drop column if exists canonical_location_status" in down
    assert "drop column if exists physical_store_status" in down
    assert "drop column if exists google_place_id" in down
    assert "where version_key = '270_vkpi_dealer_location_verification.sql'" in down
    assert "update vkpi_dealers set publication_status = 'published'" not in down
    assert "drop table if exists vkpi_dealers" not in down


@pytest.mark.pg
def test_migration_270_up_and_down_on_real_postgres(pg_dsn: str):
    import uuid

    import psycopg
    from psycopg import sql

    schema = f"vkpi_dealer_location_{uuid.uuid4().hex}"
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
            )
            conn.execute(
                """
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations VALUES ('270_vkpi_dealer_location_verification.sql');
                CREATE TABLE vkpi_dealers (
                  id BIGSERIAL PRIMARY KEY,
                  name TEXT NOT NULL,
                  address TEXT NOT NULL,
                  city TEXT,
                  state TEXT,
                  lat DOUBLE PRECISION,
                  lng DOUBLE PRECISION,
                  location_source_url TEXT,
                  brand_listing_url TEXT,
                  publication_status TEXT NOT NULL DEFAULT 'draft',
                  published_at TIMESTAMPTZ,
                  published_by TEXT NOT NULL DEFAULT '',
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            conn.execute(up)
            fields = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name='vkpi_dealers'"
                ).fetchall()
            }
            assert {
                "canonical_location_status",
                "physical_store_status",
                "google_place_verification_status",
            } <= fields
            conn.execute(
                """
                INSERT INTO vkpi_dealers (
                  name,address,city,state,lat,lng,location_source_url,
                  location_verification_contract_version,
                  canonical_location_status,canonical_location_checked_at,
                  canonical_location_checked_by,physical_store_status,
                  physical_store_checked_at,physical_store_checked_by,
                  physical_store_verification_note,
                  publication_status,published_at,published_by
                ) VALUES (
                  'Verified Store','7 Main St','Boston','MA',42.36,-71.06,
                  'https://dealer.example/stores/boston',1,
                  'official_site_verified',NOW(),'staff_5',
                  'verified_physical_store',NOW(),'staff_5',
                  'Exact address reviewed on store-owned website',
                  'published',NOW(),'staff_5'
                )
                """
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    """
                    INSERT INTO vkpi_dealers (
                      name,address,city,state,lat,lng,publication_status,
                      published_at,published_by
                    ) VALUES (
                      'Directory Name','1 Unknown','Boston','MA',42.3,-71.0,
                      'published',NOW(),'staff_5'
                    )
                    """
                )
            conn.execute(down)
            assert conn.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='vkpi_dealers' "
                "AND column_name='google_place_id'"
            ).fetchone()[0] == 0
        finally:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
