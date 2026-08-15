DROP INDEX IF EXISTS idx_vkpi_kol_contact_acquisition_due;
DROP TABLE IF EXISTS vkpi_kol_contact_acquisition_queue;

DROP INDEX IF EXISTS idx_vkpi_kol_contact_suppression_active;
DROP TABLE IF EXISTS vkpi_kol_contact_suppressions;

DROP INDEX IF EXISTS idx_vkpi_kol_contact_evidence_source;
DROP INDEX IF EXISTS idx_vkpi_kol_contact_evidence_pool;
DROP TABLE IF EXISTS vkpi_kol_contact_evidence;

DROP INDEX IF EXISTS idx_vkpi_kol_contact_verification;
DROP INDEX IF EXISTS uq_vkpi_kol_contact_normalized;
ALTER TABLE vkpi_kol_pool_contacts
    DROP CONSTRAINT IF EXISTS chk_vkpi_kol_contact_raw_full_not_verified;
ALTER TABLE vkpi_kol_pool_contacts
    DROP CONSTRAINT IF EXISTS chk_vkpi_kol_contact_verification_status;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS revoked_at;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS invalidated_at;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS verified_at;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS verification_status;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS channel;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS normalized_value;
