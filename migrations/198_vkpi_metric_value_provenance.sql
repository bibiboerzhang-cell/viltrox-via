-- 198_vkpi_metric_value_provenance.sql — Data Catalog:给指标值加可信度三元组。
-- 让任意 dashboard 数字自报 real/seeded/partial/awaiting_source/empty + 置信度,
-- 配合指标注册表(definitions.py)成统一数据目录(数字可追溯真假来源)。
-- additive、幂等。注释零 ASCII 问号。红线:纯元数据,零触 viltrox_fit_score。
BEGIN;
ALTER TABLE vkpi_metric_values ADD COLUMN IF NOT EXISTS data_status TEXT;
ALTER TABLE vkpi_metric_values ADD COLUMN IF NOT EXISTS confidence NUMERIC;
ALTER TABLE vkpi_metric_values ADD COLUMN IF NOT EXISTS is_partial BOOLEAN;
-- 既有行回填:source_count>0 视为 real;=0 视为 awaiting_source(诚实,只填空)
UPDATE vkpi_metric_values
   SET data_status = CASE WHEN COALESCE(source_count, 0) > 0 THEN 'real' ELSE 'awaiting_source' END,
       is_partial = (COALESCE(source_count, 0) = 0)
 WHERE data_status IS NULL;
COMMIT;
