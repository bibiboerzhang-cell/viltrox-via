-- V-KPI v2.1 rule-only KOL competitor relation summary.
-- This complements 064_vkpi_competitor_signals; it does not replace the
-- signal-review store. One row summarizes one KOL pool item against one brand.

CREATE TABLE IF NOT EXISTS vkpi_competitor_relation (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    kol_entity_uid TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    handle TEXT NOT NULL DEFAULT '',
    display_name TEXT DEFAULT '',
    competitor_brand TEXT NOT NULL,
    collaboration_depth TEXT NOT NULL DEFAULT 'none',
    collaboration_recency_days INTEGER,
    collaboration_count_90d INTEGER NOT NULL DEFAULT 0,
    collaboration_count_total INTEGER NOT NULL DEFAULT 0,
    sentiment TEXT NOT NULL DEFAULT 'neutral',
    risk_score NUMERIC(3,1) NOT NULL DEFAULT 0,
    risk_tier TEXT NOT NULL DEFAULT 'opportunity',
    evidence_post_uids_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    last_evidence_at TIMESTAMPTZ,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(kol_pool_id, competitor_brand)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_relation_kol
    ON vkpi_competitor_relation(kol_pool_id, risk_score DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_relation_risk
    ON vkpi_competitor_relation(risk_tier, risk_score DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_relation_brand
    ON vkpi_competitor_relation(competitor_brand, risk_tier, risk_score DESC);
