-- 305: content-inferred creator language, stored SEPARATELY from the
-- platform self-reported vkpi_kol_pool.language column.
--
-- Why a separate column instead of overwriting language:
--   language              = what the platform / creator declared about themselves.
--   language_inferred     = what we estimated from the creator's own bio and
--                           video titles. Rebuildable, lower trust, must stay
--                           visually and queryably distinguishable for operators.
-- Never write an inferred value into the self-reported column.
--
-- Column quartet follows the existing derived-with-provenance pattern on this
-- table (real_er / real_er_method / real_er_sample_n / real_er_computed_at).

ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS language_inferred TEXT;
ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS language_inferred_confidence TEXT;
ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS language_inferred_source TEXT;
ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS language_inferred_sample_n INTEGER;
ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS language_inferred_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS language_inferred_method TEXT;

COMMENT ON COLUMN vkpi_kol_pool.language_inferred IS
  'Language estimated from creator-authored text (bio + video titles); NULL means unknown, never a self-reported value';
COMMENT ON COLUMN vkpi_kol_pool.language_inferred_confidence IS
  'high / medium / low agreement tier of the inference vote';
COMMENT ON COLUMN vkpi_kol_pool.language_inferred_source IS
  'Which creator-authored text carried the winning vote: bio, video_titles, or bio+video_titles';
COMMENT ON COLUMN vkpi_kol_pool.language_inferred_sample_n IS
  'How many text samples were fed to the detector';
COMMENT ON COLUMN vkpi_kol_pool.language_inferred_at IS
  'Freshness receipt for the rebuildable language inference';
COMMENT ON COLUMN vkpi_kol_pool.language_inferred_method IS
  'Inference engine version, e.g. kol_content_langdetect_vote_v1';

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_language_inferred
  ON vkpi_kol_pool (language_inferred);
