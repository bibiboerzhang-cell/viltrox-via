ALTER TABLE vkpi_kol_profile_index_entries
  ADD COLUMN IF NOT EXISTS profile_type TEXT,
  ADD COLUMN IF NOT EXISTS creator_type_score NUMERIC(5, 2),
  ADD COLUMN IF NOT EXISTS reviewer_type_score NUMERIC(5, 2),
  ADD COLUMN IF NOT EXISTS type_reason TEXT,
  ADD COLUMN IF NOT EXISTS type_method TEXT DEFAULT 'rule_creator_reviewer_v1',
  ADD COLUMN IF NOT EXISTS typed_at TIMESTAMPTZ;
