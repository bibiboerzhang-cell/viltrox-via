-- 236 down: remove global-search-owned indexes. pg_trgm may be shared, so keep it.
BEGIN;

DROP INDEX IF EXISTS idx_vkpi_events_search_trgm;
DROP INDEX IF EXISTS idx_vkpi_events_search_fts;
DROP INDEX IF EXISTS idx_vkpi_events_search_title_prefix;

DROP INDEX IF EXISTS idx_vkpi_projects_search_trgm;
DROP INDEX IF EXISTS idx_vkpi_projects_search_fts;
DROP INDEX IF EXISTS idx_vkpi_projects_search_name_prefix;

DROP INDEX IF EXISTS idx_vkpi_kol_pool_search_trgm;
DROP INDEX IF EXISTS idx_vkpi_kol_pool_search_fts;
DROP INDEX IF EXISTS idx_vkpi_kol_pool_search_handle_prefix;
DROP INDEX IF EXISTS idx_vkpi_kol_pool_search_name_prefix;

COMMIT;
