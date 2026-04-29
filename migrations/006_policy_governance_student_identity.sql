ALTER TABLE via_policy_proposals ADD COLUMN IF NOT EXISTS reviewed_by TEXT DEFAULT '';
ALTER TABLE via_policy_proposals ADD COLUMN IF NOT EXISTS review_note TEXT DEFAULT '';
ALTER TABLE via_policy_proposals ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE via_policy_proposals ADD COLUMN IF NOT EXISTS applied_version_key TEXT DEFAULT '';
ALTER TABLE via_policy_proposals ADD COLUMN IF NOT EXISTS applied_by TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS via_policy_versions (
    id BIGSERIAL PRIMARY KEY,
    version_key TEXT NOT NULL UNIQUE,
    policy_key TEXT NOT NULL,
    version_label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'live',
    source_proposal_key TEXT DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    approved_by TEXT DEFAULT '',
    approved_at TIMESTAMPTZ,
    applied_by TEXT DEFAULT '',
    applied_at TIMESTAMPTZ,
    review_note TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS schools (
    id BIGSERIAL PRIMARY KEY,
    school_id TEXT NOT NULL UNIQUE,
    school_code TEXT NOT NULL,
    school_name TEXT NOT NULL,
    school_name_native TEXT DEFAULT '',
    country TEXT DEFAULT '',
    region TEXT DEFAULT '',
    school_type TEXT DEFAULT 'film',
    tier TEXT DEFAULT 'standard',
    partnership_status TEXT DEFAULT 'pilot',
    visual_theme_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS student_qr_codes (
    id BIGSERIAL PRIMARY KEY,
    qr_id TEXT NOT NULL UNIQUE,
    school_id TEXT NOT NULL,
    issued_batch TEXT DEFAULT '',
    display_serial TEXT DEFAULT '',
    claim_token TEXT NOT NULL,
    claim_signature TEXT NOT NULL,
    claim_url TEXT DEFAULT '',
    qr_code_url TEXT DEFAULT '',
    card_image_url TEXT DEFAULT '',
    manifest_url TEXT DEFAULT '',
    status TEXT DEFAULT 'issued',
    roster_mode TEXT DEFAULT 'anonymous',
    bound_user_id BIGINT NOT NULL DEFAULT 0,
    bound_at TIMESTAMPTZ,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    revoked_reason TEXT DEFAULT '',
    prefilled_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS student_verifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    school_id TEXT NOT NULL,
    student_id_code TEXT NOT NULL,
    verification_method TEXT DEFAULT 'qr_scan',
    verification_proof_json TEXT NOT NULL DEFAULT '{}',
    status TEXT DEFAULT 'active',
    commission_rate_override DOUBLE PRECISION NOT NULL DEFAULT 0.10,
    verified_by TEXT DEFAULT 'system_qr',
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, school_id)
);

CREATE TABLE IF NOT EXISTS student_scan_events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    qr_id TEXT DEFAULT '',
    user_id BIGINT NOT NULL DEFAULT 0,
    school_id TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    location TEXT DEFAULT '',
    event_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_via_policy_versions_policy_created ON via_policy_versions(policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_policy_versions_status_policy ON via_policy_versions(status, policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_schools_code_name ON schools(school_code, school_name);
CREATE INDEX IF NOT EXISTS idx_pg_student_qr_school_status ON student_qr_codes(school_id, status, issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_qr_batch_serial ON student_qr_codes(issued_batch, display_serial);
CREATE INDEX IF NOT EXISTS idx_pg_student_qr_user_bound ON student_qr_codes(bound_user_id, bound_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_verifications_user_status ON student_verifications(user_id, status, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_verifications_school_status ON student_verifications(school_id, status, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_scan_events_user_created ON student_scan_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_scan_events_qr_created ON student_scan_events(qr_id, created_at DESC);
