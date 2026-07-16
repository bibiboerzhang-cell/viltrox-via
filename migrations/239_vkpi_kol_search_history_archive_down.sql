BEGIN;

DROP INDEX IF EXISTS idx_vkpi_kol_search_history_archived_owner;
DROP INDEX IF EXISTS idx_vkpi_kol_search_history_active_owner;

ALTER TABLE vkpi_kol_search_sessions DROP COLUMN IF EXISTS archive_reason;
ALTER TABLE vkpi_kol_search_sessions DROP COLUMN IF EXISTS archived_by;
ALTER TABLE vkpi_kol_search_sessions DROP COLUMN IF EXISTS archived_at;

COMMIT;
