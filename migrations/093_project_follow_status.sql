ALTER TABLE vkpi_projects
  ADD COLUMN IF NOT EXISTS follow_status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE vkpi_projects
  DROP CONSTRAINT IF EXISTS chk_vkpi_projects_follow_status;

ALTER TABLE vkpi_projects
  ADD CONSTRAINT chk_vkpi_projects_follow_status
  CHECK (follow_status IN ('active', 'paused'));

COMMENT ON COLUMN vkpi_projects.follow_status IS
  'active=跟进中, paused=本轮暂停; paused projects must be skipped by auto stage derivation and future queue refresh/analysis jobs.';
