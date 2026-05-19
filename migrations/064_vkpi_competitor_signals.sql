-- V-KPI P8 competitor signal review store.

CREATE TABLE IF NOT EXISTS vkpi_competitor_signal_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uid TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'previewed',
    source_summary_json TEXT NOT NULL DEFAULT '{}',
    signal_count INTEGER NOT NULL DEFAULT 0,
    committed_by TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    committed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_signal_runs_status
    ON vkpi_competitor_signal_runs (status, created_at);

CREATE TABLE IF NOT EXISTS vkpi_competitor_signals (
    id BIGSERIAL PRIMARY KEY,
    signal_uid TEXT NOT NULL UNIQUE,
    run_id BIGINT REFERENCES vkpi_competitor_signal_runs(id) ON DELETE CASCADE,
    brand TEXT NOT NULL DEFAULT '',
    normalized_brand TEXT NOT NULL DEFAULT '',
    signal_type TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'medium',
    score NUMERIC(8,3) DEFAULT 0,
    product_hints_json TEXT NOT NULL DEFAULT '[]',
    source_table TEXT NOT NULL DEFAULT '',
    source_id BIGINT,
    source_sheet TEXT DEFAULT '',
    source_row INTEGER,
    source_url TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_signals_brand
    ON vkpi_competitor_signals (normalized_brand, signal_type);

CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_signals_source
    ON vkpi_competitor_signals (source_table, source_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_signals_review
    ON vkpi_competitor_signals (review_status, severity, created_at);
