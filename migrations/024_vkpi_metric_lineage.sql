-- V-KPI Metric Lineage Layer (Phase 6 完成项)
-- 实现"完整系统"文档 §五.3 方案 B：snapshot + source map
-- 让 Dashboard / 周报 PDF 里每个数字都可以反查到当时的 source rows
--
-- 作者：V-KPI v0.4
-- 关联文档：docs/INTEGRATION_GUIDE_CN.md §1 + §6
-- 依赖：023_vkpi_core.sql

-- ===========================================================================
-- vkpi_metric_runs
-- 一次"快照"。每生成一次 dashboard / 周报 / 导出，都新建一行。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_metric_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uid TEXT NOT NULL UNIQUE,                 -- 'mr-20260507-094210-abc12'
    period_start TIMESTAMPTZ NOT NULL,            -- 数据窗口起点
    period_end TIMESTAMPTZ NOT NULL,              -- 数据窗口终点
    scope_type TEXT NOT NULL DEFAULT 'all',       -- all | staff | project | kol | product
    scope_id BIGINT,                              -- 当 scope_type != 'all'
    trigger_source TEXT NOT NULL DEFAULT 'dashboard',  -- dashboard | weekly_report | manual_export | cron
    generated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    definition_version TEXT NOT NULL DEFAULT 'v1',  -- 指标定义版本，见 metric_definitions.py
    status TEXT NOT NULL DEFAULT 'ready',         -- pending | ready | failed
    error_message TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'      -- filters / 备注
);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_runs_period
    ON vkpi_metric_runs(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_runs_scope
    ON vkpi_metric_runs(scope_type, scope_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_runs_trigger
    ON vkpi_metric_runs(trigger_source, generated_at DESC);

-- ===========================================================================
-- vkpi_metric_values
-- 一次 run 里每个指标的"值"。一个 run 通常会产生 6-12 个 value。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_metric_values (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES vkpi_metric_runs(id) ON DELETE CASCADE,
    metric_key TEXT NOT NULL,                     -- 'gmv' | 'cost' | 'roi' | 'new_kol' | ...
    value_numeric NUMERIC(20, 6),                 -- 数值（金额用 cents 整数也写这里）
    value_text TEXT DEFAULT '',                   -- 非数值或展示用
    currency TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',                -- 'cents' | 'count' | 'ratio' | ...
    calculation_json TEXT NOT NULL DEFAULT '{}',  -- 算式：{"formula": "gmv-cost", "inputs": {...}}
    source_count INTEGER NOT NULL DEFAULT 0,      -- 这个值由几行 source rows 汇总
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_values_run
    ON vkpi_metric_values(run_id, metric_key);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_values_metric_time
    ON vkpi_metric_values(metric_key, created_at DESC);

-- ===========================================================================
-- vkpi_metric_sources
-- 一个 metric_value 是由哪些 source rows 算出来的。
-- 这是"下钻 → source rows → 业务实体证据"的根基。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_metric_sources (
    id BIGSERIAL PRIMARY KEY,
    metric_value_id BIGINT NOT NULL REFERENCES vkpi_metric_values(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,                    -- 'sales_attribution' | 'cost_ledger' | 'project' | 'claim' | 'link_click' | 'stage_event'
    source_id BIGINT NOT NULL,                    -- 源表行 id
    contribution_amount NUMERIC(20, 6) DEFAULT 0, -- 这一行贡献多少（cents 或数量）
    contribution_percent NUMERIC(8, 4) DEFAULT 0, -- 占整个 metric value 的百分比
    evidence_type TEXT NOT NULL DEFAULT '',       -- 'shopify_order' | 'amazon_order' | 'cost_invoice' | 'manual'
    evidence_ref TEXT NOT NULL DEFAULT '',        -- 业务方便对账的参照 (Shopify order id 等)
    project_id BIGINT,                            -- 冗余，方便 drilldown 不再 join
    kol_id BIGINT,
    staff_id BIGINT,
    occurred_at TIMESTAMPTZ,                      -- source row 自己的时间
    snapshot_json TEXT NOT NULL DEFAULT '{}',     -- source row 关键字段冻结快照（防源表后续 update 后看不到原状）
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_value
    ON vkpi_metric_sources(metric_value_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_business
    ON vkpi_metric_sources(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_project
    ON vkpi_metric_sources(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_kol
    ON vkpi_metric_sources(kol_id) WHERE kol_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_staff
    ON vkpi_metric_sources(staff_id) WHERE staff_id IS NOT NULL;

-- ===========================================================================
-- 视图：最近一次 dashboard run 的快捷查询入口
-- 给前端 EvidenceDrawer 用：不用每次先查最新 run_id 再查 values
-- ===========================================================================
CREATE OR REPLACE VIEW vkpi_latest_dashboard_metrics AS
SELECT
    v.id AS value_id,
    v.metric_key,
    v.value_numeric,
    v.value_text,
    v.currency,
    v.unit,
    v.calculation_json,
    v.source_count,
    r.id AS run_id,
    r.run_uid,
    r.period_start,
    r.period_end,
    r.scope_type,
    r.scope_id,
    r.generated_at,
    r.definition_version
FROM vkpi_metric_values v
INNER JOIN vkpi_metric_runs r ON r.id = v.run_id
WHERE r.id = (
    SELECT id FROM vkpi_metric_runs
    WHERE trigger_source = 'dashboard' AND status = 'ready'
    ORDER BY generated_at DESC
    LIMIT 1
);
