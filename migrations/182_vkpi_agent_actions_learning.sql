-- 182_vkpi_agent_actions_learning.sql — S3:学习闭环建表(里程碑② 先建空表,让数据先攒)。
-- vkpi_agent_actions:每个有价值动作留痕(收藏/拒绝/加项目/复盘…:who/why/cost/detail);
-- vkpi_agent_outcome_evaluations:动作的结果评估(成功与否 / 是否再推荐)→ 未来回写影响推荐权重。
-- additive、幂等、零触 viltrox_fit_score / rule_v0。本轮只「建表 + 写入」,权重回写留里程碑②后续。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_agent_actions (
    id              BIGSERIAL PRIMARY KEY,
    action_kind     TEXT        NOT NULL DEFAULT '',   -- favorite|reject|add_to_project|retrospective|...
    entity_type     TEXT        NOT NULL DEFAULT '',   -- kol|project|event|...
    entity_id       TEXT        NOT NULL DEFAULT '',
    actor_staff_id  BIGINT,
    reason          TEXT        NOT NULL DEFAULT '',
    cost_cents      INT         NOT NULL DEFAULT 0,
    detail_json     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_actions_entity ON vkpi_agent_actions(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_actions_kind ON vkpi_agent_actions(action_kind, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_agent_outcome_evaluations (
    id               BIGSERIAL PRIMARY KEY,
    agent_action_id  BIGINT      REFERENCES vkpi_agent_actions(id) ON DELETE SET NULL,
    entity_type      TEXT        NOT NULL DEFAULT '',
    entity_id        TEXT        NOT NULL DEFAULT '',
    outcome          TEXT        NOT NULL DEFAULT '',  -- success|fail|partial
    success          BOOLEAN,
    recommend_again  BOOLEAN,
    evidence_json    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_outcome_entity ON vkpi_agent_outcome_evaluations(entity_type, entity_id, created_at DESC);

COMMENT ON TABLE vkpi_agent_actions IS 'S3 学习闭环:每个有价值动作留痕(who/why/cost/detail),为②权重回写攒历史。';
COMMIT;
