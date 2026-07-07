-- 223_vkpi_prediction_runs_fva.sql — 预测账本补两列做 FVA(Forecast Value Add)对照。
-- 背景:评估在线余量要能回答「模型比最笨基线好多少」;需在预测运行上记下同口径的
--   基线预测值,并标注这条预测由哪一步产生(规则 / 模型 / 人工改写 / 基线),
--   weekly_rollup 才能把同 (sku, market, channel) 的 baseline 与 model 两版对齐算增量。
-- 与 220 vkpi_prediction_runs 的关系:纯补列,不改既有语义;老行两列为 NULL(诚实缺席)。
-- additive、幂等(ADD COLUMN IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯预测账本补列,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
-- 回滚见 223_vkpi_prediction_runs_fva_down.sql(DROP COLUMN IF EXISTS)。
--
-- 新增字段:
--   baseline_value  DOUBLE PRECISION  —— naive / seasonal-naive 基线预测值(与本预测同口径,做 FVA 参照)。
--   source_step     TEXT              —— 产生步骤(rule / model / human_override / baseline),FVA 分版口径。
BEGIN;
ALTER TABLE vkpi_prediction_runs ADD COLUMN IF NOT EXISTS baseline_value DOUBLE PRECISION;
ALTER TABLE vkpi_prediction_runs ADD COLUMN IF NOT EXISTS source_step TEXT;

CREATE INDEX IF NOT EXISTS idx_vkpi_prediction_runs_fva
    ON vkpi_prediction_runs (organization_id, product_sku, market, channel, source_step);

COMMENT ON COLUMN vkpi_prediction_runs.baseline_value IS
  'FVA 基线预测值: naive/seasonal-naive 同口径参照, 与 model 版比误差增量; 零触 viltrox_fit_score。';
COMMENT ON COLUMN vkpi_prediction_runs.source_step IS
  'FVA 分版口径: 产生该预测的步骤 rule/model/human_override/baseline; weekly_rollup 据此对齐 baseline 与 model 两版。';
COMMIT;
