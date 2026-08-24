-- 298: bounded raw-profile avatar extraction capability ledger.
-- Stores only a tri-state result plus freshness/version receipts. No provider
-- URL is persisted here. FALSE is a rebuildable negative capability used to
-- avoid repeatedly parsing large raw content payloads after the same strict
-- extractor already proved that no profile avatar path exists.

ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS raw_profile_avatar_present BOOLEAN;
ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS raw_profile_avatar_extracted_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS raw_profile_avatar_extractor_version TEXT;

COMMENT ON COLUMN vkpi_kol_pool.raw_profile_avatar_present IS
  'Tri-state profile-avatar capability from raw payload; no URL; NULL means unknown';
COMMENT ON COLUMN vkpi_kol_pool.raw_profile_avatar_extracted_at IS
  'Freshness receipt for rebuildable raw profile-avatar capability';
COMMENT ON COLUMN vkpi_kol_pool.raw_profile_avatar_extractor_version IS
  'Strict raw profile-avatar capability extractor version';
