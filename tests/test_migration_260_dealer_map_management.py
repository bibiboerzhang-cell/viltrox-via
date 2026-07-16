from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP_PATH = ROOT / "migrations/260_vkpi_dealer_map_management.sql"
DOWN_PATH = ROOT / "migrations/260_vkpi_dealer_map_management_down.sql"


def _sql(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_migration_260_is_additive_and_keeps_truth_states_separate():
    sql = _sql(UP_PATH)

    assert "add column if not exists publication_status" in sql
    assert "add column if not exists viltrox_deployment_status" in sql
    assert "add column if not exists activity_status" in sql
    assert "add column if not exists website_url" in sql
    assert "add column if not exists social_links_json" in sql
    assert "create table if not exists vkpi_dealer_brand_relationships" in sql
    assert "primary key (dealer_id, brand_key)" in sql
    assert "authorization_status = 'unverified'" in sql
    assert "official_directory_listed" in sql
    assert "insert into vkpi_dealers" not in sql
    assert "or inventory" in sql
    assert "never authorization" in sql


def test_migration_260_preserves_legacy_reviewed_source_map_visibility_without_faking_reviewer():
    sql = _sql(UP_PATH)

    assert "update vkpi_dealers set publication_status = 'published'" in sql
    assert "source_status = 'public_listing_verified'" in sql
    assert "else 'system:migration260_legacy_visibility'" in sql
    assert "published_by = 'system:migration260_legacy_visibility'" in sql
    assert "review_contract_version = 1" not in sql
    assert "lat is not null" in sql
    assert "lng is not null" in sql


def test_migration_260_down_is_scoped_and_removes_its_schema_receipt():
    down = _sql(DOWN_PATH)

    assert "drop table if exists vkpi_dealer_brand_relationships" in down
    assert "drop column if exists publication_status" in down
    assert "drop column if exists viltrox_deployment_status" in down
    assert "drop column if exists activity_status" in down
    assert "drop column if exists website_url" in down
    assert "drop column if exists social_links_json" in down
    assert "where version_key = '260_vkpi_dealer_map_management.sql'" in down
    assert "drop table if exists vkpi_dealers" not in down
