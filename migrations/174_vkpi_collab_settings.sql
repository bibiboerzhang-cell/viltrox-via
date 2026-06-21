-- 174(#24 协作设置):Project/Event 分享的「共同目标 + 提醒规则」per-resource 元数据。
-- ShareModal「协作设置」原为 disabled「后续接入」;本表让两个输入可存可读(kind+target_id 唯一)。
-- per-resource 语义:一个 project/event 一组协作设置(非 per-member);GET/PATCH /collab-settings 读写。
-- 红线(ADDITIVE 隔离表):不碰 vkpi_projects / vkpi_events / 成员表;绝不触 viltrox_fit_score / rule_v0。
-- 回滚见 174_vkpi_collab_settings_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_collab_settings (
    id            BIGSERIAL   PRIMARY KEY,
    kind          TEXT        NOT NULL,            -- 'project' | 'event'
    target_id     TEXT        NOT NULL,            -- project_id / event_id
    shared_goal   TEXT        DEFAULT '',
    reminder_rule TEXT        DEFAULT '',
    updated_by    BIGINT      REFERENCES staff(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kind, target_id)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_collab_settings_target ON vkpi_collab_settings(kind, target_id);

COMMENT ON TABLE vkpi_collab_settings IS 'Project/Event 分享协作设置(共同目标+提醒规则;per-resource,kind+target_id 唯一)';
