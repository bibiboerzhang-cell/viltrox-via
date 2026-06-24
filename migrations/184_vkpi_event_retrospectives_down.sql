-- 184 down — 移除活动复盘持久化表。
BEGIN;
DROP TABLE IF EXISTS vkpi_event_retrospectives;
COMMIT;
