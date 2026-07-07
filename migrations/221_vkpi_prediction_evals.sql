-- 221_vkpi_prediction_evals.sql — 预测评估账本(全景规格 5.2 节 C 表照单落地)。
-- 背景:结果回来后对 220 vkpi_prediction_runs 里的预测做校准与误差评估;
--   一条预测可对多个结果(run_id + outcome_id 联合唯一),带内命中与方向命中
--   分开记,calibrated_bucket 供校准曲线分桶统计。
-- 关联口径:run_id 软关联 vkpi_prediction_runs.run_id,outcome_id 软关联
--   vkpi_gtm_outcomes.id;均不做 FK 免耦合(避历史脏数据 apply 失败)。
-- organization_id 为商业化前多租户安全字段,当前 Viltrox 单租户缺省 'viltrox'。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯评估账本,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
-- 回滚见 221_vkpi_prediction_evals_down.sql(DROP TABLE IF EXISTS)。
--
-- 字段:
--   id                BIGSERIAL 主键        —— 评估行自增 id。
--   organization_id   TEXT 缺省 'viltrox'   —— 多租户安全字段。
--   run_id            TEXT NOT NULL         —— 被评估的预测运行 id(软关联 220)。
--   outcome_id        BIGINT                —— 对应结果行 id(软关联 vkpi_gtm_outcomes.id,可空)。
--   actual_value      DOUBLE PRECISION      —— 实际数值(与预测分位同口径)。
--   actual_json       JSONB 缺省 '{}'       —— 实际结果结构化快照。
--   error_abs         DOUBLE PRECISION      —— 绝对误差(实际减 p50 取绝对值)。
--   error_pct         DOUBLE PRECISION      —— 相对误差(绝对误差除以实际值)。
--   interval_hit      BOOLEAN               —— 带内命中(实际落在 p10 到 p90 之间)。
--   direction_hit     BOOLEAN               —— 方向命中(涨跌方向判对)。
--   calibrated_bucket TEXT                  —— 校准分桶(供校准曲线统计)。
--   evaluated_at      TIMESTAMPTZ 缺省 NOW  —— 评估时刻。
--   notes             TEXT                  —— 评估备注(人工或 job 留痕)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_prediction_evals (
    id                BIGSERIAL PRIMARY KEY,
    organization_id   TEXT NOT NULL DEFAULT 'viltrox',
    run_id            TEXT NOT NULL,
    outcome_id        BIGINT,
    actual_value      DOUBLE PRECISION,
    actual_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_abs         DOUBLE PRECISION,
    error_pct         DOUBLE PRECISION,
    interval_hit      BOOLEAN,
    direction_hit     BOOLEAN,
    calibrated_bucket TEXT,
    evaluated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes             TEXT,
    UNIQUE (organization_id, run_id, outcome_id)
);

COMMENT ON TABLE vkpi_prediction_evals IS
  '预测评估账本: 结果回来后对 vkpi_prediction_runs 的预测做误差与校准评估; run_id 加 outcome_id 联合唯一; interval_hit 带内命中与 direction_hit 方向命中分开记; 零触 viltrox_fit_score。';
COMMIT;
