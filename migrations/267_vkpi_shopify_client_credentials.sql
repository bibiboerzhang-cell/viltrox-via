-- 267: Shopify organization-owned app Client Credentials grant.
-- The migration runner owns the transaction boundary; do not add BEGIN/COMMIT.

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS auth_mode TEXT NOT NULL DEFAULT 'legacy_access_token';

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS client_id TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS client_secret_encrypted TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS access_token_expires_at TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS granted_scopes TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS last_refresh_at TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS refresh_lease_owner TEXT;

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS refresh_lease_expires_at TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS refresh_retry_after TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_credentials
  ADD COLUMN IF NOT EXISTS refresh_failure_count INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_vkpi_shopify_credentials_auth_mode'
      AND conrelid='vkpi_shopify_credentials'::regclass
  ) THEN
    ALTER TABLE vkpi_shopify_credentials
      ADD CONSTRAINT chk_vkpi_shopify_credentials_auth_mode
      CHECK (auth_mode IN ('client_credentials','legacy_access_token'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_credentials_token_expiry
  ON vkpi_shopify_credentials(auth_mode, access_token_expires_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_credentials_refresh_lease
  ON vkpi_shopify_credentials(auth_mode, refresh_lease_expires_at, refresh_retry_after);

COMMENT ON COLUMN vkpi_shopify_credentials.client_secret_encrypted IS
  'Fernet-encrypted organization app Client Secret. Never returned. Also materialized into webhook_secret_encrypted for native Shopify HMAC verification.';

COMMENT ON COLUMN vkpi_shopify_credentials.granted_scopes IS
  'Normalized comma-separated scopes returned by Shopify token exchange.';

COMMENT ON COLUMN vkpi_shopify_credentials.refresh_lease_owner IS
  'Opaque owner for a short committed refresh lease. External HTTP never runs inside its transaction.';
