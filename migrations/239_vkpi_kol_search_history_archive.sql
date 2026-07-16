-- KOL search history is user-facing navigation as well as operational evidence.
-- "Delete" therefore archives a session instead of cascading into its items/jobs.
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.
ALTER TABLE vkpi_kol_search_sessions
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;
ALTER TABLE vkpi_kol_search_sessions
    ADD COLUMN IF NOT EXISTS archived_by BIGINT NULL;
ALTER TABLE vkpi_kol_search_sessions
    ADD COLUMN IF NOT EXISTS archive_reason TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_history_active_owner
    ON vkpi_kol_search_sessions(created_by, updated_at DESC, id DESC)
    WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_history_archived_owner
    ON vkpi_kol_search_sessions(created_by, archived_at DESC, id DESC)
    WHERE archived_at IS NOT NULL;

COMMENT ON COLUMN vkpi_kol_search_sessions.archived_at IS
    'Soft archive timestamp for the personal search-history surface; session items and jobs remain intact.';
COMMENT ON COLUMN vkpi_kol_search_sessions.archived_by IS
    'Staff/user id that archived this personal search-history entry.';
COMMENT ON COLUMN vkpi_kol_search_sessions.archive_reason IS
    'Short audit reason for history archival; never used to drive ranking or fit.';
