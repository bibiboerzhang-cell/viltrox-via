-- 256: Revoke pre-native-proof financial artifacts without deleting history.
--
-- Migration 255 invalidated their underlying materialized metrics.  Frozen
-- report/export files can otherwise keep presenting the former values even
-- though current readers are fail-closed.  Archive reports with typed,
-- fail-closed truth markers and invalidate only financial export families.  The migration
-- runner owns the transaction boundary.

ALTER TABLE vkpi_report_runs
  ADD COLUMN IF NOT EXISTS truth_invalidated_at TIMESTAMPTZ;

ALTER TABLE vkpi_report_runs
  ADD COLUMN IF NOT EXISTS truth_invalidation_reason TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_report_runs
  ADD COLUMN IF NOT EXISTS truth_invalidation_migration INTEGER;

ALTER TABLE vkpi_report_runs
  ADD COLUMN IF NOT EXISTS truth_restorable BOOLEAN NOT NULL DEFAULT TRUE;

-- P1.6 has a separate legacy report store and API.  Mark every row that
-- existed before this one-shot migration so its frozen markdown cannot keep
-- presenting pre-native financial claims.  Rows generated after 256 retain
-- the defaults and remain available.
ALTER TABLE vkpi_weekly_reports
  ADD COLUMN IF NOT EXISTS truth_invalidated_at TIMESTAMPTZ;

ALTER TABLE vkpi_weekly_reports
  ADD COLUMN IF NOT EXISTS truth_invalidation_reason TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_weekly_reports
  ADD COLUMN IF NOT EXISTS truth_invalidation_migration INTEGER;

ALTER TABLE vkpi_weekly_reports
  ADD COLUMN IF NOT EXISTS truth_restorable BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE vkpi_weekly_reports
  ADD COLUMN IF NOT EXISTS source_data_status TEXT NOT NULL DEFAULT 'awaiting_source';

ALTER TABLE vkpi_weekly_reports
  ADD COLUMN IF NOT EXISTS source_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE vkpi_weekly_reports
  ADD COLUMN IF NOT EXISTS source_is_partial BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE vkpi_report_runs
SET status='archived',
    error_message='truth_invalidated_by_migration_256',
    truth_invalidated_at=CURRENT_TIMESTAMP,
    truth_invalidation_reason='pre_native_shopify_financial_truth',
    truth_invalidation_migration=256,
    truth_restorable=FALSE
WHERE status='ready'
  AND report_type='weekly';

UPDATE vkpi_weekly_reports
SET status='invalidated',
    truth_invalidated_at=CURRENT_TIMESTAMP,
    truth_invalidation_reason='pre_native_shopify_financial_truth',
    truth_invalidation_migration=256,
    truth_restorable=FALSE
WHERE COALESCE(status, '') <> 'invalidated';

UPDATE vkpi_export_jobs
SET status='invalidated',
    error_message='truth_invalidated_by_migration_256'
WHERE status='ready'
  AND export_type IN (
    'weekly', 'attribution', 'finance', 'cost', 'costs', 'kpi_ledger', 'staff_kpi'
  );
