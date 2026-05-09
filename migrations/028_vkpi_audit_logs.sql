-- V-KPI Audit Logs (合规模块)
-- Implements "完整系统" §13 audit + GDPR 兼容 + 财务对账可追溯
-- Depends on: 023_vkpi_core.sql

-- ===========================================================================
-- vkpi_sensitive_access_logs
-- 6 类敏感动作的访问日志
--   1. view_kol_contact     看 KOL 联系方式（email / phone / agent）
--   2. view_financial       进入 Cost / Reports 页
--   3. view_message_full    展开消息全文
--   4. view_kpi_detail      查别人的 KPI 数字
--   5. view_export_history  看导出历史
--   6. view_audit_log       看审计日志（meta-audit）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_sensitive_access_logs (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL,                  -- 6 types above
    resource_type TEXT NOT NULL,                -- 'kol' | 'project' | 'staff' | 'cost' | 'message' | 'page'
    resource_id TEXT DEFAULT '',                -- 业务实体 ID
    page_path TEXT DEFAULT '',                  -- 触发的 URL path
    ip TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_sensitive_logs_staff_time
    ON vkpi_sensitive_access_logs(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_sensitive_logs_action_time
    ON vkpi_sensitive_access_logs(action_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_sensitive_logs_resource
    ON vkpi_sensitive_access_logs(resource_type, resource_id);


-- ===========================================================================
-- vkpi_export_logs
-- 3 类导出动作（PDF / CSV / XLSX）的完整记录
-- 与 vkpi_export_jobs 区别：jobs 是任务管理，logs 是合规审计
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_export_logs (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    export_kind TEXT NOT NULL,                  -- 'pdf_report' | 'csv' | 'xlsx'
    export_target TEXT NOT NULL DEFAULT '',     -- 'weekly_report' | 'attribution' | 'cost' | 'kol' | ...
    metric_keys_json TEXT NOT NULL DEFAULT '[]',  -- 导出含哪些 metric
    filters_json TEXT NOT NULL DEFAULT '{}',    -- 时间窗口 / staff / project 等
    row_count INTEGER NOT NULL DEFAULT 0,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    file_sha256 TEXT DEFAULT '',
    download_url TEXT DEFAULT '',
    contains_pii BOOLEAN NOT NULL DEFAULT FALSE,
    contains_financial BOOLEAN NOT NULL DEFAULT FALSE,
    purpose TEXT DEFAULT '',                    -- 用户可自由填写理由
    ip TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_export_logs_staff_time
    ON vkpi_export_logs(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_export_logs_kind_time
    ON vkpi_export_logs(export_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_export_logs_target
    ON vkpi_export_logs(export_target, created_at DESC);


-- ===========================================================================
-- vkpi_settings_change_logs
-- 5 类设置变更
--   1. modify_kpi_definition   改 KPI 计算口径
--   2. modify_attribution_rule 改归因规则
--   3. modify_api_key          改 API key（Shopify/Apify/AI Provider）
--   4. modify_user_role        改员工角色 / scope
--   5. modify_link_allowlist   改短链允许列表
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_settings_change_logs (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    change_type TEXT NOT NULL,                  -- 5 types above
    setting_key TEXT NOT NULL,
    old_value_redacted TEXT DEFAULT '',         -- API key 等敏感值要 redact
    new_value_redacted TEXT DEFAULT '',
    full_value_hash TEXT DEFAULT '',            -- SHA256 of full new value (for verifying)
    ip TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_settings_logs_staff_time
    ON vkpi_settings_change_logs(staff_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_settings_logs_type_time
    ON vkpi_settings_change_logs(change_type, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_settings_logs_key
    ON vkpi_settings_change_logs(setting_key, changed_at DESC);


-- ===========================================================================
-- 视图：unified audit (跨 4 个表 + stage events)
-- 给前端 Audit Page 用，可按时间 + 员工 + 动作类型筛选
-- ===========================================================================
CREATE OR REPLACE VIEW vkpi_unified_audit AS
SELECT
    'sensitive_access' AS event_category,
    s.id AS event_id,
    s.staff_id,
    s.action_type AS action,
    s.resource_type AS target_type,
    s.resource_id AS target_id,
    s.page_path AS detail,
    s.ip,
    s.user_agent,
    s.created_at AS occurred_at,
    s.metadata_json
FROM vkpi_sensitive_access_logs s

UNION ALL

SELECT
    'export' AS event_category,
    e.id AS event_id,
    e.staff_id,
    'export_'||e.export_kind AS action,
    'export' AS target_type,
    e.export_target AS target_id,
    'rows='||e.row_count||' size='||e.file_size_bytes AS detail,
    e.ip,
    e.user_agent,
    e.created_at AS occurred_at,
    e.filters_json AS metadata_json
FROM vkpi_export_logs e

UNION ALL

SELECT
    'settings_change' AS event_category,
    c.id AS event_id,
    c.staff_id,
    c.change_type AS action,
    'setting' AS target_type,
    c.setting_key AS target_id,
    'old='||COALESCE(c.old_value_redacted,'')||' → new='||COALESCE(c.new_value_redacted,'') AS detail,
    c.ip,
    c.user_agent,
    c.changed_at AS occurred_at,
    c.metadata_json
FROM vkpi_settings_change_logs c

UNION ALL

SELECT
    'attribution_adjust' AS event_category,
    a.id AS event_id,
    a.adjusted_by_staff_id AS staff_id,
    a.adjustment_type AS action,
    'attribution' AS target_type,
    CAST(a.attribution_id AS TEXT) AS target_id,
    a.field_changed||': '||a.old_value||' → '||a.new_value AS detail,
    a.ip,
    '' AS user_agent,
    a.adjusted_at AS occurred_at,
    a.metadata_json
FROM vkpi_attribution_adjustments a

ORDER BY occurred_at DESC;
