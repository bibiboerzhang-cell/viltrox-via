-- Roll back only migration-298 rebuildable capability evidence.

ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS raw_profile_avatar_extractor_version;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS raw_profile_avatar_extracted_at;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS raw_profile_avatar_present;

DELETE FROM schema_migrations
WHERE version_key='298_vkpi_kol_pool_raw_profile_avatar_capability.sql';
