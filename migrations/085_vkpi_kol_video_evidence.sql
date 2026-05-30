-- 085_vkpi_kol_video_evidence.sql
-- Store Viltrox video evidence from Excel, manual enrichment, and scrapers.

BEGIN;

CREATE TABLE IF NOT EXISTS vkpi_kol_video_evidence (
  id BIGSERIAL PRIMARY KEY,
  kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
  project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE SET NULL,
  content_url TEXT NOT NULL,
  platform VARCHAR(20),
  video_title TEXT,
  posted_at DATE,
  view_count BIGINT,
  like_count BIGINT,
  comment_count BIGINT,
  source VARCHAR(20) NOT NULL,
  source_ref TEXT,
  confidence VARCHAR(10) DEFAULT 'high',
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(content_url)
);

CREATE INDEX IF NOT EXISTS idx_evidence_kol_pool
  ON vkpi_kol_video_evidence(kol_pool_id);

CREATE INDEX IF NOT EXISTS idx_evidence_project
  ON vkpi_kol_video_evidence(project_id);

CREATE INDEX IF NOT EXISTS idx_evidence_active
  ON vkpi_kol_video_evidence(is_active, kol_pool_id)
  WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_evidence_posted_at
  ON vkpi_kol_video_evidence(posted_at);

COMMENT ON TABLE vkpi_kol_video_evidence IS
  '视频证据 - 一个 KOL 可有多条视频, 是 Active Roster 真实化的核心';

COMMIT;
