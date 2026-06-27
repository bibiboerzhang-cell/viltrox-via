-- 192 down — 移除统一事件总线。
BEGIN;
DROP TABLE IF EXISTS vkpi_event_ledger;
COMMIT;
