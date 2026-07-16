-- 264: Register the broad 08:00 morning bundle as an explicit, default-OFF task.
--
-- ENABLE_SCHEDULER only starts APScheduler.  Execution additionally requires:
--   1. VKPI_COMPOSITE_MORNING_SYNC_ENABLED=1 in reviewed runtime config; and
--   2. this scheduler_tasks row to be enabled by an operator.
--
-- This migration starts no process, fetches no provider and writes no business
-- data.  The dedicated vkpi-sync-daily systemd service remains a separate
-- scripts/cron_daily_sync.py path and is intentionally not changed here.

INSERT INTO scheduler_tasks
  (task_key, label, enabled, max_daily_runs, max_daily_cost_cents,
   allowed_hours, owner, risk_level)
VALUES
  ('vkpi_morning_sync',
   'Composite channel, industry, product monitor and staff digest morning sync',
   FALSE, 1, 0, '08:00-09:00 Asia/Shanghai', 'marketing_ops', 'high')
ON CONFLICT (task_key) DO NOTHING;
