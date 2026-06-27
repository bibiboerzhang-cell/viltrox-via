-- 194_vkpi_evals.sql — Evals 评测体系(P4):用业务评测保证升级是变聪明非变玄学。
-- run = 一次套件运行;result = 每个 case 的通过/分数/明细。additive、幂等。注释零 ASCII 问号。
-- 红线:评测只读跑系统 + 断言,零触 viltrox_fit_score(评测本身校验不触碰才算过)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_eval_runs (
    id           BIGSERIAL PRIMARY KEY,
    suite        TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'running',  -- running / done
    total        INTEGER     NOT NULL DEFAULT 0,
    passed       INTEGER     NOT NULL DEFAULT 0,
    summary_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_suite ON vkpi_eval_runs(suite, started_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_eval_results (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT      NOT NULL,
    case_name   TEXT        NOT NULL,
    passed      BOOLEAN     NOT NULL DEFAULT FALSE,
    score       DOUBLE PRECISION,
    detail      TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run ON vkpi_eval_results(run_id, case_name);
COMMIT;
