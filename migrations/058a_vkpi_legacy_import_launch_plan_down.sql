DROP TABLE IF EXISTS vkpi_legacy_launch_plans_staging;

ALTER TABLE vkpi_legacy_import_batches
  DROP COLUMN IF EXISTS auto_rollback_at,
  DROP COLUMN IF EXISTS rollback_policy,
  DROP COLUMN IF EXISTS rollback_until;
