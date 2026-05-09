-- V-KPI project evidence assets.
-- Stores real message captures, published content, cooperation terms, samples,
-- and shipments. Empty UI states must be used when these tables have no rows.

CREATE TABLE IF NOT EXISTS vkpi_messages (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    staff_id BIGINT,
    source TEXT NOT NULL DEFAULT 'manual',
    direction TEXT NOT NULL DEFAULT 'outbound',
    sender TEXT DEFAULT '',
    receiver TEXT DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    snippet TEXT DEFAULT '',
    evidence_url TEXT DEFAULT '',
    follow_up_due_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_messages_project
    ON vkpi_messages(project_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_message_attachments (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES vkpi_messages(id) ON DELETE CASCADE,
    file_url TEXT NOT NULL,
    file_type TEXT DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_content_posts (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    link_id BIGINT REFERENCES vkpi_links(id) ON DELETE SET NULL,
    platform TEXT NOT NULL DEFAULT '',
    post_url TEXT NOT NULL,
    title TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    published_at TIMESTAMPTZ,
    content_type TEXT DEFAULT '',
    views BIGINT NOT NULL DEFAULT 0,
    likes BIGINT NOT NULL DEFAULT 0,
    comments BIGINT NOT NULL DEFAULT 0,
    shares BIGINT NOT NULL DEFAULT 0,
    rights_status TEXT DEFAULT 'unknown',
    ad_usage_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, post_url)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_content_posts_project
    ON vkpi_content_posts(project_id, published_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_content_assets (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT REFERENCES vkpi_content_posts(id) ON DELETE CASCADE,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    asset_url TEXT NOT NULL,
    asset_type TEXT DEFAULT '',
    usage_rights TEXT DEFAULT 'unknown',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_project_terms (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL UNIQUE REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    cash_fee_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    sample_terms TEXT DEFAULT '',
    deliverables_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    usage_rights TEXT DEFAULT '',
    due_at TIMESTAMPTZ,
    note TEXT DEFAULT '',
    created_by_staff_id BIGINT,
    updated_by_staff_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_project_deliverables (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    deliverable_type TEXT NOT NULL DEFAULT 'video',
    quantity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'planned',
    due_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    evidence_url TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_sample_assets (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    product_sku TEXT DEFAULT '',
    product_name TEXT DEFAULT '',
    serial_number TEXT DEFAULT '',
    sample_cost_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    return_required BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'planned',
    shipped_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    returned_at TIMESTAMPTZ,
    note TEXT DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_sample_assets_project
    ON vkpi_sample_assets(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_shipments (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    sample_asset_id BIGINT REFERENCES vkpi_sample_assets(id) ON DELETE SET NULL,
    carrier TEXT DEFAULT '',
    tracking_number TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    shipping_cost_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    shipped_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    evidence_url TEXT DEFAULT '',
    note TEXT DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_shipments_project
    ON vkpi_shipments(project_id, created_at DESC);
