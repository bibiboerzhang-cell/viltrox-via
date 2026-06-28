-- 196_vkpi_competitor_signal_expiry_down.sql — 回滚竞品信号有效期列。
BEGIN;
DROP INDEX IF EXISTS idx_competitor_signals_expires;
ALTER TABLE vkpi_competitor_signals DROP COLUMN IF EXISTS expires_at;
COMMIT;
