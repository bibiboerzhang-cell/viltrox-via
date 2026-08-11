-- 277: immutable server-owned Action -> Project -> Outreach truth bridge.
--
-- A GTM reply prediction may only become evaluable after a manager binds its
-- immutable prediction run to an existing project and the first outbound
-- message inside the contracted seven-day observation window.  The API derives
-- every evidence field from server rows; client message metadata is never a
-- binding source.  The migration runner owns the surrounding transaction.

CREATE TABLE IF NOT EXISTS vkpi_action_outreach_truth_bridges (
    id                         BIGSERIAL PRIMARY KEY,
    organization_id            BIGINT NOT NULL,
    action_inbox_id             BIGINT NOT NULL REFERENCES vkpi_action_inbox(id) ON DELETE RESTRICT,
    prediction_organization_id  TEXT NOT NULL,
    prediction_run_id           TEXT NOT NULL,
    project_id                  BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE RESTRICT,
    kol_pool_id                 BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE RESTRICT,
    kol_id                      BIGINT NOT NULL REFERENCES kols(id) ON DELETE RESTRICT,
    product_sku                 TEXT NOT NULL,
    channel                     TEXT NOT NULL,
    first_outbound_message_id   BIGINT NOT NULL REFERENCES vkpi_messages(id) ON DELETE RESTRICT,
    first_outbound_at           TIMESTAMPTZ NOT NULL,
    observation_start_at        TIMESTAMPTZ NOT NULL,
    observation_end_at          TIMESTAMPTZ NOT NULL,
    action_approved_at           TIMESTAMPTZ NOT NULL,
    approval_snapshot_sha256     TEXT NOT NULL,
    first_outbound_created_at    TIMESTAMPTZ NOT NULL,
    actor_staff_id              BIGINT NOT NULL,
    correlation_id              TEXT NOT NULL,
    request_fingerprint         TEXT NOT NULL,
    binding_fingerprint         TEXT NOT NULL,
    verified_at                 TIMESTAMPTZ NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_vkpi_outreach_truth_prediction
      FOREIGN KEY (prediction_organization_id, prediction_run_id)
      REFERENCES vkpi_prediction_runs(organization_id, run_id) ON DELETE RESTRICT,
    CONSTRAINT uq_vkpi_outreach_truth_action UNIQUE (organization_id, action_inbox_id),
    CONSTRAINT uq_vkpi_outreach_truth_correlation UNIQUE (organization_id, correlation_id),
    CONSTRAINT uq_vkpi_outreach_truth_prediction
      UNIQUE (prediction_organization_id, prediction_run_id),
    CONSTRAINT chk_vkpi_outreach_truth_org1 CHECK (organization_id = 1),
    CONSTRAINT chk_vkpi_outreach_truth_prediction_org
      CHECK (prediction_organization_id = 'viltrox'),
    CONSTRAINT chk_vkpi_outreach_truth_window
      CHECK (
        first_outbound_at >= observation_start_at
        AND first_outbound_at >= action_approved_at
        AND first_outbound_at <= observation_end_at
        AND first_outbound_at <= first_outbound_created_at
        AND first_outbound_created_at <= observation_end_at
        AND first_outbound_created_at <= verified_at
        AND observation_end_at = observation_start_at + INTERVAL '7 days'
      ),
    CONSTRAINT chk_vkpi_outreach_truth_nonempty
      CHECK (
        length(btrim(product_sku)) > 0
        AND length(btrim(channel)) > 0
        AND length(btrim(correlation_id)) BETWEEN 8 AND 160
      ),
    CONSTRAINT chk_vkpi_outreach_truth_hashes
      CHECK (
        request_fingerprint ~ '^[0-9a-f]{64}$'
        AND binding_fingerprint ~ '^[0-9a-f]{64}$'
        AND approval_snapshot_sha256 ~ '^[0-9a-f]{64}$'
      )
);

CREATE INDEX IF NOT EXISTS idx_vkpi_outreach_truth_project
  ON vkpi_action_outreach_truth_bridges(project_id, first_outbound_at);

CREATE OR REPLACE FUNCTION vkpi_action_outreach_truth_bridge_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'action outreach truth bridges are append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_action_outreach_truth_bridge_immutable
  ON vkpi_action_outreach_truth_bridges;
CREATE TRIGGER trg_vkpi_action_outreach_truth_bridge_immutable
BEFORE UPDATE OR DELETE ON vkpi_action_outreach_truth_bridges
FOR EACH ROW EXECUTE FUNCTION vkpi_action_outreach_truth_bridge_reject_mutation();

CREATE OR REPLACE FUNCTION vkpi_action_outreach_truth_event_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.event_type IN ('action_outreach_bound', 'action_outreach_reply_verified')
       OR (
         TG_OP = 'UPDATE'
         AND NEW.event_type IN ('action_outreach_bound', 'action_outreach_reply_verified')
       ) THEN
        RAISE EXCEPTION 'action outreach truth events are append-only';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_action_outreach_truth_event_immutable
  ON vkpi_event_ledger;
