-- 272 down: remove the assignment linkage column.
DROP INDEX IF EXISTS idx_vkpi_shipments_assignment;
ALTER TABLE vkpi_shipments DROP COLUMN IF EXISTS assignment_id;
