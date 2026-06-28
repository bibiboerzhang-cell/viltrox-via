-- 198_vkpi_metric_value_provenance_down.sql — 回滚指标值可信度三元组。
BEGIN;
ALTER TABLE vkpi_metric_values DROP COLUMN IF EXISTS data_status;
ALTER TABLE vkpi_metric_values DROP COLUMN IF EXISTS confidence;
ALTER TABLE vkpi_metric_values DROP COLUMN IF EXISTS is_partial;
COMMIT;
