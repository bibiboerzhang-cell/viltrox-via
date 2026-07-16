-- 257: Workspace-scoped Dealer/Event candidate staging and manual promotion gate.
--
-- This migration creates no candidate rows and imports no business data.  A
-- registry source, manufacturer directory, public product page or event listing
-- is only a discovery input.  It never proves Viltrox authorization, inventory,
-- participation, attendance, sales, ROI or local impact.
--
-- The migration runner owns the surrounding transaction and advisory lock.

-- A source-registry passport proves the reviewed identity of one discovery
-- publisher.  It is deliberately separate from Dealer/Event entity passports.
ALTER TABLE vkpi_source_passports
  ADD COLUMN IF NOT EXISTS registry_source_id TEXT;

ALTER TABLE vkpi_source_passports
  DROP CONSTRAINT IF EXISTS chk_source_passport_entity_type;
ALTER TABLE vkpi_source_passports
  DROP CONSTRAINT IF EXISTS chk_source_passport_entity_link;
ALTER TABLE vkpi_source_passports
  DROP CONSTRAINT IF EXISTS chk_source_passport_registry_id;

ALTER TABLE vkpi_source_passports
  ADD CONSTRAINT chk_source_passport_entity_type CHECK (
    entity_type IN (
      'dealer_location','event_source','event_opportunity','source_registry'
    )
  );

ALTER TABLE vkpi_source_passports
  ADD CONSTRAINT chk_source_passport_registry_id CHECK (
    registry_source_id IS NULL
    OR registry_source_id ~ '^[a-z0-9][a-z0-9_-]{5,127}$'
  );

