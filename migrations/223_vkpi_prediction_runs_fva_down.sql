-- 223 down — 撤销 FVA 两列与其索引(DROP IF EXISTS,幂等)。
BEGIN;
DROP INDEX IF EXISTS idx_vkpi_prediction_runs_fva;
ALTER TABLE vkpi_prediction_runs DROP COLUMN IF EXISTS source_step;
ALTER TABLE vkpi_prediction_runs DROP COLUMN IF EXISTS baseline_value;
COMMIT;
