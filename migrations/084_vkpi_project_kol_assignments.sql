-- 084_vkpi_project_kol_assignments.sql
-- Bridge Excel's 1:N Project x KOL cooperation records into V-KPI.

BEGIN;

CREATE TABLE IF NOT EXISTS vkpi_project_kol_assignments (
  id BIGSERIAL PRIMARY KEY,
  project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
  kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
  stage VARCHAR(30) NOT NULL,
  stage_status VARCHAR(20) DEFAULT 'active',
  assigned_staff_id BIGINT,
  tracking_number VARCHAR(100),
  is_placeholder_tracking BOOLEAN DEFAULT FALSE,
  source VARCHAR(20) DEFAULT 'excel',
  source_ref TEXT,
  excel_progress VARCHAR(50),
  metadata_json JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(project_id, kol_pool_id)
);

CREATE INDEX IF NOT EXISTS idx_pka_project
  ON vkpi_project_kol_assignments(project_id);

CREATE INDEX IF NOT EXISTS idx_pka_kol_pool
  ON vkpi_project_kol_assignments(kol_pool_id);

CREATE INDEX IF NOT EXISTS idx_pka_stage
  ON vkpi_project_kol_assignments(stage);

CREATE INDEX IF NOT EXISTS idx_pka_active
  ON vkpi_project_kol_assignments(stage)
  WHERE stage IN ('content_posted', 'reviewed');

COMMENT ON TABLE vkpi_project_kol_assignments IS
  'KOL x Project 多对多桥接, 从 Excel 32 个产品 sheet 导入';

COMMIT;
