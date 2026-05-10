-- migrations/052_vkpi_pillars.sql
-- P1.5: Content pillar classification

-- 1. Pillar 主表
CREATE TABLE IF NOT EXISTS vkpi_pillars (
  id SERIAL PRIMARY KEY,
  pillar_key VARCHAR(50) UNIQUE NOT NULL,
  display_name VARCHAR(100) NOT NULL,
  description TEXT,
  layer SMALLINT NOT NULL,
  is_active BOOLEAN DEFAULT TRUE,
  display_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Post-Pillar 关联表 (M:N)
CREATE TABLE IF NOT EXISTS vkpi_post_pillars (
  id BIGSERIAL PRIMARY KEY,
  post_id BIGINT NOT NULL,
  post_table VARCHAR(50) NOT NULL,
  pillar_id INT NOT NULL REFERENCES vkpi_pillars(id),
  is_primary BOOLEAN DEFAULT FALSE,
  confidence NUMERIC(3,2),
  
  llm_provider VARCHAR(50),
  llm_model VARCHAR(100),
  prompt_version VARCHAR(20),
  classified_at TIMESTAMPTZ DEFAULT NOW(),
  
  CONSTRAINT vkpi_post_pillar_uniq 
    UNIQUE (post_id, post_table, pillar_id, prompt_version)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_post_pillars_post 
  ON vkpi_post_pillars(post_id, post_table);

CREATE INDEX IF NOT EXISTS idx_vkpi_post_pillars_pillar 
  ON vkpi_post_pillars(pillar_id, classified_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_post_pillars_primary 
  ON vkpi_post_pillars(post_id, post_table) 
  WHERE is_primary;

-- ─── Seed Layer 1 (Generic) ─────────────────────────────────
INSERT INTO vkpi_pillars (pillar_key, display_name, layer, display_order, description) VALUES
  ('lifestyle',         'Lifestyle',           1, 100, 'Daily life, travel, personal moments'),
  ('education',         'Education',           1, 110, 'Academic, instructional, informative'),
  ('entertainment',     'Entertainment',       1, 120, 'Pure entertainment without specific niche'),
  ('product_review',    'Product Review',      1, 130, 'General product review (non-camera)'),
  ('tutorial',          'Tutorial',            1, 140, 'How-to (non-camera-specific)'),
  ('news_commentary',   'News/Commentary',     1, 150, 'News, opinion, commentary'),
  ('other',             'Other',               1, 199, 'Does not fit any specific category')
ON CONFLICT (pillar_key) DO NOTHING;

-- ─── Seed Layer 2 (Photography/Cinema) ──────────────────────
INSERT INTO vkpi_pillars (pillar_key, display_name, layer, display_order, description) VALUES
  ('lens_review',       'Lens Review',         2, 200, 'Specific lens unboxing, hands-on, review'),
  ('cinema_bts',        'Cinema Behind-Scenes', 2, 210, 'Film/cinema behind the scenes'),
  ('shooting_tutorial', 'Shooting Tutorial',   2, 220, 'How-to: shooting techniques'),
  ('vlog',              'Vlog',                2, 230, 'Personal vlog with camera content'),
  ('gear_comparison',   'Gear Comparison',     2, 240, 'Comparing multiple cameras/lenses'),
  ('lighting',          'Lighting',            2, 250, 'Lighting setup, technique, gear'),
  ('interview',         'Interview',           2, 260, 'Interview content (creator/expert)'),
  ('event_coverage',    'Event Coverage',      2, 270, 'Photography/cinema event coverage'),
  ('test_footage',      'Test Footage',        2, 280, 'Camera/lens test footage, sample shots'),
  ('color_grading',     'Color Grading',       2, 290, 'Color grading tutorials, looks, LUTs')
ON CONFLICT (pillar_key) DO NOTHING;

-- Layer 3: empty until team gathers data and adds discovered pillars
