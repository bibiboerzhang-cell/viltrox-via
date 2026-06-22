-- 180_vkpi_agent_orchestration.sql — 路线1:Agent 编排层(PLAN-ONLY)留痕。
-- vkpi_agent_orchestration_plan:每次"一句话→分步计划"留一条;vkpi_agent_tool_run:计划里每步
-- (批准后执行)留一条。编排器只产计划不执行;写库/烧 LLM 的步骤走 Action Inbox 人审。
-- additive、幂等、零触 viltrox_fit_score / rule_v0。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_agent_orchestration_plan (
    id                   BIGSERIAL PRIMARY KEY,
    goal                 TEXT        NOT NULL DEFAULT '',
    input_context_json   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    plan_json            JSONB       NOT NULL DEFAULT '[]'::jsonb,
    status               TEXT        NOT NULL DEFAULT 'planned'
                            CHECK (status IN ('planned','ready','executing','success','failed','cancelled')),
    estimated_cost_cents INT         NOT NULL DEFAULT 0,
    created_by_staff_id  BIGINT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_agent_tool_run (
    id           BIGSERIAL PRIMARY KEY,
    plan_id      BIGINT      REFERENCES vkpi_agent_orchestration_plan(id) ON DELETE CASCADE,
    tool_id      TEXT        NOT NULL DEFAULT '',
    step_index   INT         NOT NULL DEFAULT 0,
    inputs_json  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    output_ref   TEXT        NOT NULL DEFAULT '',
    cost_cents   INT         NOT NULL DEFAULT 0,
    status       TEXT        NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned','approved','executed','failed','skipped')),
    error        TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at  TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_tool_run_plan ON vkpi_agent_tool_run(plan_id, step_index);

COMMENT ON TABLE vkpi_agent_orchestration_plan IS
    '路线1 编排器 PLAN-ONLY 留痕:一句话目标→分步计划;计划本身不执行,写库/烧LLM步骤走 Action Inbox 人审。';
COMMIT;
