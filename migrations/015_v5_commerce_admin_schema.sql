-- V5 commerce/admin schema for Postgres runtimes.
--
-- This formalizes the SQLite-first V5 commerce tables in the production
-- migration path and adds schema-level idempotency for Shopify order webhooks
-- and payout accruals.

ALTER TABLE platform_ingest_events
    ADD COLUMN IF NOT EXISTS ingested_into_orders_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pg_ingest_shopify_order_event_unique
    ON platform_ingest_events(source_platform, event_type, external_id)
    WHERE source_platform = 'shopify'
      AND entity_type = 'order'
      AND external_id IS NOT NULL
      AND external_id <> '';

CREATE INDEX IF NOT EXISTS idx_pg_ingest_shopify_orders_uningested
    ON platform_ingest_events(occurred_at, id)
    WHERE source_platform = 'shopify'
      AND entity_type = 'order'
      AND ingested_into_orders_at IS NULL;

CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    external_order_id TEXT NOT NULL,
    source_platform TEXT NOT NULL,
    customer_email TEXT,
    customer_country TEXT,
    subtotal_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    items_json TEXT,
    attribution_source TEXT,
    attribution_type TEXT,
    attribution_user_id BIGINT REFERENCES users(id),
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    commission_rate_bps INTEGER DEFAULT 0,
    commission_cents INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'paid',
    placed_at TIMESTAMPTZ NOT NULL,
    webhook_event_ids_json TEXT,
    raw_payload TEXT,
    flagged_reason TEXT,
    flagged_by BIGINT REFERENCES users(id),
    flagged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pg_orders_external_order_unique
    ON orders(external_order_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pg_orders_source_external_unique
    ON orders(source_platform, external_order_id);

CREATE INDEX IF NOT EXISTS idx_pg_orders_attribution
    ON orders(attribution_source, placed_at);

CREATE INDEX IF NOT EXISTS idx_pg_orders_status
    ON orders(status, placed_at);

CREATE INDEX IF NOT EXISTS idx_pg_orders_utm
    ON orders(utm_source, utm_medium);

CREATE INDEX IF NOT EXISTS idx_pg_orders_user
    ON orders(attribution_user_id, placed_at);

CREATE TABLE IF NOT EXISTS payout_cycles (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'upcoming',
    process_date TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    processed_by BIGINT REFERENCES users(id),
    total_approved_cents INTEGER DEFAULT 0,
    total_paid_cents INTEGER DEFAULT 0,
    creator_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payouts (
    id BIGSERIAL PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES payout_cycles(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    amount_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    order_ids_json TEXT,
    gmv_cents INTEGER DEFAULT 0,
    order_count INTEGER DEFAULT 0,
    method TEXT,
    method_details TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    hold_reason TEXT,
    approved_at TIMESTAMPTZ,
    approved_by BIGINT REFERENCES users(id),
    paid_at TIMESTAMPTZ,
    paid_tx_id TEXT,
    failed_at TIMESTAMPTZ,
    failed_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pg_payouts_cycle_user_unique
    ON payouts(cycle_id, user_id);

CREATE INDEX IF NOT EXISTS idx_pg_payouts_cycle
    ON payouts(cycle_id, status);

CREATE INDEX IF NOT EXISTS idx_pg_payouts_user
    ON payouts(user_id, created_at);

CREATE TABLE IF NOT EXISTS attribution_clicks (
    id BIGSERIAL PRIMARY KEY,
    ref_code TEXT NOT NULL,
    ref_type TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    session_id TEXT,
    user_agent TEXT,
    ip_hash TEXT,
    country TEXT,
    landing_path TEXT,
    converted_to_order_id BIGINT REFERENCES orders(id),
    clicked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_clicks_ref
    ON attribution_clicks(ref_code, clicked_at);

CREATE INDEX IF NOT EXISTS idx_pg_clicks_sess
    ON attribution_clicks(session_id);

CREATE TABLE IF NOT EXISTS payout_disputes (
    id BIGSERIAL PRIMARY KEY,
    payout_id BIGINT REFERENCES payouts(id),
    user_id BIGINT REFERENCES users(id),
    reason TEXT NOT NULL,
    evidence_json TEXT,
    status TEXT DEFAULT 'open',
    resolved_by BIGINT REFERENCES users(id),
    resolved_at TIMESTAMPTZ,
    resolution_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