ALTER TABLE vkpi_source_passports
  ADD CONSTRAINT chk_source_passport_entity_link CHECK (
    (entity_type = 'dealer_location' AND dealer_id IS NOT NULL
      AND event_source_id IS NULL AND event_opportunity_id IS NULL
      AND registry_source_id IS NULL)
    OR
    (entity_type = 'event_source' AND dealer_id IS NULL
      AND event_source_id IS NOT NULL AND event_opportunity_id IS NULL
      AND registry_source_id IS NULL)
    OR
    (entity_type = 'event_opportunity' AND dealer_id IS NULL
      AND event_source_id IS NULL AND event_opportunity_id IS NOT NULL
      AND registry_source_id IS NULL)
    OR
    (entity_type = 'source_registry' AND dealer_id IS NULL
      AND event_source_id IS NULL AND event_opportunity_id IS NULL
      AND registry_source_id IS NOT NULL)
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_passport_registry_source
  ON vkpi_source_passports(organization_id, registry_source_id)
  WHERE entity_type = 'source_registry';

CREATE TABLE IF NOT EXISTS vkpi_dealer_event_candidates (
    organization_id BIGINT NOT NULL,
    id TEXT NOT NULL,
    candidate_type TEXT NOT NULL,
    source_registry_id TEXT NOT NULL,
    source_entity_key TEXT NOT NULL,
    source_url TEXT NOT NULL,
    stable_org_key TEXT NOT NULL DEFAULT '',
    stable_location_key TEXT NOT NULL DEFAULT '',
    content_sha256 TEXT NOT NULL,
    candidate_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status TEXT NOT NULL DEFAULT 'pending',
    reviewer_staff_id BIGINT REFERENCES staff(id) ON DELETE RESTRICT,
    reviewed_at TIMESTAMPTZ,
    source_passport_id TEXT,
    promotion_gate_status TEXT NOT NULL DEFAULT 'blocked',
    promotion_target_type TEXT NOT NULL DEFAULT '',
    promotion_target_id TEXT NOT NULL DEFAULT '',
    promotion_reviewer_staff_id BIGINT REFERENCES staff(id) ON DELETE RESTRICT,
    promoted_at TIMESTAMPTZ,
    claim_status TEXT NOT NULL DEFAULT 'descriptive_only',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, id),
    CONSTRAINT fk_candidate_source_passport
      FOREIGN KEY (organization_id, source_passport_id)
      REFERENCES vkpi_source_passports(organization_id, id)
      ON DELETE RESTRICT,
    CONSTRAINT chk_candidate_org CHECK (organization_id > 0),
    CONSTRAINT chk_candidate_id CHECK (id ~ '^cand_[0-9a-f]{32}$'),
    CONSTRAINT chk_candidate_type CHECK (
      candidate_type IN ('dealer_location','event_opportunity')
    ),
    CONSTRAINT chk_candidate_source_registry CHECK (
      source_registry_id ~ '^[a-z0-9][a-z0-9_-]{5,127}$'
    ),
    CONSTRAINT chk_candidate_source_entity_key CHECK (
      source_entity_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}$'
    ),
    CONSTRAINT chk_candidate_source_url CHECK (
      source_url LIKE 'https://%' AND length(source_url) <= 2048
    ),
    CONSTRAINT chk_candidate_content_sha CHECK (
      content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT chk_candidate_review_status CHECK (
      review_status IN ('pending','needs_review','approved','rejected')
    ),
    CONSTRAINT chk_candidate_gate_status CHECK (
      promotion_gate_status IN (
        'blocked','eligible_for_manual_promotion','manually_promoted'
      )
    ),
    CONSTRAINT chk_candidate_claim CHECK (claim_status = 'descriptive_only'),
    CONSTRAINT chk_candidate_stable_keys CHECK (
      (
        candidate_type = 'dealer_location'
        AND (
          (stable_org_key = '' AND stable_location_key = '')
          OR (
            stable_org_key ~ '^dealer_org_[a-z0-9]{8,64}$'
            AND stable_location_key ~ '^dealer_loc_[a-z0-9]{8,64}$'
          )
        )
      )
      OR
      (
        candidate_type = 'event_opportunity'
        AND stable_org_key = ''
        AND (
          stable_location_key = ''
          OR stable_location_key ~ '^dealer_loc_[a-z0-9]{8,64}$'
        )
      )
    ),
    CONSTRAINT chk_candidate_approved_review CHECK (
      review_status <> 'approved'
      OR (
        reviewer_staff_id IS NOT NULL
        AND reviewed_at IS NOT NULL
        AND source_passport_id IS NOT NULL
        AND (
          candidate_type <> 'dealer_location'
          OR (stable_org_key <> '' AND stable_location_key <> '')
        )
      )
    ),
    CONSTRAINT chk_candidate_gate_shape CHECK (
      promotion_gate_status = 'blocked'
      OR (
        review_status = 'approved'
        AND reviewer_staff_id IS NOT NULL
        AND reviewed_at IS NOT NULL
        AND source_passport_id IS NOT NULL
      )
    ),
    CONSTRAINT chk_candidate_manual_promotion_receipt CHECK (
      (
        promotion_gate_status <> 'manually_promoted'
        AND promotion_target_type = ''
        AND promotion_target_id = ''
        AND promotion_reviewer_staff_id IS NULL
        AND promoted_at IS NULL
      )
      OR
      (
        promotion_gate_status = 'manually_promoted'
        AND promotion_target_type = candidate_type
        AND promotion_target_id <> ''
        AND promotion_reviewer_staff_id IS NOT NULL
        AND promoted_at IS NOT NULL
      )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_source_identity
  ON vkpi_dealer_event_candidates(
    organization_id, candidate_type, source_registry_id, source_entity_key
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_dealer_location
  ON vkpi_dealer_event_candidates(organization_id, stable_location_key)
  WHERE candidate_type = 'dealer_location'
    AND stable_location_key <> ''
    AND review_status <> 'rejected';

CREATE INDEX IF NOT EXISTS idx_candidate_review_queue
  ON vkpi_dealer_event_candidates(
    organization_id, candidate_type, review_status, updated_at DESC
  );

CREATE INDEX IF NOT EXISTS idx_candidate_promotion_gate
  ON vkpi_dealer_event_candidates(
    organization_id, promotion_gate_status, updated_at DESC
  );

CREATE TABLE IF NOT EXISTS vkpi_candidate_field_evidence_links (
    organization_id BIGINT NOT NULL,
    candidate_id TEXT NOT NULL,
    field_evidence_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    added_by_staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
      organization_id, candidate_id, field_evidence_id, evidence_role
    ),
    CONSTRAINT fk_candidate_evidence_candidate
      FOREIGN KEY (organization_id, candidate_id)
      REFERENCES vkpi_dealer_event_candidates(organization_id, id)
      ON DELETE CASCADE,
    CONSTRAINT fk_candidate_evidence_field
      FOREIGN KEY (organization_id, field_evidence_id)
      REFERENCES vkpi_source_field_evidence(organization_id, id)
      ON DELETE RESTRICT,
    CONSTRAINT chk_candidate_evidence_org CHECK (organization_id > 0),
    CONSTRAINT chk_candidate_evidence_role CHECK (
      evidence_role IN (
        'source_listing','identity','location','activity',
        'product_presence','contact'
      )
    )
);

CREATE INDEX IF NOT EXISTS idx_candidate_evidence_candidate
  ON vkpi_candidate_field_evidence_links(
    organization_id, candidate_id, evidence_role
  );

-- A constraint alone cannot prove that a referenced passport/evidence row is
-- current.  This trigger rechecks current source truth whenever a candidate is
-- moved beyond blocked.  Initial inserts therefore remain blocked; evidence is
-- linked first, and only an explicit later human action may request eligibility.
CREATE OR REPLACE FUNCTION vkpi_validate_candidate_promotion_gate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $candidate_gate$
DECLARE
  passport_row vkpi_source_passports%ROWTYPE;
  required_role TEXT;
  has_current_field_evidence BOOLEAN := FALSE;
  has_promotion_target BOOLEAN := FALSE;
BEGIN
  -- A completed manual-promotion receipt is an append-only audit fact.  Once
  -- emitted, no caller may retarget it, swap its reviewed identity/evidence,
  -- or rewrite the source snapshot while leaving the status promoted.  Only
  -- the mechanical updated_at timestamp may change.
  IF TG_OP = 'UPDATE'
     AND OLD.promotion_gate_status = 'manually_promoted'
     AND (to_jsonb(NEW) - 'updated_at')
         IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at') THEN
    RAISE EXCEPTION 'manual promotion receipt and candidate truth are immutable';
  END IF;

  IF NEW.source_passport_id IS NOT NULL THEN
    SELECT * INTO passport_row
    FROM vkpi_source_passports
    WHERE organization_id = NEW.organization_id
      AND id = NEW.source_passport_id;

    IF NOT FOUND
       OR passport_row.entity_type <> 'source_registry'
       OR passport_row.registry_source_id IS DISTINCT FROM NEW.source_registry_id THEN
      RAISE EXCEPTION 'candidate source passport must match its source registry identity';
    END IF;
  END IF;

  IF NEW.promotion_gate_status = 'blocked' THEN
    RETURN NEW;
  END IF;

  IF NEW.source_passport_id IS NULL
     OR passport_row.verification_status <> 'verified'
     OR passport_row.identity_status <> 'exact'
     OR passport_row.freshness_status_at_write <> 'fresh'
     OR passport_row.verified_at IS NULL
     OR passport_row.verified_at > CURRENT_TIMESTAMP + INTERVAL '5 minutes'
     OR passport_row.verified_at < CURRENT_TIMESTAMP
        - make_interval(days => passport_row.stale_after_days)
     OR passport_row.reviewer_staff_id IS NULL
     OR passport_row.claim_status <> 'descriptive_only' THEN
    RAISE EXCEPTION 'candidate promotion requires a current verified source registry passport';
  END IF;

  required_role := CASE
    WHEN NEW.candidate_type = 'dealer_location' THEN 'location'
    ELSE 'activity'
  END;

  SELECT EXISTS (
    SELECT 1
    FROM vkpi_candidate_field_evidence_links link
    JOIN vkpi_source_field_evidence evidence
      ON evidence.organization_id = link.organization_id
     AND evidence.id = link.field_evidence_id
    WHERE link.organization_id = NEW.organization_id
      AND link.candidate_id = NEW.id
      AND link.evidence_role = required_role
      AND evidence.passport_id = NEW.source_passport_id
      AND evidence.verification_status = 'verified'
      AND evidence.freshness_status_at_write = 'fresh'
      AND evidence.value_status = 'observed'
      AND evidence.verified_at IS NOT NULL
      AND evidence.verified_at <= CURRENT_TIMESTAMP + INTERVAL '5 minutes'
      AND evidence.verified_at >= CURRENT_TIMESTAMP
          - make_interval(days => evidence.stale_after_days)
      AND evidence.reviewer_staff_id IS NOT NULL
      AND evidence.claim_status = 'descriptive_only'
  ) INTO has_current_field_evidence;

  IF NOT has_current_field_evidence THEN
    RAISE EXCEPTION 'candidate promotion requires current verified field evidence';
  END IF;

  IF NEW.promotion_gate_status = 'manually_promoted' THEN
    IF NEW.candidate_type = 'dealer_location' THEN
      IF NEW.promotion_target_id !~ '^[1-9][0-9]*$' THEN
        RAISE EXCEPTION 'Dealer promotion target must be an existing numeric Dealer id';
      END IF;
      SELECT EXISTS (
        SELECT 1
        FROM vkpi_dealers dealer
        JOIN vkpi_dealer_identity_aliases alias
          ON alias.dealer_id = dealer.id
         AND alias.organization_id = NEW.organization_id
         AND alias.stable_location_key = NEW.stable_location_key
         AND alias.verified_at IS NOT NULL
        WHERE dealer.id = NEW.promotion_target_id::BIGINT
      ) INTO has_promotion_target;
    ELSE
      SELECT EXISTS (
        SELECT 1
        FROM vkpi_event_opportunities opportunity
        WHERE opportunity.organization_id = NEW.organization_id
          AND opportunity.id = NEW.promotion_target_id
          AND opportunity.official_url = NEW.source_url
          AND opportunity.verification_status = 'verified'
      ) INTO has_promotion_target;
    END IF;

    IF NOT has_promotion_target THEN
      RAISE EXCEPTION 'manual promotion receipt requires an exact existing business target';
    END IF;
  END IF;

  RETURN NEW;
END
$candidate_gate$;

DROP TRIGGER IF EXISTS trg_vkpi_candidate_promotion_gate
  ON vkpi_dealer_event_candidates;
CREATE TRIGGER trg_vkpi_candidate_promotion_gate
BEFORE INSERT OR UPDATE
ON vkpi_dealer_event_candidates
FOR EACH ROW
EXECUTE FUNCTION vkpi_validate_candidate_promotion_gate();

COMMENT ON TABLE vkpi_dealer_event_candidates IS
  'Workspace-scoped discovery candidates only. Rows default blocked and never prove Viltrox authorization, inventory, participation, sales, ROI, attendance or local impact.';
COMMENT ON COLUMN vkpi_dealer_event_candidates.promotion_gate_status IS
  'Eligibility marker only. This migration exposes no automatic promotion path; business promotion remains a separate explicit human workflow.';
COMMENT ON TABLE vkpi_candidate_field_evidence_links IS
  'Typed links to append-only field evidence. A link is necessary but is revalidated for freshness when the manual promotion gate changes.';
