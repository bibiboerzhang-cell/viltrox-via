-- down for 118_vkpi_kol_pool_contact_audit.sql
DROP INDEX IF EXISTS idx_vkpi_kol_pool_contact_reveal;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_last_revealed_by_staff_id;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_last_revealed_at;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_reveal_count;