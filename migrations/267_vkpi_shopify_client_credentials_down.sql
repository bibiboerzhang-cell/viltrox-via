DROP INDEX IF EXISTS idx_vkpi_shopify_credentials_token_expiry;
DROP INDEX IF EXISTS idx_vkpi_shopify_credentials_refresh_lease;

ALTER TABLE vkpi_shopify_credentials
  DROP CONSTRAINT IF EXISTS chk_vkpi_shopify_credentials_auth_mode;

ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS revoked_at;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS refresh_failure_count;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS refresh_retry_after;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS refresh_lease_expires_at;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS refresh_lease_owner;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS last_refresh_at;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS granted_scopes;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS access_token_expires_at;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS client_secret_encrypted;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS client_id;
ALTER TABLE vkpi_shopify_credentials DROP COLUMN IF EXISTS auth_mode;

DELETE FROM schema_migrations
WHERE version_key = '267_vkpi_shopify_client_credentials.sql';
