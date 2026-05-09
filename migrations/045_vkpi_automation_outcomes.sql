-- V-KPI automation prebuilds: outcomes, experiments, LLM calls, model registry.
CREATE TABLE IF NOT EXISTS vkpi_recommendation_outcomes (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT REFERENCES vkpi_kol_recommendations(id) ON DELETE SET NULL,
    kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
    launch_id BIGINT REFERENCES vkpi_product_launches(id) ON DELETE SET NULL,
    was_shortlisted BOOLEAN NOT NULL DEFAULT FALSE,
    shortlisted_at TIMESTAMPTZ,
    was_rejected BOOLEAN NOT NULL DEFAULT FALSE,
    rejected_at TIMESTAMPTZ,
    reject_reason TEXT DEFAULT '',
    was_claimed BOOLEAN NOT NULL DEFAULT FALSE,
    claimed_at TIMESTAMPTZ,
    project_created BOOLEAN NOT NULL DEFAULT FALSE,
    project_created_at TIMESTAMPTZ,
    outreach_sent BOOLEAN NOT NULL DEFAULT FALSE,
    outreach_sent_at TIMESTAMPTZ,
    reply_received BOOLEAN NOT NULL DEFAULT FALSE,
    reply_at TIMESTAMPTZ,
    reply_sentiment TEXT DEFAULT '',
    agreement_reached BOOLEAN NOT NULL DEFAULT FALSE,
    agreement_at TIMESTAMPTZ,
    content_published BOOLEAN NOT NULL DEFAULT FALSE,
    content_published_at TIMESTAMPTZ,
    content_url TEXT DEFAULT '',
    order_attributed BOOLEAN NOT NULL DEFAULT FALSE,
    first_order_at TIMESTAMPTZ,
    attributed_clicks INTEGER NOT NULL DEFAULT 0,
    attributed_orders INTEGER NOT NULL DEFAULT 0,
    attributed_gmv_cents BIGINT NOT NULL DEFAULT 0,
    attributed_cost_cents BIGINT NOT NULL DEFAULT 0,
    computed_roi NUMERIC(12,5),
    recommended_at TIMESTAMPTZ NOT NULL,
    first_action_at TIMESTAMPTZ,
    outcome_finalized_at TIMESTAMPTZ,
    feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
    scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
    model_version TEXT DEFAULT 'rule_v0',
    display_position INTEGER,
    display_context_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vkpi_scoring_experiments (
    id BIGSERIAL PRIMARY KEY,
    experiment_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    variant_a_strategy TEXT NOT NULL DEFAULT 'rule_v0',
    variant_b_strategy TEXT NOT NULL DEFAULT 'rule_v0',
    traffic_split NUMERIC(5,4) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    start_at TIMESTAMPTZ,
    end_at TIMESTAMPTZ,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_recommendation_assignments (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES vkpi_kol_recommendations(id) ON DELETE CASCADE UNIQUE,
    experiment_id BIGINT REFERENCES vkpi_scoring_experiments(id) ON DELETE SET NULL,
    variant TEXT NOT NULL DEFAULT 'a',
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_llm_calls (
    id BIGSERIAL PRIMARY KEY,
    call_uid TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL,
    prompt_hash TEXT DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents BIGINT NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'not_configured',
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vkpi_model_registry (
    id BIGSERIAL PRIMARY KEY,
    model_version TEXT NOT NULL UNIQUE,
    model_type TEXT NOT NULL DEFAULT 'rule',
    status TEXT NOT NULL DEFAULT 'registered',
    activated_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO vkpi_model_registry (model_version, model_type, status, metadata_json)
VALUES ('rule_v0', 'rule', 'active', '{}')
ON CONFLICT(model_version) DO NOTHING;
