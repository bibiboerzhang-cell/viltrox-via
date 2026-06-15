-- 132: 真·活动共享成员表(ADDITIVE,加宽显式共享活动的可见性 + 收紧写权限,镜像 131 项目共享)。
-- 背景:Events 此前 list_events 仅按 owner_id/team_ids 过滤读;写端对任何 vkpi:write 放行(过松)。
--   本表把「显式共享给某员工」落成真表 —— service.list_events / scope.assert_event_access 据此:
--   读侧再 OR 进「被共享给自己」的活动;写侧收紧成 owner/editor-member/admin 才可改。
-- 红线(ADDITIVE-only):本迁移只新增隔离表 + 索引,绝不改 vkpi_events/staff 任何列;
--   既有 owner/team_ids 读可见性原样保留 —— 共享只「加宽」读,不「放开」写。
-- event_id 类型:VARCHAR(64) —— 与 vkpi_events.id 一致(前端 'evt_...' 串 id)。
-- role:'viewer'(只读)| 'editor'(可写)。assert_event_access 据 role 区分读/写。
-- UNIQUE(event_id, staff_id):同一活动同一人只一条共享记录。
-- 级联:event 删→成员行随删;staff 删→其成员行随删(ON DELETE CASCADE)。
-- 注意:本表不在 connection.py 自动注册(由主控审阅后手动接线),回滚见 _down。

CREATE TABLE IF NOT EXISTS vkpi_event_members (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES vkpi_events(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'viewer',
    added_by_staff_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, staff_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_event_members_staff
    ON vkpi_event_members (staff_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_event_members_event
    ON vkpi_event_members (event_id);

COMMENT ON TABLE vkpi_event_members IS
    '真·活动共享成员:显式把活动共享给某员工(viewer 只读 / editor 可写)。ADDITIVE——只加宽显式共享活动的读可见性,不收窄既有 owner/team_ids;写侧收紧成 owner/editor/admin。';
