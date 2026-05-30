-- 087_evidence_type.sql
-- Distinguish video evidence from media article evidence.

BEGIN;

ALTER TABLE vkpi_kol_video_evidence
  ADD COLUMN IF NOT EXISTS evidence_type VARCHAR(20) DEFAULT 'video';

CREATE INDEX IF NOT EXISTS idx_evidence_type
  ON vkpi_kol_video_evidence(evidence_type);

COMMIT;
