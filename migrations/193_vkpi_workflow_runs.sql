-- 193_vkpi_workflow_runs.sql — Durable Workflow(P2):可暂停/恢复/重放的流程引擎底座。
-- 每个复杂流程 = 一个 run + 多个 step + checkpoint;失败可从中间 step 恢复,不重跑全部。
-- 轴A:trace_id;轴B:organization_id(默认1)。additive、幂等。注释零 ASCII 问号。零触 viltrox_fit_score。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_workflow_runs (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT      NOT NULL DEFAULT 1,
    workflow_name   TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'running',  -- running / paused / completed / failed
    input_json      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    current_step    INTEGER     NOT NULL DEFAULT 0,
    entity_type     TEXT        NOT NULL DEFAULT '',
    entity_id       TEXT        NOT NULL DEFAULT '',
    trace_id        TEXT        NOT NULL DEFAULT '',
    last_error      TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON vkpi_workflow_runs(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_name   ON vkpi_workflow_runs(workflow_name, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_workflow_steps (
    id           BIGSERIAL PRIMARY KEY,
    run_id       BIGINT      NOT NULL,
    step_index   INTEGER     NOT NULL,
    step_name    TEXT        NOT NULL DEFAULT '',
    status       TEXT        NOT NULL DEFAULT 'pending',     -- pending / running / done / failed / skipped
    output_json  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT        NOT NULL DEFAULT '',
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workflow_steps_run ON vkpi_workflow_steps(run_id, step_index);

CREATE TABLE IF NOT EXISTS vkpi_workflow_checkpoints (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT      NOT NULL,
    step_index  INTEGER     NOT NULL,
    state_json  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workflow_ckpt_run ON vkpi_workflow_checkpoints(run_id, step_index DESC);
COMMIT;
