-- 272: vkpi_shipments gains a real assignment linkage column.
--
-- Why: the fulfillment loop reads vkpi_shipments (scan_delivered_into_windows)
-- but the shipment writer could only stash assignment_id inside metadata_json,
-- forcing the scanner into a per-project fan-out that opened observation
-- windows for every assignment in the project (102 windows all from one demo
-- shipment on project 3994). A first-class column lets each shipment open a
-- window for exactly its own assignment.
--
-- Truth boundaries: additive only. No dealer scoring, no viltrox_fit_score,
-- no rule_v0 involvement. Existing rows keep their data; the one legacy demo
-- row is backfilled from metadata_json below.

ALTER TABLE vkpi_shipments
  ADD COLUMN IF NOT EXISTS assignment_id BIGINT
  REFERENCES vkpi_project_kol_assignments(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_shipments_assignment
  ON vkpi_shipments(assignment_id)
  WHERE assignment_id IS NOT NULL;

-- Backfill the real column from metadata for rows written before this
-- migration (record_delivered_signal stored assignment_id in metadata_json).
UPDATE vkpi_shipments
SET assignment_id = NULLIF(metadata_json->>'assignment_id', '')::bigint
WHERE assignment_id IS NULL
  AND NULLIF(metadata_json->>'assignment_id', '') IS NOT NULL;
