CREATE TABLE IF NOT EXISTS vkpi_project_stars (
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_project_stars_user_created
  ON vkpi_project_stars (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_project_stars_project
  ON vkpi_project_stars (project_id);
