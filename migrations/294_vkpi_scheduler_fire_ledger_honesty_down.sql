-- 294 down:回到 249 的三值 CHECK(先把新终态行归入 failed,否则约束加不回去)。
UPDATE vkpi_scheduler_fire_claims SET status = 'failed'
 WHERE status = 'claim_failed' OR status LIKE 'blocked:%';
ALTER TABLE vkpi_scheduler_fire_claims DROP CONSTRAINT IF EXISTS chk_vkpi_scheduler_fire_claims_status;
ALTER TABLE vkpi_scheduler_fire_claims ADD CONSTRAINT vkpi_scheduler_fire_claims_status_check
  CHECK (status IN ('running', 'completed', 'failed'));
ALTER TABLE scheduler_tasks DROP COLUMN IF EXISTS last_status;
