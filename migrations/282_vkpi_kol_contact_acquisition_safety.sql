-- 282_vkpi_kol_contact_acquisition_safety.sql
-- Additive contact-acquisition safety state. Existing contact rows remain
-- observed until an explicit evidence-backed verifier promotes them.
-- This migration is isolated from creator-fit scoring state.

ALTER TABLE vkpi_kol_pool_contacts
    ADD COLUMN IF NOT EXISTS normalized_value TEXT;
ALTER TABLE vkpi_kol_pool_contacts
    ADD COLUMN IF NOT EXISTS channel TEXT;
ALTER TABLE vkpi_kol_pool_contacts
    ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'observed';
ALTER TABLE vkpi_kol_pool_contacts
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool_contacts
    ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool_contacts
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_vkpi_kol_contact_verification_status'
          AND conrelid = 'vkpi_kol_pool_contacts'::regclass
    ) THEN
        ALTER TABLE vkpi_kol_pool_contacts
            ADD CONSTRAINT chk_vkpi_kol_contact_verification_status
            CHECK (verification_status IN (
                'observed',
                'verified_public_business',
                'stale',
                'invalid',
                'revoked'
            ));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_vkpi_kol_contact_raw_full_not_verified'
          AND conrelid = 'vkpi_kol_pool_contacts'::regclass
    ) THEN
        ALTER TABLE vkpi_kol_pool_contacts
            ADD CONSTRAINT chk_vkpi_kol_contact_raw_full_not_verified
            CHECK (
                verification_status <> 'verified_public_business'
                OR contact_source <> 'raw_full_scan'
            );
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_kol_contact_normalized
    ON vkpi_kol_pool_contacts(kol_pool_id, channel, normalized_value)
    WHERE normalized_value IS NOT NULL AND channel IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_contact_verification
    ON vkpi_kol_pool_contacts(verification_status, channel, kol_pool_id);

CREATE TABLE IF NOT EXISTS vkpi_kol_contact_evidence (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT NOT NULL REFERENCES vkpi_kol_pool_contacts(id) ON DELETE CASCADE,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    source_field TEXT NOT NULL DEFAULT '',
    evidence_fingerprint CHAR(64) NOT NULL,
    confidence NUMERIC(5,4),
    is_public_declared BOOLEAN NOT NULL DEFAULT FALSE,
    consent_basis TEXT NOT NULL DEFAULT 'source_observation',
    consent_at TIMESTAMPTZ,
    provider_run_ref TEXT NOT NULL DEFAULT '',
    observed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_kol_contact_evidence
        UNIQUE(contact_id, evidence_fingerprint),
    CONSTRAINT chk_vkpi_kol_contact_evidence_fingerprint
        CHECK (evidence_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_vkpi_kol_contact_evidence_confidence
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CONSTRAINT chk_vkpi_kol_contact_evidence_consent_basis
        CHECK (consent_basis IN (
            'source_observation',
            'public_scan',
            'legitimate_interest_public_business',
            'manual_entry',
            'creator_opt_in',
            'platform_messaging_consent',
            'existing_business_relationship'
        )),
    CONSTRAINT chk_vkpi_kol_contact_evidence_consent_timestamp
        CHECK (
            consent_basis NOT IN ('creator_opt_in', 'platform_messaging_consent')
            OR consent_at IS NOT NULL
        ),
    CONSTRAINT chk_vkpi_kol_contact_evidence_manual_actor
        CHECK (
            source_type NOT IN ('manual', 'manual_verified_public_business')
            OR observed_by_staff_id IS NOT NULL
        )
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_contact_evidence_pool
    ON vkpi_kol_contact_evidence(kol_pool_id, contact_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_contact_evidence_source
    ON vkpi_kol_contact_evidence(source_type, is_public_declared, confidence);

CREATE TABLE IF NOT EXISTS vkpi_kol_contact_suppressions (
    id BIGSERIAL PRIMARY KEY,
    brand_scope TEXT NOT NULL,
    kol_pool_id BIGINT NOT NULL,
    channel TEXT NOT NULL,
    contact_fingerprint CHAR(64) NOT NULL,
    fingerprint_key_id CHAR(16) NOT NULL,
    reason TEXT NOT NULL,
    source_type TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    suppressed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    suppressed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    released_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_kol_contact_suppression
        UNIQUE(brand_scope, kol_pool_id, channel, contact_fingerprint),
    CONSTRAINT chk_vkpi_kol_contact_suppression_fingerprint
        CHECK (contact_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_vkpi_kol_contact_suppression_key_id
        CHECK (fingerprint_key_id ~ '^[0-9a-f]{16}$'),
    CONSTRAINT chk_vkpi_kol_contact_suppression_reason
        CHECK (reason IN (
            'unsubscribe',
            'manual_block',
            'complaint',
            'hard_bounce',
            'legal_request',
            'invalid_contact',
            'provider_request'
        )),
    CONSTRAINT chk_vkpi_kol_contact_suppression_source
        CHECK (source_type IN ('reply', 'manual', 'compliance', 'bounce', 'provider'))
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_contact_suppression_active
    ON vkpi_kol_contact_suppressions(
        brand_scope, kol_pool_id, channel, contact_fingerprint
    ) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS vkpi_kol_contact_acquisition_queue (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL UNIQUE REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    trigger_source TEXT NOT NULL DEFAULT 'reconcile',
    reason_code TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    contactability_score NUMERIC,
    last_reconciled_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_vkpi_kol_contact_acquisition_status
        CHECK (status IN (
            'pending_l0',
            'ready',
            'needs_public_profile',
            'needs_website',
            'needs_marketplace_or_dm',
            'suppressed',
            'error'
        )),
    CONSTRAINT chk_vkpi_kol_contact_acquisition_attempt_count
        CHECK (attempt_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_contact_acquisition_due
    ON vkpi_kol_contact_acquisition_queue(status, next_attempt_at, id);

COMMENT ON TABLE vkpi_kol_contact_evidence IS
    'Multi-source contact evidence without raw contact text; consent evidence alone remains non-actionable until a dedicated consent-verification lifecycle exists';
COMMENT ON TABLE vkpi_kol_contact_suppressions IS
    'Durable brand and subject contact suppression using keyed fingerprints only';
COMMENT ON TABLE vkpi_kol_contact_acquisition_queue IS
    'PII-free contact acquisition reconciliation state; provider execution is owned elsewhere';
