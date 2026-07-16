-- Durable, tenant-scoped idempotency boundary for billable Advisor turns.
-- No prompt, response body, credential or claim token is persisted in clear.

CREATE TABLE IF NOT EXISTS vkpi_advisor_turn_claims (
  organization_id BIGINT NOT NULL,
  staff_id BIGINT NOT NULL,
  thread_uid TEXT NOT NULL,
  client_request_id TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  claim_token_sha256 TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'claimed',
  provider_attempted BOOLEAN NOT NULL DEFAULT FALSE,
  provider_binding TEXT NOT NULL DEFAULT '',
  failure_code TEXT NOT NULL DEFAULT '',
  result_user_message_uid TEXT,
  result_assistant_message_uid TEXT,
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  lease_expires_at TIMESTAMPTZ NOT NULL,
  provider_started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (organization_id, staff_id, thread_uid, client_request_id),
  FOREIGN KEY (organization_id, staff_id, thread_uid)
    REFERENCES vkpi_advisor_threads (organization_id, staff_id, thread_uid)
    ON DELETE CASCADE,
  CHECK (client_request_id <> ''),
  CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (claim_token_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (state IN (
    'claimed',
    'provider_started',
    'completed',
    'failed_before_provider',
    'outcome_unknown'
  )),
  CHECK (NOT provider_attempted OR provider_started_at IS NOT NULL),
  CHECK (state <> 'provider_started' OR provider_attempted),
  CHECK (state <> 'outcome_unknown' OR provider_attempted),
  CHECK (state <> 'completed' OR completed_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_advisor_turn_claim_recovery
  ON vkpi_advisor_turn_claims (state, lease_expires_at)
  WHERE state IN ('claimed', 'provider_started');

CREATE INDEX IF NOT EXISTS idx_vkpi_advisor_turn_claim_owner_updated
  ON vkpi_advisor_turn_claims (organization_id, staff_id, updated_at DESC);
