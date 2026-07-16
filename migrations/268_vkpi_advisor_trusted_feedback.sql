-- 268: Owner-scoped Marketing Advisor feedback and trusted learning candidates.
--
-- Security and learning invariants:
--   * feedback belongs to one organization + staff owner and one assistant message;
--   * one current feedback row exists per owned assistant message;
--   * client_request_id replay is payload-bound and auditable;
--   * propose_memory may create only a pending candidate. It never creates a
--     memory fact, changes model weights, or starts a training job.
--
-- The migration runner owns the surrounding transaction and advisory lock.
-- Do not add BEGIN/COMMIT here.

CREATE TABLE IF NOT EXISTS vkpi_advisor_message_feedback (
    id BIGSERIAL PRIMARY KEY,
    feedback_uid TEXT NOT NULL,
    organization_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    thread_uid TEXT NOT NULL,
    message_uid TEXT NOT NULL,
    rating TEXT NOT NULL,
    correction_text TEXT NOT NULL DEFAULT '',
    propose_memory BOOLEAN NOT NULL DEFAULT FALSE,
    context_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    candidate_uid TEXT,
    last_client_request_id TEXT NOT NULL DEFAULT '',
    payload_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_advisor_feedback_thread
      FOREIGN KEY (organization_id, staff_id, thread_uid)
      REFERENCES vkpi_advisor_threads(organization_id, staff_id, thread_uid)
      ON DELETE CASCADE,
    CONSTRAINT fk_advisor_feedback_message
      FOREIGN KEY (organization_id, staff_id, message_uid)
      REFERENCES vkpi_advisor_messages(organization_id, staff_id, message_uid)
      ON DELETE CASCADE,
    CONSTRAINT fk_advisor_feedback_candidate
      FOREIGN KEY (organization_id, staff_id, candidate_uid)
      REFERENCES vkpi_advisor_memory_candidates(organization_id, staff_id, candidate_uid)
      ON DELETE RESTRICT,
    CONSTRAINT chk_advisor_feedback_org CHECK (organization_id > 0),
    CONSTRAINT chk_advisor_feedback_staff CHECK (staff_id > 0),
    CONSTRAINT chk_advisor_feedback_uid CHECK (
      feedback_uid <> '' AND length(feedback_uid) <= 80
    ),
    CONSTRAINT chk_advisor_feedback_rating CHECK (rating IN ('helpful','unhelpful')),
    CONSTRAINT chk_advisor_feedback_correction CHECK (length(correction_text) <= 4000),
    CONSTRAINT chk_advisor_feedback_request CHECK (length(last_client_request_id) <= 120),
    CONSTRAINT chk_advisor_feedback_payload_sha CHECK (
      payload_sha256 ~ '^[0-9a-f]{64}$'
    ),
    UNIQUE (organization_id, staff_id, feedback_uid),
    UNIQUE (organization_id, staff_id, message_uid)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_advisor_feedback_request
  ON vkpi_advisor_message_feedback(organization_id, staff_id, last_client_request_id)
  WHERE last_client_request_id <> '';

CREATE INDEX IF NOT EXISTS idx_advisor_feedback_thread
  ON vkpi_advisor_message_feedback(organization_id, staff_id, thread_uid, updated_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_advisor_message_feedback_events (
    id BIGSERIAL PRIMARY KEY,
    event_uid TEXT NOT NULL,
    feedback_uid TEXT NOT NULL,
    organization_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    thread_uid TEXT NOT NULL,
    message_uid TEXT NOT NULL,
    actor_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    before_sha256 TEXT NOT NULL DEFAULT '',
    after_sha256 TEXT NOT NULL,
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_advisor_feedback_event_owner
      FOREIGN KEY (organization_id, staff_id, feedback_uid)
      REFERENCES vkpi_advisor_message_feedback(organization_id, staff_id, feedback_uid)
      ON DELETE CASCADE,
    CONSTRAINT chk_advisor_feedback_event_uid CHECK (
      event_uid <> '' AND length(event_uid) <= 80
    ),
    CONSTRAINT chk_advisor_feedback_event_type CHECK (event_type IN ('created','updated')),
    CONSTRAINT chk_advisor_feedback_event_request_id CHECK (
      client_request_id <> '' AND length(client_request_id) <= 120
    ),
    CONSTRAINT chk_advisor_feedback_event_request_sha CHECK (
      request_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT chk_advisor_feedback_event_before_sha CHECK (
      before_sha256 = '' OR before_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT chk_advisor_feedback_event_after_sha CHECK (
      after_sha256 ~ '^[0-9a-f]{64}$'
    ),
    UNIQUE (organization_id, staff_id, event_uid),
    UNIQUE (organization_id, staff_id, client_request_id)
);

CREATE INDEX IF NOT EXISTS idx_advisor_feedback_events_owner
  ON vkpi_advisor_message_feedback_events(organization_id, staff_id, created_at DESC);

COMMENT ON TABLE vkpi_advisor_message_feedback IS
  'Owner-scoped human feedback for assistant messages; never an automatic training or activation trigger.';
COMMENT ON COLUMN vkpi_advisor_message_feedback.candidate_uid IS
  'Optional pending personal-memory candidate. Explicit confirmation is still required before a Fact can become active.';
COMMENT ON TABLE vkpi_advisor_message_feedback_events IS
  'Append-only audit trail for owner feedback mutations and payload-bound idempotency.';
