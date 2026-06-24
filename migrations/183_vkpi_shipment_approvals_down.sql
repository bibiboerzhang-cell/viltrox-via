-- 183_down — 回滚发货审批门槛表。
BEGIN;
DROP TABLE IF EXISTS vkpi_shipment_approvals;
COMMIT;
