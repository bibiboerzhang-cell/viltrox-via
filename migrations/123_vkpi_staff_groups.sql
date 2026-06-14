-- 123: V-KPI Staff Groups - 员工分组(团队)表,供 TeamModal/EditGroupModal/Events 选人复用。
-- member_ids = jsonb 数组(bigint staff id),镜像 vkpi_events.team_ids 口径。
-- permissions_json 内嵌 {shared_projects,shared_kol_pool,kpi_goal,reminder_rule}。
-- created_by FK staff(id) ON DELETE SET NULL(FK-safe:无 actor 时写 NULL)。

CREATE TABLE IF NOT EXISTS vkpi_staff_groups (
    id VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    member_ids JSONB DEFAULT '[]',
    permissions_json JSONB DEFAULT '{}',
    created_by BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_staff_groups_created_by ON vkpi_staff_groups(created_by, updated_at DESC);

COMMENT ON TABLE vkpi_staff_groups IS 'V-KPI Staff Groups - 员工分组/团队,member_ids 镜像 vkpi_events.team_ids,permissions_json 内嵌共享/目标/提醒口径';
