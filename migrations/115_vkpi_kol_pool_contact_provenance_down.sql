-- down for 114。不进迁移序列,仅手工回滚用。
DROP TABLE IF EXISTS vkpi_kol_pool_contacts;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_first_seen_at;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_consent_basis;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_source_detail;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_source;
