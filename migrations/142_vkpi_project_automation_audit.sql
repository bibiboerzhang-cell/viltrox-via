-- 142(W2 · Projects 履约自动化审计)。Action 执行层 + Projects 履约闭环的「黑匣子」:
-- 把"哪单同步 / 哪天开窗 / 扫了谁 / 命中啥 / 为何进复盘"逐条落档,供 timeline / 审计读。
--
-- 红线(写死在表结构里):
--   1. touches_v6_fit 列 + CHECK(touches_v6_fit=FALSE):自动化审计永远不碰 viltrox_fit_score。
--   2. additive only、幂等(IF NOT EXISTS);与 KOL Pool / 评分域 / rule_v0 物理隔离。
--   3. 纯审计落档:本表只「记录已发生的履约自动化动作」,不驱动任何业务写或裁决。
-- 回滚见 142_vkpi_project_automation_audit_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_project_automation_audit (
    id                BIGSERIAL PRIMARY KEY,
    project_id        BIGINT NOT NULL,                 -- 软引用(与既有履约表同款,无 FK)
    action            TEXT NOT NULL,                   -- shipment_sync|window_open|content_scan|content_match|retrospective_enqueue
    shipment_id       BIGINT,                          -- NULL 允许
    window_id         BIGINT,                          -- NULL 允许
    scanned_kol_count INT NOT NULL DEFAULT 0,
    matched_count     INT NOT NULL DEFAULT 0,
    reason            TEXT NOT NULL DEFAULT '',
    detail_json       JSONB NOT NULL DEFAULT '{}',
    touches_v6_fit    BOOLEAN NOT NULL DEFAULT FALSE,  -- 红线1
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_proj_automation_audit_no_v6_fit CHECK (touches_v6_fit = FALSE),
    CONSTRAINT chk_proj_automation_audit_action
        CHECK (action IN ('shipment_sync','window_open','content_scan','content_match','retrospective_enqueue'))
);
CREATE INDEX IF NOT EXISTS idx_proj_automation_audit_project ON vkpi_project_automation_audit(project_id);
CREATE INDEX IF NOT EXISTS idx_proj_automation_audit_created ON vkpi_project_automation_audit(created_at);

COMMENT ON TABLE vkpi_project_automation_audit IS
  'W2 Projects 自动化审计:哪单同步/哪天开窗/扫了谁/命中啥/为何进复盘;CHECK(touches_v6_fit=FALSE) 兜底永不碰 viltrox_fit_score。';
