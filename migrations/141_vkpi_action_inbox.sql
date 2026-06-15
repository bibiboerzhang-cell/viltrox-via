-- 141(W1 · Action Inbox 地基,dry-run only)。Auto-Ops OS 的"第一刀":
-- 每天聚合 8 类待办建议(kol_profile/deep_missing/failed_retry/project_observation/
-- content_candidate/retrospective/event_followup/inventory_low),只产建议、不执行、不写业务表。
--
-- 红线(写死在表结构里):
--   1. touches_v6_fit 列 + CHECK(touches_v6_fit=FALSE):Action 系统永远不碰 viltrox_fit_score。
--   2. requires_approval 默认 TRUE:任何会烧 LLM / 写业务表的动作必须人审(W2 执行器读这列)。
--   9. payload_json 严禁写入任何 secret(纯结构化业务上下文)。
-- additive only、幂等(IF NOT EXISTS);与 KOL Pool / 评分域 / rule_v0 物理隔离。
-- 回滚见 141_vkpi_action_inbox_down.sql。

-- 建议台账:每条 = 一个"系统建议你做的事",自带 权限/端点/成本/是否写库·LLM/影响实体/为什么。
CREATE TABLE IF NOT EXISTS vkpi_action_inbox (
    id                  BIGSERIAL PRIMARY KEY,
    dedupe_key          TEXT NOT NULL UNIQUE,          -- 自然键(category:entity),日重算幂等 UPSERT
    category            TEXT NOT NULL,                 -- 8 类之一
    title               TEXT NOT NULL DEFAULT '',
    detail              TEXT NOT NULL DEFAULT '',      -- 人话说明
    priority            TEXT NOT NULL DEFAULT 'medium',-- high|medium|low
    entity_type         TEXT NOT NULL DEFAULT '',      -- kol|project|event|job|inventory|shipment
    entity_id           TEXT NOT NULL DEFAULT '',      -- 软引用(无 FK,跨多表)
    suggested_endpoint  TEXT NOT NULL DEFAULT '',      -- 这条建议对应的执行端点(W2 用)
    estimated_cost_cents INTEGER NOT NULL DEFAULT 0,   -- 预估成本(W2 预算闸用)
    writes_business_data BOOLEAN NOT NULL DEFAULT FALSE,-- 执行是否写业务表
    uses_llm            BOOLEAN NOT NULL DEFAULT FALSE, -- 执行是否烧 LLM
    requires_approval   BOOLEAN NOT NULL DEFAULT TRUE,  -- 红线2:默认必须人审
    owner_staff_id      BIGINT,                        -- 归属成员(scope 过滤;NULL=公司/全局级,仅管理层可见)
    reason              TEXT NOT NULL DEFAULT '',       -- 红线7:每条可追溯原因
    payload_json        JSONB NOT NULL DEFAULT '{}',    -- 红线9:严禁 secret
    touches_v6_fit      BOOLEAN NOT NULL DEFAULT FALSE, -- 红线1:CHECK 兜底永 FALSE
    status              TEXT NOT NULL DEFAULT 'suggested',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_action_inbox_no_v6_fit CHECK (touches_v6_fit = FALSE),
    CONSTRAINT chk_action_inbox_status
        CHECK (status IN ('suggested', 'approved', 'dismissed', 'snoozed', 'executed', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_action_inbox_status ON vkpi_action_inbox(status);
CREATE INDEX IF NOT EXISTS idx_action_inbox_category ON vkpi_action_inbox(category);
CREATE INDEX IF NOT EXISTS idx_action_inbox_owner ON vkpi_action_inbox(owner_staff_id);
CREATE INDEX IF NOT EXISTS idx_action_inbox_created ON vkpi_action_inbox(created_at);

COMMENT ON TABLE vkpi_action_inbox IS
  'W1 Action Inbox: 每天聚合 8 类待办建议(dry-run);CHECK(touches_v6_fit=FALSE) 兜底永不碰 viltrox_fit_score;owner_staff_id NULL=公司级仅管理层可见。';

-- 执行台账:W1 只写 run 级 dry_run 行(审计起点);W2 执行器每动作落一行(可重试/可忽略)。
CREATE TABLE IF NOT EXISTS vkpi_action_execution_ledger (
    id              BIGSERIAL PRIMARY KEY,
    action_id       BIGINT REFERENCES vkpi_action_inbox(id) ON DELETE SET NULL,
    category        TEXT NOT NULL DEFAULT '',
    dedupe_key      TEXT NOT NULL DEFAULT '',
    actor_staff_id  BIGINT,                          -- 谁批准/触发(系统 run = NULL)
    mode            TEXT NOT NULL DEFAULT 'dry_run',  -- dry_run|executed
    outcome         TEXT NOT NULL DEFAULT 'pending',  -- pending|success|failed|skipped|ignored
    endpoint        TEXT NOT NULL DEFAULT '',
    cost_cents      INTEGER NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT '',
    detail_json     JSONB NOT NULL DEFAULT '{}',
    touches_v6_fit  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_action_ledger_no_v6_fit CHECK (touches_v6_fit = FALSE),
    CONSTRAINT chk_action_ledger_mode CHECK (mode IN ('dry_run', 'executed')),
    CONSTRAINT chk_action_ledger_outcome
        CHECK (outcome IN ('pending', 'success', 'failed', 'skipped', 'ignored'))
);
CREATE INDEX IF NOT EXISTS idx_action_ledger_action ON vkpi_action_execution_ledger(action_id);
CREATE INDEX IF NOT EXISTS idx_action_ledger_dedupe ON vkpi_action_execution_ledger(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_action_ledger_created ON vkpi_action_execution_ledger(created_at);

COMMENT ON TABLE vkpi_action_execution_ledger IS
  'W1/W2 Action 执行台账: W1 仅 run 级 dry_run 审计行; W2 每动作落行(可重试/可忽略); CHECK(touches_v6_fit=FALSE)。';

-- 在调度注册表(130)登记本任务:默认 enabled=FALSE(灰度 low 档),运营在 Ops 页显式开启才真跑。
-- ON CONFLICT DO NOTHING 幂等,绝不覆盖运营已调过的开关。
INSERT INTO scheduler_tasks (task_key, label, risk_level)
VALUES ('daily_action_inbox_generate', 'Auto-Ops 每日行动建议(dry-run)', 'low')
ON CONFLICT (task_key) DO NOTHING;
