-- V-KPI Reconciliation tables (Phase 5 完成项)
-- Implements "完整系统" §9 attribution 模块的 reconciliation queue + adjustments
-- Depends on: 023_vkpi_core.sql

-- ===========================================================================
-- vkpi_attribution_adjustments
-- 每次手动调整归因（adjust amount / change kol / change project）的审计记录
-- attribution 主表保持 append-only，所有变更都通过这张表追踪
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_attribution_adjustments (
    id BIGSERIAL PRIMARY KEY,
    attribution_id BIGINT NOT NULL REFERENCES vkpi_sales_attributions(id) ON DELETE CASCADE,
    adjustment_type TEXT NOT NULL,              -- 'manual_resolve' | 'amount_adjust' | 'reassign' | 'void' | 'refund'
    field_changed TEXT DEFAULT '',              -- 'revenue_cents' | 'project_id' | 'kol_id' | 'staff_id' | 'confidence'
    old_value TEXT DEFAULT '',
    new_value TEXT DEFAULT '',
    evidence_text TEXT NOT NULL DEFAULT '',     -- required for manual_resolve
    evidence_files_json TEXT NOT NULL DEFAULT '[]',  -- list of attachment URLs/refs
    adjusted_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    adjusted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_vkpi_attr_adj_attribution
    ON vkpi_attribution_adjustments(attribution_id, adjusted_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_attr_adj_staff_time
    ON vkpi_attribution_adjustments(adjusted_by_staff_id, adjusted_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_attr_adj_type
    ON vkpi_attribution_adjustments(adjustment_type, adjusted_at DESC);


-- ===========================================================================
-- vkpi_reconciliation_queue
-- 未匹配订单的工作队列。一条 row 代表"等待人工处理"的订单。
-- 处理后 status='resolved' 但保留行（审计需要）。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_reconciliation_queue (
    id BIGSERIAL PRIMARY KEY,
    source_platform TEXT NOT NULL,              -- 'shopify' | 'amazon'
    source_ref TEXT NOT NULL,                   -- Shopify order ID 或 Amazon record ID
    order_id TEXT DEFAULT '',                   -- 业务订单号
    revenue_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    occurred_at TIMESTAMPTZ NOT NULL,
    customer_country TEXT DEFAULT '',
    product_sku TEXT DEFAULT '',
    discount_codes_json TEXT NOT NULL DEFAULT '[]',
    utm_json TEXT NOT NULL DEFAULT '{}',
    landing_referrer TEXT DEFAULT '',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | in_review | resolved | rejected | expired
    priority INTEGER NOT NULL DEFAULT 0,        -- 0=normal, 10=high (按金额 + 时间)
    assigned_to_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ,
    resolved_attribution_id BIGINT REFERENCES vkpi_sales_attributions(id) ON DELETE SET NULL,
    resolved_at TIMESTAMPTZ,
    resolved_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    rejection_reason TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_platform, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_recon_status_priority
    ON vkpi_reconciliation_queue(status, priority DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_recon_assigned
    ON vkpi_reconciliation_queue(assigned_to_staff_id, status)
    WHERE assigned_to_staff_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vkpi_recon_occurred
    ON vkpi_reconciliation_queue(occurred_at DESC);


-- ===========================================================================
-- vkpi_shopify_sync_runs
-- Shopify 订单同步历史日志（Sync Logs 标签页用）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_shopify_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    sync_uid TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'webhook',     -- 'webhook' | 'manual_pull' | 'cron'
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',     -- running | success | partial | failed
    orders_received INTEGER NOT NULL DEFAULT 0,
    orders_matched INTEGER NOT NULL DEFAULT 0,
    orders_unmatched INTEGER NOT NULL DEFAULT 0,
    orders_failed INTEGER NOT NULL DEFAULT 0,
    error_message TEXT DEFAULT '',
    triggered_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_sync_time
    ON vkpi_shopify_sync_runs(started_at DESC);


-- ===========================================================================
-- 视图：reconciliation queue 统计 (前端 4-tab badge counts 用)
-- ===========================================================================
CREATE OR REPLACE VIEW vkpi_reconciliation_stats AS
SELECT
    -- 待处理
    COUNT(*) FILTER (WHERE q.status = 'pending') AS pending_count,
    COUNT(*) FILTER (WHERE q.status = 'in_review') AS in_review_count,
    COUNT(*) FILTER (WHERE q.status = 'resolved') AS resolved_count,
    COUNT(*) FILTER (WHERE q.status = 'rejected') AS rejected_count,
    -- 金额
    COALESCE(SUM(q.revenue_cents) FILTER (WHERE q.status = 'pending'), 0) AS pending_gmv_cents,
    -- 时间窗口
    MIN(q.created_at) FILTER (WHERE q.status = 'pending') AS oldest_pending_at,
    -- 整体
    (SELECT COUNT(*) FROM vkpi_sales_attributions WHERE confidence = 'confirmed') AS attr_confirmed_count,
    (SELECT COUNT(*) FROM vkpi_sales_attributions WHERE confidence = 'estimated') AS attr_estimated_count,
    (SELECT COUNT(*) FROM vkpi_sales_attributions WHERE confidence = 'manual') AS attr_manual_count
FROM vkpi_reconciliation_queue q;
