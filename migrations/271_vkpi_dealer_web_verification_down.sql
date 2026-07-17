DROP INDEX IF EXISTS idx_vkpi_dealer_webverify_carries;
DROP INDEX IF EXISTS idx_vkpi_dealer_webverify_prominence;
DROP INDEX IF EXISTS idx_vkpi_dealer_webverify_latest;

-- Verification receipts are derived data; rollback removes the whole history.
-- vkpi_dealers itself is never touched by 271 in either direction.
DROP TABLE IF EXISTS vkpi_dealer_web_verification;

DELETE FROM schema_migrations
WHERE version_key = '271_vkpi_dealer_web_verification.sql';
