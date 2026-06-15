-- 133: 公司公共项目/活动标记(is_public,ADDITIVE,只「加宽」公共资源的读可见性)。
-- 背景:某些项目/活动是「公司公共」——希望对所有员工可见(无需逐个共享/派活)。
--   本列把「公共」落成真布尔列:scope.project_filter / events.list_events 据此读侧再
--   OR 进「is_public=TRUE」的项目/活动;assert_project_access / assert_event_access
--   对 is_public(且非 restricted)的行放行「读」。
-- 红线(ADDITIVE-only):
--   - 只「加宽」读 —— 写权仍严格 gate 在 owner/editor/admin(public 不放开写)。
--   - 与 restricted 互斥:restricted 项目永不因 is_public 而对非授权员工可见(先遮后开铁则不破)。
--   - 列类型与 110_restricted 对齐:BOOLEAN NOT NULL DEFAULT FALSE(默认非公共,存量行不受影响)。
--   - 绝不碰 viltrox_fit_score / rule_v0,绝不动既有列。
-- 注意:本列不在 connection.py 自动注册(由主控审阅后手动接线),回滚见 _down。

ALTER TABLE vkpi_projects ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE vkpi_events ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;
