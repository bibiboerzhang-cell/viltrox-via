ALTER TABLE vkpi_kol_video_evidence
  ADD COLUMN IF NOT EXISTS metrics_scraped_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS metrics_source VARCHAR(20),
  ADD COLUMN IF NOT EXISTS share_count BIGINT;

CREATE INDEX IF NOT EXISTS idx_evidence_metrics_source
  ON vkpi_kol_video_evidence(metrics_source)
  WHERE metrics_source IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_metrics_scraped_at
  ON vkpi_kol_video_evidence(metrics_scraped_at DESC)
  WHERE metrics_scraped_at IS NOT NULL;
