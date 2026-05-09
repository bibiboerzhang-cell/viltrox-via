-- V-KPI core architecture.
-- This layer turns the reused V-OS backend into an internal marketing KPI
-- system with one auditable path: staff -> KOL -> project -> link -> click
-- -> sales -> cost -> KPI.

CREATE TABLE IF NOT EXISTS vkpi_projects (
    id BIGSERIAL PRIMARY KEY,
    project_uid TEXT NOT NULL UNIQUE,
    project_name TEXT NOT NULL,
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    assigned_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    product_sku TEXT DEFAULT '',
    product_name TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    marketplace TEXT DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'discovery',
    stage_status TEXT NOT NULL DEFAULT 'active',
    priority TEXT NOT NULL DEFAULT 'normal',
    source_type TEXT DEFAULT 'manual',
    shopify_discount_code TEXT DEFAULT '',
    shopify_link TEXT DEFAULT '',
    amazon_asin TEXT DEFAULT '',
    amazon_attribution_link TEXT DEFAULT '',
    amazon_associates_link TEXT DEFAULT '',
    sample_status TEXT DEFAULT 'not_required',
    tracking_number TEXT DEFAULT '',
    target_post_date TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_projects_stage ON vkpi_projects(stage, stage_status);
CREATE INDEX IF NOT EXISTS idx_vkpi_projects_staff ON vkpi_projects(assigned_staff_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_projects_kol ON vkpi_projects(kol_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_project_stage_events (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    from_stage TEXT DEFAULT '',
    to_stage TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'stage_change',
    actor_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    note TEXT DEFAULT '',
    source_ref_type TEXT DEFAULT '',
    source_ref_id TEXT DEFAULT '',
    effective_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_stage_events_project ON vkpi_project_stage_events(project_id, effective_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_stage_events_staff ON vkpi_project_stage_events(actor_staff_id, effective_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_kol_claims (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    last_effective_touch_at TIMESTAMPTZ,
    release_reason TEXT DEFAULT '',
    released_at TIMESTAMPTZ,
    released_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vkpi_kol_claims_one_active
    ON vkpi_kol_claims(kol_id)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_claims_staff ON vkpi_kol_claims(staff_id, status, expires_at);

CREATE TABLE IF NOT EXISTS vkpi_links (
    id BIGSERIAL PRIMARY KEY,
    link_uid TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    link_type TEXT NOT NULL DEFAULT 'generic',
    destination_url TEXT NOT NULL,
    fallback_url TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    product_sku TEXT DEFAULT '',
    campaign_name TEXT DEFAULT '',
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE SET NULL,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'live',
    redirect_mode INTEGER NOT NULL DEFAULT 302,
    allowlist_status TEXT NOT NULL DEFAULT 'allowed',
    bot_filter_mode TEXT NOT NULL DEFAULT 'standard',
    utm_source TEXT DEFAULT '',
    utm_medium TEXT DEFAULT '',
    utm_campaign TEXT DEFAULT '',
    utm_content TEXT DEFAULT '',
    starts_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    health_status TEXT NOT NULL DEFAULT 'unknown',
    last_health_checked_at TIMESTAMPTZ,
    click_count BIGINT NOT NULL DEFAULT 0,
    valid_click_count BIGINT NOT NULL DEFAULT 0,
    bot_click_count BIGINT NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_links_project ON vkpi_links(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_links_kol ON vkpi_links(kol_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_links_staff ON vkpi_links(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_links_status ON vkpi_links(status, platform);

CREATE TABLE IF NOT EXISTS vkpi_link_destinations (
    id BIGSERIAL PRIMARY KEY,
    link_id BIGINT NOT NULL REFERENCES vkpi_links(id) ON DELETE CASCADE,
    country_code TEXT DEFAULT '',
    device_type TEXT DEFAULT '',
    weight INTEGER NOT NULL DEFAULT 100,
    destination_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_link_destinations_link ON vkpi_link_destinations(link_id, status);

CREATE TABLE IF NOT EXISTS vkpi_link_clicks (
    id BIGSERIAL PRIMARY KEY,
    link_id BIGINT NOT NULL REFERENCES vkpi_links(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL UNIQUE,
    clicked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_hash TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    referrer TEXT DEFAULT '',
    country_code TEXT DEFAULT '',
    device_type TEXT DEFAULT '',
    bot_score INTEGER NOT NULL DEFAULT 0,
    is_bot INTEGER NOT NULL DEFAULT 0,
    is_unique INTEGER NOT NULL DEFAULT 0,
    session_id TEXT DEFAULT '',
    destination_url TEXT NOT NULL,
    order_id BIGINT REFERENCES orders(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_vkpi_clicks_link_time ON vkpi_link_clicks(link_id, clicked_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_clicks_session ON vkpi_link_clicks(session_id, clicked_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_clicks_order ON vkpi_link_clicks(order_id);

CREATE TABLE IF NOT EXISTS vkpi_sales_attributions (
    id BIGSERIAL PRIMARY KEY,
    source_platform TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE SET NULL,
    link_id BIGINT REFERENCES vkpi_links(id) ON DELETE SET NULL,
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    product_sku TEXT DEFAULT '',
    order_id BIGINT REFERENCES orders(id) ON DELETE SET NULL,
    amazon_campaign_id TEXT DEFAULT '',
    revenue_cents BIGINT NOT NULL DEFAULT 0,
    commission_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    attribution_model TEXT NOT NULL DEFAULT 'last_touch',
    confidence TEXT NOT NULL DEFAULT 'confirmed',
    occurred_at TIMESTAMPTZ,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_platform, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_sales_project ON vkpi_sales_attributions(project_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_sales_staff ON vkpi_sales_attributions(staff_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_sales_kol ON vkpi_sales_attributions(kol_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_cost_ledger (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE SET NULL,
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    cost_type TEXT NOT NULL,
    amount_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'actual',
    incurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_ref TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_cost_project ON vkpi_cost_ledger(project_id, incurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_cost_staff ON vkpi_cost_ledger(staff_id, incurred_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_kpi_ledger (
    id BIGSERIAL PRIMARY KEY,
    ledger_date DATE NOT NULL,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE SET NULL,
    metric_key TEXT NOT NULL,
    metric_value NUMERIC(18,4) NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'system',
    source_ref TEXT DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'confirmed',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kpi_staff_date ON vkpi_kpi_ledger(staff_id, ledger_date DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_kpi_project_date ON vkpi_kpi_ledger(project_id, ledger_date DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_kpi_metric_date ON vkpi_kpi_ledger(metric_key, ledger_date DESC);

CREATE TABLE IF NOT EXISTS vkpi_alerts (
    id BIGSERIAL PRIMARY KEY,
    alert_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL DEFAULT 'info',
    status TEXT NOT NULL DEFAULT 'open',
    target_type TEXT NOT NULL DEFAULT '',
    target_id BIGINT,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    rule_key TEXT DEFAULT '',
    due_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_alerts_status ON vkpi_alerts(status, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_alerts_staff ON vkpi_alerts(staff_id, status, due_at);

CREATE TABLE IF NOT EXISTS vkpi_decision_snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    window_key TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(snapshot_date, window_key)
);
