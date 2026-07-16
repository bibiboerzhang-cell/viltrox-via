-- 243: External Event Radar truth layer.
-- External web opportunities stay separate from vkpi_events until an authorized
-- user explicitly promotes one.  Public dealer listings do not prove Viltrox
-- authorization, stock, ROI, attendance, or local commercial impact.

CREATE TABLE IF NOT EXISTS vkpi_event_watch_targets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    country_code TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    canonical_url TEXT NOT NULL UNIQUE,
    discovery_url TEXT NOT NULL DEFAULT '',
    fetch_mode TEXT NOT NULL DEFAULT 'manual_reviewed',
    parser_profile TEXT NOT NULL DEFAULT 'manual_reviewed_v1',
    evidence_grade TEXT NOT NULL DEFAULT 'A2',
    priority_tier INTEGER NOT NULL DEFAULT 2,
    refresh_policy TEXT NOT NULL DEFAULT 'daily',
    requires_human_review BOOLEAN NOT NULL DEFAULT FALSE,
    terms_robots_status TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'active',
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    last_checked_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    next_check_at TIMESTAMPTZ,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    dealer_id BIGINT REFERENCES vkpi_dealers(id) ON DELETE SET NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_event_watch_grade CHECK (evidence_grade IN ('A1','A2','B','X')),
    CONSTRAINT chk_event_watch_status CHECK (status IN ('active','hold','retired','blocked')),
    CONSTRAINT chk_event_watch_priority CHECK (priority_tier BETWEEN 0 AND 9),
    CONSTRAINT chk_event_watch_failure_count CHECK (failure_count >= 0)
);

CREATE TABLE IF NOT EXISTS vkpi_event_source_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    source_id TEXT REFERENCES vkpi_event_watch_targets(id) ON DELETE SET NULL,
    run_kind TEXT NOT NULL DEFAULT 'reviewed_seed',
    status TEXT NOT NULL DEFAULT 'running',
    record_only BOOLEAN NOT NULL DEFAULT TRUE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    http_status INTEGER,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error_class TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_event_source_run_status CHECK (status IN ('running','succeeded','partial','failed','preview'))
);

CREATE TABLE IF NOT EXISTS vkpi_event_opportunities (
    id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL REFERENCES vkpi_event_watch_targets(id) ON DELETE RESTRICT,
    external_event_key TEXT NOT NULL DEFAULT '',
    lane TEXT NOT NULL,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    organizer TEXT NOT NULL DEFAULT '',
    start_date DATE,
    end_date DATE,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    local_time_text TEXT NOT NULL DEFAULT '',
    date_precision TEXT NOT NULL DEFAULT 'date',
    venue TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    country_code TEXT NOT NULL DEFAULT '',
    is_online BOOLEAN NOT NULL DEFAULT FALSE,
    official_url TEXT NOT NULL,
    registration_url TEXT NOT NULL DEFAULT '',
    event_status TEXT NOT NULL DEFAULT 'scheduled',
    decision_status TEXT NOT NULL DEFAULT 'new',
    evidence_grade TEXT NOT NULL DEFAULT 'A2',
    verification_status TEXT NOT NULL DEFAULT 'needs_review',
    confidence NUMERIC(4,3) NOT NULL DEFAULT 0,
    relevance_score NUMERIC(5,2),
    relevance_basis TEXT NOT NULL DEFAULT '',
    viltrox_presence_status TEXT NOT NULL DEFAULT 'unknown',
    viltrox_evidence_url TEXT NOT NULL DEFAULT '',
    source_checked_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_verified_at TIMESTAMPTZ,
    content_hash TEXT NOT NULL,
    decision_note TEXT NOT NULL DEFAULT '',
    decision_by BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    decision_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_event_radar_lane CHECK (lane IN ('major_expo','dealer_event','local_activity','brand_event')),
    CONSTRAINT chk_event_radar_date_precision CHECK (date_precision IN ('date','date_time','month_only','tbd')),
    CONSTRAINT chk_event_radar_status CHECK (event_status IN ('scheduled','postponed','cancelled','ended','unknown')),
    CONSTRAINT chk_event_radar_decision CHECK (decision_status IN ('new','watching','approved','dismissed','promoted','needs_review')),
    CONSTRAINT chk_event_radar_grade CHECK (evidence_grade IN ('A1','A2','B','X')),
    CONSTRAINT chk_event_radar_verification CHECK (verification_status IN ('verified','provisional','conflict','needs_review')),
    CONSTRAINT chk_event_radar_confidence CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT chk_event_radar_relevance CHECK (relevance_score IS NULL OR relevance_score BETWEEN 0 AND 100),
    CONSTRAINT chk_event_radar_viltrox_presence CHECK (viltrox_presence_status IN ('unknown','not_found','brand_listed','confirmed_exhibitor')),
    CONSTRAINT chk_event_radar_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
    UNIQUE(source_id, external_event_key)
);

