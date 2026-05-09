-- KOL public profile/contact enrichment.
-- Stores real account-level fields extracted from platform scans.

ALTER TABLE kols ADD COLUMN IF NOT EXISTS avatar_url TEXT DEFAULT '';
ALTER TABLE kols ADD COLUMN IF NOT EXISTS profile_url TEXT DEFAULT '';
ALTER TABLE kols ADD COLUMN IF NOT EXISTS contact_links_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE kols ADD COLUMN IF NOT EXISTS contact_raw_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_kols_profile_url ON kols(profile_url);
