-- 126: P2 履约 stage-2 · 人工复核观察任务表(SAFE,零自动裁决)。
-- 红线:本表是唯一写入目标。系统只 CREATE「待人看」的任务 + READ,绝不自动改
--   vkpi_projects.stage/closed_at、vkpi_project_kol_assignments.stage、vkpi_cost_ledger、
--   viltrox_fit_score、rule_v0。任务的 reviewed/dismissed 只动本表行,不连带改业务表。
-- task_type:content_due(签收满 N 天无内容证据)| delivery_check(物流签收待人核)。
-- status:pending(待复核)| reviewed(已复核)| dismissed(忽略)。
-- 去重:同 (project_id, kol_pool_id, task_type) 的 pending 任务唯一(partial unique);
--   若环境对 partial-unique 支持脆弱,代码侧已先查后插兜底(create_observation_task)。
-- 回滚见 126_vkpi_fulfillment_observation_tasks_down.sql(DROP TABLE)。

CREATE TABLE IF NOT EXISTS vkpi_fulfillment_observation_tasks (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    kol_pool_id BIGINT,
    task_type TEXT NOT NULL,
    reason_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_by_staff_id BIGINT,
    assigned_staff_id BIGINT,
    reviewed_by_staff_id BIGINT,
    note TEXT NOT NULL DEFAULT '',
    closed_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_fot_project_status
    ON vkpi_fulfillment_observation_tasks (project_id, status);
CREATE INDEX IF NOT EXISTS idx_vkpi_fot_status_created
    ON vkpi_fulfillment_observation_tasks (status, created_at);

-- 同一项目/KOL/类型只允许一条 pending,避免扫描重复刷任务(代码侧另有先查后插兜底)。
CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_fot_pending
    ON vkpi_fulfillment_observation_tasks (project_id, kol_pool_id, task_type)
    WHERE status = 'pending';

COMMENT ON TABLE vkpi_fulfillment_observation_tasks IS
    'P2 履约 stage-2 人工复核观察任务表;系统只创建待人看任务 + 读取,零自动裁决,绝不改项目/派单/费用状态。';
