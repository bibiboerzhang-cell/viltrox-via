-- V-KPI P5 selected tables: Campaigns, Budget Pool, Offboarding.
-- Adapted to current cost status model: vkpi_cost_ledger.status is actual/void.

CREATE TABLE IF NOT EXISTS vkpi_campaigns (
    id BIGSERIAL PRIMARY KEY,
    campaign_uid TEXT NOT NULL UNIQUE,
    campaign_name TEXT NOT NULL,
    owner_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    platform TEXT DEFAULT '',
    product_sku TEXT DEFAULT '',
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    goal_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_campaigns_status ON vkpi_campaigns(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_campaigns_owner ON vkpi_campaigns(owner_staff_id, status);

CREATE TABLE IF NOT EXISTS vkpi_campaign_projects (
    campaign_id BIGINT NOT NULL REFERENCES vkpi_campaigns(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    added_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_campaign_projects_project ON vkpi_campaign_projects(project_id);

CREATE TABLE IF NOT EXISTS vkpi_budget_pools (
    id BIGSERIAL PRIMARY KEY,
    pool_uid TEXT NOT NULL UNIQUE,
    pool_name TEXT NOT NULL,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    total_budget_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    owner_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_budget_pools_status ON vkpi_budget_pools(status, period_start, period_end);

CREATE TABLE IF NOT EXISTS vkpi_budget_allocations (
    id BIGSERIAL PRIMARY KEY,
    budget_pool_id BIGINT NOT NULL REFERENCES vkpi_budget_pools(id) ON DELETE CASCADE,
    campaign_id BIGINT REFERENCES vkpi_campaigns(id) ON DELETE SET NULL,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE SET NULL,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    allocated_cents BIGINT NOT NULL DEFAULT 0,
    note TEXT DEFAULT '',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_budget_alloc_pool ON vkpi_budget_allocations(budget_pool_id, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_offboarding_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uid TEXT NOT NULL UNIQUE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    initiated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    new_owner_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    active_claims_count INTEGER NOT NULL DEFAULT 0,
    active_projects_count INTEGER NOT NULL DEFAULT 0,
    channels_count INTEGER NOT NULL DEFAULT 0,
    actual_costs_count INTEGER NOT NULL DEFAULT 0,
    actions_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vkpi_offboarding_staff ON vkpi_offboarding_runs(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_offboarding_status ON vkpi_offboarding_runs(status, created_at DESC);