CREATE TRIGGER trg_vkpi_action_outreach_truth_event_immutable
BEFORE UPDATE OR DELETE ON vkpi_event_ledger
FOR EACH ROW EXECUTE FUNCTION vkpi_action_outreach_truth_event_reject_mutation();

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_action_outreach_truth_event
ON vkpi_event_ledger(organization_id, entity_type, entity_id, source)
WHERE event_type = 'action_outreach_bound';

CREATE TABLE IF NOT EXISTS vkpi_action_outreach_reply_truth_receipts (
    id                       BIGSERIAL PRIMARY KEY,
    organization_id          BIGINT NOT NULL,
    binding_id               BIGINT NOT NULL
      REFERENCES vkpi_action_outreach_truth_bridges(id) ON DELETE RESTRICT,
    outcome                  TEXT NOT NULL,
    inbound_message_id       BIGINT REFERENCES vkpi_messages(id) ON DELETE RESTRICT,
    inbound_captured_at      TIMESTAMPTZ,
    inbound_created_at       TIMESTAMPTZ,
    first_outbound_at        TIMESTAMPTZ NOT NULL,
    observation_end_at       TIMESTAMPTZ NOT NULL,
    candidate_observed_at    TIMESTAMPTZ NOT NULL,
    verified_at              TIMESTAMPTZ NOT NULL,
    actor_staff_id           BIGINT NOT NULL,
    correlation_id           TEXT NOT NULL,
    request_fingerprint      TEXT NOT NULL,
    binding_fingerprint      TEXT NOT NULL,
    review_candidate_sha256  TEXT NOT NULL,
    review_candidate_json    JSONB NOT NULL,
    receipt_fingerprint      TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_outreach_reply_truth_binding
      UNIQUE (organization_id, binding_id),
    CONSTRAINT uq_vkpi_outreach_reply_truth_correlation
      UNIQUE (organization_id, correlation_id),
    CONSTRAINT chk_vkpi_outreach_reply_truth_org1 CHECK (organization_id = 1),
    CONSTRAINT chk_vkpi_outreach_reply_truth_outcome
      CHECK (outcome IN ('replied', 'no_reply')),
    CONSTRAINT chk_vkpi_outreach_reply_truth_shape CHECK (
      (outcome = 'replied'
       AND inbound_message_id IS NOT NULL
       AND inbound_captured_at IS NOT NULL
       AND inbound_created_at IS NOT NULL
       AND inbound_captured_at > first_outbound_at
       AND inbound_captured_at <= inbound_created_at
       AND inbound_created_at <= observation_end_at
       AND candidate_observed_at >= observation_end_at
       AND candidate_observed_at <= verified_at
       AND inbound_created_at <= verified_at)
      OR
      (outcome = 'no_reply'
       AND inbound_message_id IS NULL
       AND inbound_captured_at IS NULL
       AND inbound_created_at IS NULL
       AND candidate_observed_at >= observation_end_at
       AND candidate_observed_at <= verified_at
       AND verified_at >= observation_end_at)
    ),
    CONSTRAINT chk_vkpi_outreach_reply_truth_correlation
      CHECK (length(btrim(correlation_id)) BETWEEN 8 AND 160),
    CONSTRAINT chk_vkpi_outreach_reply_truth_hash
      CHECK (
        request_fingerprint ~ '^[0-9a-f]{64}$'
        AND binding_fingerprint ~ '^[0-9a-f]{64}$'
        AND review_candidate_sha256 ~ '^[0-9a-f]{64}$'
        AND receipt_fingerprint ~ '^[0-9a-f]{64}$'
      ),
    CONSTRAINT chk_vkpi_outreach_reply_truth_candidate CHECK (
      jsonb_typeof(review_candidate_json) = 'object'
      AND review_candidate_json->>'schema' =
        'vkpi_action_outreach_reply_review_candidate/v1'
      AND octet_length(review_candidate_json::TEXT) <= 65536
    )
);

CREATE OR REPLACE FUNCTION vkpi_action_outreach_reply_truth_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'action outreach reply truth receipts are append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_action_outreach_reply_truth_immutable
  ON vkpi_action_outreach_reply_truth_receipts;
CREATE TRIGGER trg_vkpi_action_outreach_reply_truth_immutable
BEFORE UPDATE OR DELETE ON vkpi_action_outreach_reply_truth_receipts
FOR EACH ROW EXECUTE FUNCTION vkpi_action_outreach_reply_truth_reject_mutation();

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_action_outreach_reply_truth_event
ON vkpi_event_ledger(organization_id, entity_type, entity_id, source)
WHERE event_type = 'action_outreach_reply_verified';

COMMENT ON TABLE vkpi_action_outreach_truth_bridges IS
  'Immutable manager-created proof linking one approved GTM outreach Action to its exact project and first outbound message; client metadata is not evidence.';

COMMENT ON TABLE vkpi_action_outreach_reply_truth_receipts IS
  'Immutable manager verification of one reply or no-reply result after server resolution; unverified message rows never become prediction actuals.';
