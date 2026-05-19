-- V-KPI P3 Memory v0.
-- Stores explainable business memory facts derived from committed operational
-- data. JSON payloads use TEXT for compatibility with the current DB layer.

CREATE TABLE IF NOT EXISTS vkpi_memory_entities (
  id BIGSERIAL PRIMARY KEY,
  entity_uid TEXT NOT NULL UNIQUE,
  entity_type TEXT NOT NULL,
  identity_key TEXT NOT NULL,
  display_name TEXT DEFAULT '',
  source_table TEXT DEFAULT '',
  source_id TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  confidence_score NUMERIC(8,5) DEFAULT 1.0,
  identity_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(entity_type, identity_key)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_entities_type
  ON vkpi_memory_entities(entity_type, status);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_entities_source
  ON vkpi_memory_entities(source_table, source_id);

CREATE TABLE IF NOT EXISTS vkpi_memory_facts (
  id BIGSERIAL PRIMARY KEY,
  fact_uid TEXT NOT NULL UNIQUE,
  entity_id BIGINT NOT NULL REFERENCES vkpi_memory_entities(id) ON DELETE CASCADE,
  fact_type TEXT NOT NULL,
  fact_key TEXT NOT NULL DEFAULT '',
  fact_value_text TEXT DEFAULT '',
  confidence_score NUMERIC(8,5) DEFAULT 1.0,
  source_ref TEXT NOT NULL DEFAULT '',
  source_table TEXT DEFAULT '',
  source_id TEXT DEFAULT '',
  fact_json TEXT NOT NULL DEFAULT '{}',
  source_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(entity_id, fact_type, fact_key, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_facts_entity
  ON vkpi_memory_facts(entity_id, fact_type);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_facts_type
  ON vkpi_memory_facts(fact_type, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_facts_source
  ON vkpi_memory_facts(source_ref);

CREATE TABLE IF NOT EXISTS vkpi_memory_links (
  id BIGSERIAL PRIMARY KEY,
  link_uid TEXT NOT NULL UNIQUE,
  source_entity_id BIGINT NOT NULL REFERENCES vkpi_memory_entities(id) ON DELETE CASCADE,
  target_entity_id BIGINT NOT NULL REFERENCES vkpi_memory_entities(id) ON DELETE CASCADE,
  link_type TEXT NOT NULL,
  weight NUMERIC(8,5) DEFAULT 1.0,
  confidence_score NUMERIC(8,5) DEFAULT 1.0,
  source_ref TEXT NOT NULL DEFAULT '',
  source_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(source_entity_id, target_entity_id, link_type, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_links_source
  ON vkpi_memory_links(source_entity_id, link_type);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_links_target
  ON vkpi_memory_links(target_entity_id, link_type);

CREATE TABLE IF NOT EXISTS vkpi_memory_feedback (
  id BIGSERIAL PRIMARY KEY,
  feedback_uid TEXT NOT NULL UNIQUE,
  entity_id BIGINT REFERENCES vkpi_memory_entities(id) ON DELETE SET NULL,
  fact_id BIGINT REFERENCES vkpi_memory_facts(id) ON DELETE SET NULL,
  link_id BIGINT REFERENCES vkpi_memory_links(id) ON DELETE SET NULL,
  feedback_type TEXT NOT NULL,
  rating INTEGER,
  status TEXT NOT NULL DEFAULT 'open',
  created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  resolved_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  feedback_json TEXT NOT NULL DEFAULT '{}',
  resolution_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_feedback_entity
  ON vkpi_memory_feedback(entity_id, status);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_feedback_status
  ON vkpi_memory_feedback(status, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_memory_snapshots (
  id BIGSERIAL PRIMARY KEY,
  snapshot_uid TEXT NOT NULL UNIQUE,
  scope TEXT NOT NULL,
  source_ref TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'completed',
  entity_count INTEGER NOT NULL DEFAULT 0,
  fact_count INTEGER NOT NULL DEFAULT 0,
  link_count INTEGER NOT NULL DEFAULT 0,
  feedback_count INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_memory_snapshots_scope
  ON vkpi_memory_snapshots(scope, created_at DESC);
