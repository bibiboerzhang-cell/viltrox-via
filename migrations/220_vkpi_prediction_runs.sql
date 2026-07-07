-- 220_vkpi_prediction_runs.sql — 预测运行账本(全景规格 5.2 节 B 表照单落地)。
-- 背景:每一次模型/规则/LLM 生成的预测都要落账,预判才可回测;run_id 与
--   organization_id 联合唯一,input_fingerprint 记输入指纹保证同输入可对账。
-- 与 215 vkpi_forecast_log 的分工:215 是 KOL 播放量预测专用流水(kol_pool_id 锚),
--   本表是 GTM 级通用预测账本(任意 task_type,产品x市场x渠道口径),互不替代。
-- 消费方:后续校准与误差评估读本表联 221 vkpi_prediction_evals 对答案。
-- organization_id 为商业化前多租户安全字段,当前 Viltrox 单租户缺省 'viltrox'。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯预测账本,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
-- 回滚见 220_vkpi_prediction_runs_down.sql(DROP TABLE IF EXISTS)。
--
-- 字段:
--   id                BIGSERIAL 主键        —— 预测行自增 id。
--   organization_id   TEXT 缺省 'viltrox'   —— 多租户安全字段。
--   run_id            TEXT NOT NULL         —— 预测运行 id(与 organization_id 联合唯一)。
--   model_name        TEXT NOT NULL         —— 模型名(规则名 / 模型名 / LLM 名,可追溯)。
--   model_version     TEXT NOT NULL         —— 模型版本(权重或提示词版本)。
--   task_type         TEXT NOT NULL         —— 预测任务类型(如 launch_forecast / demand_trend)。
--   product_sku       TEXT                  —— 产品 SKU(可空 = 非产品级预测)。
--   market            TEXT                  —— 目标市场(如 US / JP)。
--   channel           TEXT                  —— 渠道(creator / dealer / official 等)。
--   horizon_days      INTEGER               —— 预测窗口天数。
--   input_fingerprint TEXT NOT NULL         —— 输入指纹(同输入同指纹,可对账)。
--   input_summary     JSONB 缺省 '{}'       —— 输入摘要(喂给模型的关键特征快照)。
--   prediction        JSONB NOT NULL        —— 预测正文(结构化预测结果)。
--   p10               DOUBLE PRECISION      —— 预测下沿分位。
--   p50               DOUBLE PRECISION      —— 预测中位分位。
--   p90               DOUBLE PRECISION      —— 预测上沿分位。
--   confidence        TEXT NOT NULL         —— 置信度档位(low / medium / high)。
--   confidence_score  DOUBLE PRECISION      —— 置信度数值(0 到 1,可空)。
--   missing_data      JSONB 缺省 '[]'       —— 缺数据清单(诚实态:缺什么写什么)。
--   basis             JSONB 缺省 '[]'       —— 预测依据清单(证据可追溯)。
--   created_at        TIMESTAMPTZ 缺省 NOW  —— 预测时刻(对答案窗口的起点)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_prediction_runs (
    id                BIGSERIAL PRIMARY KEY,
    organization_id   TEXT NOT NULL DEFAULT 'viltrox',
    run_id            TEXT NOT NULL,
    model_name        TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    task_type         TEXT NOT NULL,
    product_sku       TEXT,
    market            TEXT,
    channel           TEXT,
    horizon_days      INTEGER,
    input_fingerprint TEXT NOT NULL,
    input_summary     JSONB NOT NULL DEFAULT '{}'::jsonb,
    prediction        JSONB NOT NULL,
    p10               DOUBLE PRECISION,
    p50               DOUBLE PRECISION,
    p90               DOUBLE PRECISION,
    confidence        TEXT NOT NULL,
    confidence_score  DOUBLE PRECISION,
    missing_data      JSONB NOT NULL DEFAULT '[]'::jsonb,
    basis             JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_prediction_runs_lookup
    ON vkpi_prediction_runs (organization_id, product_sku, market, channel, created_at DESC);

COMMENT ON TABLE vkpi_prediction_runs IS
  '预测运行账本: 每次模型/规则/LLM 预测落一行; run_id 联合 organization_id 唯一; input_fingerprint 保同输入可对账; missing_data 与 basis 保诚实可追溯; 联 vkpi_prediction_evals 回测校准; 零触 viltrox_fit_score。';
COMMIT;
