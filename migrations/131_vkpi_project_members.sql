-- 131: 真·项目共享成员表(ADDITIVE,加宽显式共享项目的可见性,绝不收窄既有 own-only)。
-- 背景:此前 staff_groups.permissions_json.shared_projects 只是设置页 UI 元数据,不入
--   scope —— 共享是「假」的。本表把共享落成真表,scope.project_filter / assert_project_access
--   据此放行:非 admin 员工除自己负责/创建的项目外,还能看到「被显式共享给自己」的项目。
-- 红线(ADDITIVE-only):本迁移只新增隔离表 + 索引,绝不改 vkpi_projects/staff 任何列;
--   既有 own-only 限制原样保留 —— 共享只「加宽」,不「放开」。
-- role:'viewer'(只读)| 'editor'(可写)。assert_project_access 据 role 区分读/写。
-- restricted 项目仍只认 assigned/creator/can_view_all —— 共享成员的放行同样 gate 在
--   非 restricted 上(与 own 子句对齐;先遮后开铁则不破)。
-- UNIQUE(project_id, staff_id):同一项目同一人只一条共享记录。
-- 级联:project 删→成员行随删;staff 删→其成员行随删(ON DELETE CASCADE)。
-- 注意:本表不在 connection.py 自动注册(由主控审阅后手动接线),回滚见 _down。

CREATE TABLE IF NOT EXISTS vkpi_project_members (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'viewer',
    added_by_staff_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, staff_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_project_members_staff
    ON vkpi_project_members (staff_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_project_members_project
    ON vkpi_project_members (project_id);

COMMENT ON TABLE vkpi_project_members IS
    '真·项目共享成员:显式把项目共享给某员工(viewer 只读 / editor 可写)。ADDITIVE——只加宽显式共享项目的可见性,不收窄既有 own-only;restricted 项目仍只认 assigned/creator/admin。';
