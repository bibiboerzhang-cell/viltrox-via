-- 250: Marketing Advisor persistent conversations and per-staff memory.
--
-- Security invariants:
--   * every user-owned row carries BOTH organization_id and staff_id;
--   * child foreign keys include the complete tenant/user scope;
--   * memory candidates never become active facts without an explicit confirm;
--   * business, outbound and cost-bearing requests are stored as drafts only.
--
-- The migration runner owns the surrounding transaction and advisory lock.
-- Do not add BEGIN/COMMIT here.

CREATE TABLE IF NOT EXISTS vkpi_advisor_threads (
    id BIGSERIAL PRIMARY KEY,
    thread_uid TEXT NOT NULL,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    context_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_advisor_thread_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_thread_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_thread_uid CHECK (thread_uid <> '' AND length(thread_uid) <= 80),
    CONSTRAINT chk_advisor_thread_title CHECK (length(title) <= 240),
    CONSTRAINT chk_advisor_thread_status CHECK (status IN ('active','archived','deleted')),
    UNIQUE (organization_id, staff_id, thread_uid)
);

CREATE INDEX IF NOT EXISTS idx_advisor_threads_owner
  ON vkpi_advisor_threads(organization_id, staff_id, updated_at DESC)
  WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS vkpi_advisor_messages (
    id BIGSERIAL PRIMARY KEY,
    message_uid TEXT NOT NULL,
    organization_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    thread_uid TEXT NOT NULL,
    role TEXT NOT NULL,
    content_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready',
    provider_status TEXT NOT NULL DEFAULT 'not_requested',
    provider_reason TEXT NOT NULL DEFAULT '',
    context_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    client_request_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT fk_advisor_message_thread
      FOREIGN KEY (organization_id, staff_id, thread_uid)
      REFERENCES vkpi_advisor_threads(organization_id, staff_id, thread_uid)
      ON DELETE CASCADE,
    CONSTRAINT chk_advisor_message_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_message_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_message_uid CHECK (message_uid <> '' AND length(message_uid) <= 80),
    CONSTRAINT chk_advisor_message_role CHECK (role IN ('user','assistant','system')),
    CONSTRAINT chk_advisor_message_status CHECK (status IN ('ready','degraded','failed','draft')),
    CONSTRAINT chk_advisor_message_provider CHECK (
      provider_status IN ('not_requested','ready','unavailable','blocked','failed')
    ),
    CONSTRAINT chk_advisor_message_request CHECK (length(client_request_id) <= 120),
    UNIQUE (organization_id, staff_id, message_uid)
);

CREATE INDEX IF NOT EXISTS idx_advisor_messages_thread
  ON vkpi_advisor_messages(organization_id, staff_id, thread_uid, id ASC)
  WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_advisor_message_request
  ON vkpi_advisor_messages(organization_id, staff_id, thread_uid, client_request_id)
  WHERE client_request_id <> '' AND role = 'user' AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS vkpi_advisor_memory_settings (
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'active',
    retention_days INTEGER NOT NULL DEFAULT 180,
    updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, staff_id),
    CONSTRAINT chk_advisor_memory_setting_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_memory_setting_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_memory_setting_state CHECK (state IN ('active','paused')),
    CONSTRAINT chk_advisor_memory_retention CHECK (retention_days BETWEEN 1 AND 3650)
);

CREATE TABLE IF NOT EXISTS vkpi_advisor_memory_candidates (
    id BIGSERIAL PRIMARY KEY,
    candidate_uid TEXT NOT NULL,
    organization_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    source_message_uid TEXT,
    memory_kind TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'pending',
    confirmed_fact_uid TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    CONSTRAINT fk_advisor_candidate_owner
      FOREIGN KEY (organization_id, staff_id)
      REFERENCES vkpi_advisor_memory_settings(organization_id, staff_id)
      ON DELETE CASCADE,
    CONSTRAINT fk_advisor_candidate_message
      FOREIGN KEY (organization_id, staff_id, source_message_uid)
      REFERENCES vkpi_advisor_messages(organization_id, staff_id, message_uid)
      ON DELETE RESTRICT,
    CONSTRAINT chk_advisor_candidate_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_candidate_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_candidate_uid CHECK (candidate_uid <> '' AND length(candidate_uid) <= 80),
    CONSTRAINT chk_advisor_candidate_kind CHECK (
      memory_kind IN ('preference','semantic','episodic','business_goal','constraint')
    ),
    CONSTRAINT chk_advisor_candidate_key CHECK (memory_key <> '' AND length(memory_key) <= 160),
    CONSTRAINT chk_advisor_candidate_summary CHECK (length(summary) <= 2000),
    CONSTRAINT chk_advisor_candidate_sensitivity CHECK (
      sensitivity IN ('normal','sensitive','restricted')
    ),
    CONSTRAINT chk_advisor_candidate_status CHECK (
      status IN ('pending','confirmed','rejected','deleted')
    ),
    UNIQUE (organization_id, staff_id, candidate_uid)
);

