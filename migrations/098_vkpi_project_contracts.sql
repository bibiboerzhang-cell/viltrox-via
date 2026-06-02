CREATE TABLE IF NOT EXISTS vkpi_project_contracts (
  id BIGSERIAL PRIMARY KEY,
  project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
  assignment_id BIGINT NULL REFERENCES vkpi_project_kol_assignments(id) ON DELETE SET NULL,
  kol_pool_id BIGINT NULL REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
  file_name TEXT NOT NULL DEFAULT '',
  r2_key TEXT NOT NULL,
  file_size_bytes BIGINT NOT NULL DEFAULT 0,
  mime_type TEXT NOT NULL DEFAULT '',
  uploaded_by_user_id BIGINT NULL,
  status TEXT NOT NULL DEFAULT 'extracted',
  extraction_status TEXT NOT NULL DEFAULT 'pending',
  fee_amount NUMERIC NULL,
  fee_currency TEXT NOT NULL DEFAULT 'USD',
  contract_duration TEXT NOT NULL DEFAULT '',
  start_date DATE NULL,
  end_date DATE NULL,
  platforms_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  deliverable_count INTEGER NULL,
  deliverables_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  must_include_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  usage_rights TEXT NOT NULL DEFAULT '',
  exclusivity TEXT NOT NULL DEFAULT '',
  buyout_rights TEXT NOT NULL DEFAULT '',
  breach_terms TEXT NOT NULL DEFAULT '',
  payment_terms TEXT NOT NULL DEFAULT '',
  cancellation_terms TEXT NOT NULL DEFAULT '',
  revision_terms TEXT NOT NULL DEFAULT '',
  raw_extracted_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  field_confidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  field_confirmed_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  confirmed_by_user_id BIGINT NULL,
  confirmed_at TIMESTAMPTZ NULL,
  manual_overrides_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  promised_platforms_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  promised_deliverables_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  promised_publish_deadline TIMESTAMPTZ NULL,
  promised_must_include_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_vkpi_project_contracts_status
    CHECK (status IN ('draft','extracted','confirmed','needs_review','void')),
  CONSTRAINT chk_vkpi_project_contracts_extraction_status
    CHECK (extraction_status IN ('pending','processing','ready','failed','skipped'))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_project_contracts_project
  ON vkpi_project_contracts (project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_project_contracts_assignment
  ON vkpi_project_contracts (assignment_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_project_contracts_kol
  ON vkpi_project_contracts (kol_pool_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_project_contracts_project_assignment_r2
  ON vkpi_project_contracts (project_id, COALESCE(assignment_id, 0), r2_key);

COMMENT ON TABLE vkpi_project_contracts IS
  'Project KOL contract archive. LLM extracted fields are draft facts until manually confirmed.';

COMMENT ON COLUMN vkpi_project_contracts.field_confidence_json IS
  'Per extracted field confidence from LLM. Do not treat legal/financial fields as final until confirmed.';

COMMENT ON COLUMN vkpi_project_contracts.field_confirmed_json IS
  'Per field human confirmation flags used by retrospective obligation matching.';

COMMENT ON COLUMN vkpi_project_contracts.promised_deliverables_json IS
  'Reserved for future fulfillment comparison against actual video/content output.';

INSERT INTO vkpi_provider_budget_caps
  (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
  ('single_call_contract', 5.00, 0, 0.80, 1.00, NULL, 'block_contract_extract',
   '{"seeded_by":"098_vkpi_project_contracts","tier":"hard_stop","provider":"claude","target_types":["contract"]}'),
  ('cron:vkpi_contract_extract', 25.00, 0, 0.80, 1.00, NULL, 'block_contract_extract',
   '{"seeded_by":"098_vkpi_project_contracts","tier":"manual_upload","provider":"claude","target_types":["contract"]}')
ON CONFLICT (scope) DO NOTHING;
