-- V-KPI recommendation runs and feedback.
CREATE TABLE IF NOT EXISTS vkpi_kol_recommendation_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uid TEXT NOT NULL UNIQUE,
    launch_id BIGINT REFERENCES vkpi_product_launches(id) ON DELETE SET NULL,
    strategy_version TEXT NOT NULL DEFAULT 'rule_v0',
    status TEXT NOT NULL DEFAULT 'completed',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    recommendation_count INTEGER NOT NULL DEFAULT 0,
    filters_json TEXT NOT NULL DEFAULT '{}',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS vkpi_kol_recommendations (
    id BIGSERIAL PRIMARY KEY,
    recommendation_uid TEXT NOT NULL UNIQUE,
    run_id BIGINT NOT NULL REFERENCES vkpi_kol_recommendation_runs(id) ON DELETE CASCADE,
    launch_id BIGINT REFERENCES vkpi_product_launches(id) ON DELETE SET NULL,
    kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
    linked_main_kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    platform TEXT DEFAULT '',
    handle TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    score NUMERIC(8,3),
    rank INTEGER,
    status TEXT NOT NULL DEFAULT 'recommended',
    feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
    scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
    explanation_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_recommendations_run_rank ON vkpi_kol_recommendations(run_id, rank);

CREATE TABLE IF NOT EXISTS vkpi_recommendation_explanations (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES vkpi_kol_recommendations(id) ON DELETE CASCADE,
    explanation_type TEXT NOT NULL DEFAULT 'rule',
    explanation_text TEXT DEFAULT '',
    strengths_json TEXT NOT NULL DEFAULT '[]',
    concerns_json TEXT NOT NULL DEFAULT '[]',
    model_version TEXT DEFAULT 'rule_v0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_recommendation_feedback (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES vkpi_kol_recommendations(id) ON DELETE CASCADE,
    feedback_type TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
