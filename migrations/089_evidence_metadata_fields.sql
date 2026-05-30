-- YouTube evidence metadata fields for Step 4a scraper output.
-- Intentionally nullable so existing Excel/media evidence rows remain valid.

ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS view_count BIGINT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS like_count BIGINT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS comment_count BIGINT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS publish_date TIMESTAMPTZ;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS channel_id TEXT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS channel_name TEXT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS scrape_status VARCHAR(20);
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS scraped_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS scrape_error TEXT;
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS scrape_source VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_evidence_scrape_status
  ON vkpi_kol_video_evidence(scrape_status)
  WHERE scrape_status IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_publish_date
  ON vkpi_kol_video_evidence(publish_date DESC)
  WHERE publish_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_scrape_source
  ON vkpi_kol_video_evidence(scrape_source)
  WHERE scrape_source IS NOT NULL;
