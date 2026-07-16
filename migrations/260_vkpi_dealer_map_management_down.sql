DROP INDEX IF EXISTS idx_vkpi_dealer_activity_watch;
DROP INDEX IF EXISTS idx_vkpi_dealer_brand_lookup;
DROP INDEX IF EXISTS idx_vkpi_dealers_publication_geo;
DROP INDEX IF EXISTS idx_vkpi_dealers_publication;

DROP TABLE IF EXISTS vkpi_dealer_brand_relationships;

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_activity_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_social_links_array,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_website_url,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_viltrox_deployed_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_viltrox_deployment_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_published_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_publication_status;

ALTER TABLE vkpi_dealers
  DROP COLUMN IF EXISTS updated_at,
  DROP COLUMN IF EXISTS social_links_json,
  DROP COLUMN IF EXISTS website_url,
  DROP COLUMN IF EXISTS activity_note,
  DROP COLUMN IF EXISTS next_activity_at,
  DROP COLUMN IF EXISTS activity_checked_at,
  DROP COLUMN IF EXISTS activity_page_url,
  DROP COLUMN IF EXISTS activity_status,
  DROP COLUMN IF EXISTS viltrox_deployment_note,
  DROP COLUMN IF EXISTS viltrox_deployed_by,
  DROP COLUMN IF EXISTS viltrox_deployed_at,
  DROP COLUMN IF EXISTS viltrox_deployment_status,
  DROP COLUMN IF EXISTS published_by,
  DROP COLUMN IF EXISTS published_at,
  DROP COLUMN IF EXISTS publication_status;

DELETE FROM schema_migrations
WHERE version_key = '260_vkpi_dealer_map_management.sql';
