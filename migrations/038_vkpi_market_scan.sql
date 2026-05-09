-- V-KPI market scan skeletons.
CREATE TABLE IF NOT EXISTS vkpi_market_scan_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uid TEXT NOT NULL UNIQUE,
    launch_id BIGINT REFERENCES vkpi_product_launches(id) ON DELETE SET NULL,
    scan_type TEXT NOT NULL DEFAULT 'manual',
    platforms_json TEXT NOT NULL DEFAULT '[]',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    provider_status TEXT NOT NULL DEFAULT 'not_configured',
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT DEFAULT '',
    triggered_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vkpi_market_scan_runs_launch
    ON vkpi_market_scan_runs(launch_id, triggered_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_market_sources (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES vkpi_market_scan_runs(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    platform TEXT DEFAULT '',
    source_ref TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_market_mentions (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES vkpi_market_scan_runs(id) ON DELETE CASCADE,
    source_id BIGINT REFERENCES vkpi_market_sources(id) ON DELETE SET NULL,
    platform TEXT DEFAULT '',
    handle TEXT DEFAULT '',
    mention_text TEXT DEFAULT '',
    product_sku TEXT DEFAULT '',
    competitor_product TEXT DEFAULT '',
    sentiment TEXT DEFAULT '',
    score NUMERIC(8,3),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_competitor_products (
    id BIGSERIAL PRIMARY KEY,
    launch_id BIGINT REFERENCES vkpi_product_launches(id) ON DELETE SET NULL,
    brand TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    sku TEXT DEFAULT '',
    category TEXT DEFAULT '',
    price_cents INTEGER DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_competitor_content (
    id BIGSERIAL PRIMARY KEY,
    competitor_product_id BIGINT REFERENCES vkpi_competitor_products(id) ON DELETE CASCADE,
    platform TEXT DEFAULT '',
    post_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    views BIGINT,
    likes BIGINT,
    comments BIGINT,
    published_at TIMESTAMPTZ,
    raw_platform_data TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
