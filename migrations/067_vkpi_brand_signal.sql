-- V-KPI v2.1 brand and Viltrox signal review store.

CREATE TABLE IF NOT EXISTS vkpi_brand_signal (
    id BIGSERIAL PRIMARY KEY,
    signal_uid TEXT NOT NULL UNIQUE,
    kol_entity_uid TEXT NOT NULL DEFAULT '',
    post_uid TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    source_id BIGINT,
    post_url TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    published_at TIMESTAMPTZ,
    analysis_scope TEXT NOT NULL DEFAULT 'current_year',
    signal_type TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    brand_role TEXT NOT NULL,
    signal_strength TEXT NOT NULL DEFAULT 'medium',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_new BOOLEAN NOT NULL DEFAULT TRUE,
    reviewed_at TIMESTAMPTZ,
    reviewed_by INTEGER,
    action_taken TEXT DEFAULT '',
    UNIQUE(kol_entity_uid, post_uid, signal_type, brand_name)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_brand_signal_new
    ON vkpi_brand_signal(is_new, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_brand_signal_kol
    ON vkpi_brand_signal(kol_entity_uid, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_brand_signal_type
    ON vkpi_brand_signal(signal_type, brand_role, detected_at DESC);

