-- Roll back P2C-2 review decision fields.

DROP INDEX IF EXISTS idx_legacy_kol_entities_pending_decision;

ALTER TABLE vkpi_legacy_kol_entities
  DROP COLUMN IF EXISTS resolution_decision,
  DROP COLUMN IF EXISTS merge_target_entity_id,
  DROP COLUMN IF EXISTS merge_target_uid,
  DROP COLUMN IF EXISTS decision_reason,
  DROP COLUMN IF EXISTS decision_note,
  DROP COLUMN IF EXISTS decided_by,
  DROP COLUMN IF EXISTS decided_at;
