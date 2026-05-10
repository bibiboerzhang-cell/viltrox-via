-- migrations/053_vkpi_weekly_reports.sql
-- P1.6: Weekly report generation (3-layer)

-- Current V-KPI uses staff + users, not vkpi_staff.
-- Keep P1.6 schema additive and avoid mutating staff records here.

-- 1. Weekly reports table
CREATE TABLE IF NOT EXISTS vkpi_weekly_reports (
  id BIGSERIAL PRIMARY KEY,
  staff_id INT,
  layer SMALLINT NOT NULL,
  template_key VARCHAR(50) NOT NULL,
  
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  
  title VARCHAR(200),
  body_md TEXT,
  
  -- LLM metadata
  llm_provider VARCHAR(50),
  llm_model VARCHAR(100),
  prompt_version VARCHAR(20),
  input_tokens INT DEFAULT 0,
  output_tokens INT DEFAULT 0,
  cost_cents INT DEFAULT 0,
  
  -- Delivery
  status VARCHAR(20) DEFAULT 'draft',
  sent_at TIMESTAMPTZ,
  delivery_channels TEXT,
  
  generated_at TIMESTAMPTZ DEFAULT NOW(),
  
  CONSTRAINT vkpi_weekly_reports_uniq 
    UNIQUE (staff_id, layer, template_key, period_start)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_weekly_reports_period 
  ON vkpi_weekly_reports(period_start DESC, period_end DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_weekly_reports_staff 
  ON vkpi_weekly_reports(staff_id, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_weekly_reports_status 
  ON vkpi_weekly_reports(status, generated_at DESC);
