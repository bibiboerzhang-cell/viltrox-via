-- 204 回滚:去掉 L0 联系方式的 置信度/取证/首末见 列。
BEGIN;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS confidence;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS evidence_text;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS first_seen_at;
ALTER TABLE vkpi_kol_pool_contacts DROP COLUMN IF EXISTS last_seen_at;
COMMIT;
