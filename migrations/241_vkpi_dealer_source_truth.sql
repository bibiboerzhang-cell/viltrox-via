-- 241: Dealer 公开来源与授权状态分离。
-- “公开在售/门店存在”不等于“Viltrox 官方授权”，两条证据链必须分别记录。

ALTER TABLE vkpi_dealers
  ADD COLUMN IF NOT EXISTS country TEXT NOT NULL DEFAULT 'US',
  ADD COLUMN IF NOT EXISTS brand_listing_url TEXT,
  ADD COLUMN IF NOT EXISTS location_source_url TEXT,
  ADD COLUMN IF NOT EXISTS source_status TEXT NOT NULL DEFAULT 'unverified',
  ADD COLUMN IF NOT EXISTS authorization_status TEXT NOT NULL DEFAULT 'needs_viltrox_confirmation',
  ADD COLUMN IF NOT EXISTS source_checked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS verification_note TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_source_status
  ON vkpi_dealers(source_status);
CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_authorization_status
  ON vkpi_dealers(authorization_status);

COMMENT ON COLUMN vkpi_dealers.brand_listing_url IS '零售商公开 Viltrox 在售页或产品页。';
COMMENT ON COLUMN vkpi_dealers.location_source_url IS '零售商官方门店地址页。';
COMMENT ON COLUMN vkpi_dealers.authorization_status IS
  'Viltrox 授权关系单独核验；公开在售不得自动推断为官方授权。';