CREATE TABLE IF NOT EXISTS vkpi_event_source_observations (
    id BIGSERIAL PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES vkpi_event_watch_targets(id) ON DELETE CASCADE,
    run_id BIGINT REFERENCES vkpi_event_source_runs(id) ON DELETE SET NULL,
    opportunity_id TEXT REFERENCES vkpi_event_opportunities(id) ON DELETE SET NULL,
    external_event_key TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extracted_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_payload_ref TEXT NOT NULL DEFAULT '',
    extractor TEXT NOT NULL DEFAULT 'manual_reviewed_v1',
    UNIQUE(source_id, external_event_key, content_hash)
);

CREATE TABLE IF NOT EXISTS vkpi_event_opportunity_changes (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES vkpi_event_opportunities(id) ON DELETE CASCADE,
    observation_id BIGINT REFERENCES vkpi_event_source_observations(id) ON DELETE SET NULL,
    change_kind TEXT NOT NULL,
    before_hash TEXT NOT NULL DEFAULT '',
    after_hash TEXT NOT NULL,
    changed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_event_opportunity_dealers (
    opportunity_id TEXT NOT NULL REFERENCES vkpi_event_opportunities(id) ON DELETE CASCADE,
    dealer_id BIGINT NOT NULL REFERENCES vkpi_dealers(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'host',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(opportunity_id, dealer_id, relation_type),
    CONSTRAINT chk_event_radar_dealer_relation CHECK (relation_type IN ('host','location','sponsor','nearby'))
);

CREATE TABLE IF NOT EXISTS vkpi_event_opportunity_promotions (
    opportunity_id TEXT PRIMARY KEY REFERENCES vkpi_event_opportunities(id) ON DELETE RESTRICT,
    event_id VARCHAR(64) NOT NULL UNIQUE REFERENCES vkpi_events(id) ON DELETE RESTRICT,
    promoted_by BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    promoted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_event_watch_status_next ON vkpi_event_watch_targets(status, enabled, next_check_at);
CREATE INDEX IF NOT EXISTS idx_event_watch_country_kind ON vkpi_event_watch_targets(country_code, source_kind);
CREATE INDEX IF NOT EXISTS idx_event_radar_date_lane ON vkpi_event_opportunities(start_date, lane);
CREATE INDEX IF NOT EXISTS idx_event_radar_decision_updated ON vkpi_event_opportunities(decision_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_radar_country_date ON vkpi_event_opportunities(country_code, start_date);
CREATE INDEX IF NOT EXISTS idx_event_radar_verified ON vkpi_event_opportunities(verification_status, last_verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_source_runs_started ON vkpi_event_source_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_observations_opportunity ON vkpi_event_source_observations(opportunity_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_changes_opportunity ON vkpi_event_opportunity_changes(opportunity_id, detected_at DESC);

COMMENT ON TABLE vkpi_event_opportunities IS
  'External event opportunities only. Rows are source-backed leads, not approved internal execution and not evidence of ROI, sales, attendance, or Viltrox participation.';
COMMENT ON COLUMN vkpi_event_opportunities.relevance_score IS
  'Descriptive prioritization score with relevance_basis; never GMV, ROI, conversion probability, or proven local impact.';
COMMENT ON COLUMN vkpi_event_opportunities.viltrox_presence_status IS
  'Separate evidence state. A Dealer/product listing never implies authorization, current stock, or confirmed event participation.';
