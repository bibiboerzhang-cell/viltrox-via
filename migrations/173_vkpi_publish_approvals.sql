-- 173(#18 发布审批):7 天日历内容条目的审批状态机(按 source_table+source_id 唯一锁定真来源)。
-- 来源:GET /dashboard/recent-content 的 source_table(vkpi_employee_channel_metrics / viltrox_matrix_scan_posts
-- / vkpi_brand_signal)+ source_id;PublishPreviewModal 三按钮(审批通过/编辑时间/提醒 KOL)写本表。
-- 红线(ADDITIVE 隔离表):不改任何既有表;绝不触 viltrox_fit_score / rule_v0。占位符 '?'(方言层翻译)。
-- 回滚见 173_vkpi_publish_approvals_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_publish_approvals (
    id                   BIGSERIAL   PRIMARY KEY,
    source_table         TEXT        NOT NULL,
    source_id            TEXT        NOT NULL,
    platform             TEXT        DEFAULT '',
    account_handle       TEXT        DEFAULT '',
    title                TEXT        DEFAULT '',
    status               TEXT        NOT NULL DEFAULT 'pending',   -- pending | approved | scheduled
    scheduled_publish_at TIMESTAMPTZ,
    note                 TEXT        DEFAULT '',
    approved_by          BIGINT      REFERENCES staff(id) ON DELETE SET NULL,
    approved_at          TIMESTAMPTZ,
    reminder_sent_at     TIMESTAMPTZ,
    reminder_by          BIGINT      REFERENCES staff(id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_table, source_id)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_publish_approvals_status ON vkpi_publish_approvals(status);

COMMENT ON TABLE vkpi_publish_approvals IS '发布审批状态机(按 source_table+source_id 唯一;审批通过/改期/提醒 KOL)';