CREATE INDEX IF NOT EXISTS idx_advisor_memory_candidates_owner
  ON vkpi_advisor_memory_candidates(organization_id, staff_id, status, created_at DESC)
  WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS vkpi_advisor_memory_facts (
    id BIGSERIAL PRIMARY KEY,
    fact_uid TEXT NOT NULL,
    organization_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    source_candidate_uid TEXT,
    memory_kind TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT fk_advisor_fact_owner
      FOREIGN KEY (organization_id, staff_id)
      REFERENCES vkpi_advisor_memory_settings(organization_id, staff_id)
      ON DELETE CASCADE,
    CONSTRAINT fk_advisor_fact_candidate
      FOREIGN KEY (organization_id, staff_id, source_candidate_uid)
      REFERENCES vkpi_advisor_memory_candidates(organization_id, staff_id, candidate_uid)
      ON DELETE RESTRICT,
    CONSTRAINT chk_advisor_fact_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_fact_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_fact_uid CHECK (fact_uid <> '' AND length(fact_uid) <= 80),
    CONSTRAINT chk_advisor_fact_kind CHECK (
      memory_kind IN ('preference','semantic','episodic','business_goal','constraint')
    ),
    CONSTRAINT chk_advisor_fact_key CHECK (memory_key <> '' AND length(memory_key) <= 160),
    CONSTRAINT chk_advisor_fact_summary CHECK (length(summary) <= 2000),
    CONSTRAINT chk_advisor_fact_sensitivity CHECK (
      sensitivity IN ('normal','sensitive','restricted')
    ),
    CONSTRAINT chk_advisor_fact_status CHECK (status IN ('active','paused','deleted')),
    CONSTRAINT chk_advisor_fact_version CHECK (version > 0),
    UNIQUE (organization_id, staff_id, fact_uid)
);

CREATE INDEX IF NOT EXISTS idx_advisor_memory_facts_owner
  ON vkpi_advisor_memory_facts(organization_id, staff_id, status, updated_at DESC)
  WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_advisor_memory_fact_key
  ON vkpi_advisor_memory_facts(organization_id, staff_id, memory_key)
  WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS vkpi_advisor_action_drafts (
    id BIGSERIAL PRIMARY KEY,
    draft_uid TEXT NOT NULL,
    organization_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    thread_uid TEXT NOT NULL,
    source_message_uid TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    estimated_cost_cents INTEGER NOT NULL DEFAULT 0,
    writes_business_data BOOLEAN NOT NULL DEFAULT FALSE,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMPTZ,
    CONSTRAINT fk_advisor_draft_thread
      FOREIGN KEY (organization_id, staff_id, thread_uid)
      REFERENCES vkpi_advisor_threads(organization_id, staff_id, thread_uid)
      ON DELETE CASCADE,
    CONSTRAINT fk_advisor_draft_message
      FOREIGN KEY (organization_id, staff_id, source_message_uid)
      REFERENCES vkpi_advisor_messages(organization_id, staff_id, message_uid)
      ON DELETE CASCADE,
    CONSTRAINT chk_advisor_draft_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_draft_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_draft_uid CHECK (draft_uid <> '' AND length(draft_uid) <= 80),
    CONSTRAINT chk_advisor_draft_action CHECK (
      action_type IN ('send_message','external_contact','write_business_data','incur_cost','business_change')
    ),
    CONSTRAINT chk_advisor_draft_target CHECK (length(target_type) <= 40 AND length(target_id) <= 160),
    CONSTRAINT chk_advisor_draft_cost CHECK (estimated_cost_cents >= 0),
    CONSTRAINT chk_advisor_draft_status CHECK (status IN ('draft','cancelled')),
    UNIQUE (organization_id, staff_id, draft_uid)
);

CREATE INDEX IF NOT EXISTS idx_advisor_action_drafts_owner
  ON vkpi_advisor_action_drafts(organization_id, staff_id, thread_uid, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_advisor_memory_events (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    actor_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_uid TEXT NOT NULL,
    before_sha256 TEXT NOT NULL DEFAULT '',
    after_sha256 TEXT NOT NULL DEFAULT '',
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_advisor_memory_event_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_memory_event_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_memory_event_type CHECK (
      event_type IN ('candidate_created','confirmed','edited','paused','resumed','rejected','deleted')
    ),
    CONSTRAINT chk_advisor_memory_subject_type CHECK (
      subject_type IN ('settings','candidate','fact')
    ),
    CONSTRAINT chk_advisor_memory_subject_uid CHECK (subject_uid <> '' AND length(subject_uid) <= 160),
    CONSTRAINT chk_advisor_memory_before_sha CHECK (
      before_sha256 = '' OR before_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT chk_advisor_memory_after_sha CHECK (
      after_sha256 = '' OR after_sha256 ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_advisor_memory_events_owner
  ON vkpi_advisor_memory_events(organization_id, staff_id, created_at DESC);

COMMENT ON TABLE vkpi_advisor_threads IS
  'Private, organization-and-staff scoped Marketing Advisor conversations.';
COMMENT ON TABLE vkpi_advisor_memory_candidates IS
  'Untrusted proposed memories. A candidate is never retrievable as an active fact until the owner explicitly confirms it.';
COMMENT ON TABLE vkpi_advisor_memory_facts IS
  'Owner-confirmed personal memory facts; business entities remain references with provenance rather than copied source-of-truth records.';
COMMENT ON TABLE vkpi_advisor_action_drafts IS
  'Non-executable drafts only. This table grants no authority to send, write business state, or incur cost.';
