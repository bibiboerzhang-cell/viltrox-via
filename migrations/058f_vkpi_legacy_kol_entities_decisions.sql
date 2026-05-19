-- P2C-2: review decisions for legacy KOL entity resolution.
--
-- Decisions are recorded on the canonical entity without moving refs. P2D will
-- fold merge_with decisions during dry-run/commit so staging remains reversible.

ALTER TABLE vkpi_legacy_kol_entities
  ADD COLUMN IF NOT EXISTS resolution_decision TEXT,
  ADD COLUMN IF NOT EXISTS merge_target_entity_id BIGINT REFERENCES vkpi_legacy_kol_entities(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS merge_target_uid TEXT,
  ADD COLUMN IF NOT EXISTS decision_reason TEXT,
  ADD COLUMN IF NOT EXISTS decision_note TEXT,
  ADD COLUMN IF NOT EXISTS decided_by TEXT,
  ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_legacy_kol_entities_pending_decision
  ON vkpi_legacy_kol_entities (import_batch_id, weak_label, resolution_status)
  WHERE resolution_decision IS NULL;
