-- 236: first-screen global search retrieval indexes for KOLs, projects, and events.
-- Built-in Postgres FTS and literal-prefix indexes are mandatory. pg_trgm is an
-- optional accelerator/fuzzy fallback: lack of extension privileges or extension
-- files must not block the migration.
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_search_name_prefix
    ON vkpi_kol_pool ((LOWER(COALESCE(display_name, ''))) text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_search_handle_prefix
    ON vkpi_kol_pool ((LOWER(COALESCE(handle, ''))) text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_search_fts
    ON vkpi_kol_pool USING GIN (
        TO_TSVECTOR(
            'simple'::regconfig,
            LOWER(COALESCE(display_name, '') || ' ' || COALESCE(handle, ''))
        )
    );

CREATE INDEX IF NOT EXISTS idx_vkpi_projects_search_name_prefix
    ON vkpi_projects ((LOWER(COALESCE(project_name, ''))) text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_vkpi_projects_search_fts
    ON vkpi_projects USING GIN (
        TO_TSVECTOR('simple'::regconfig, LOWER(COALESCE(project_name, '')))
    );

CREATE INDEX IF NOT EXISTS idx_vkpi_events_search_title_prefix
    ON vkpi_events ((LOWER(COALESCE(title, ''))) text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_vkpi_events_search_fts
    ON vkpi_events USING GIN (
        TO_TSVECTOR('simple'::regconfig, LOWER(COALESCE(title, '')))
    );

DO $migration$
BEGIN
    BEGIN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_trgm';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pg_trgm unavailable; global search keeps FTS and LIKE fallback';
    END;
END
$migration$;

DO $migration$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        BEGIN
            EXECUTE $index$
                CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_search_trgm
                ON vkpi_kol_pool USING GIN (
                    (LOWER(COALESCE(display_name, '') || ' ' || COALESCE(handle, '')))
                    gin_trgm_ops
                )
            $index$;
            EXECUTE $index$
                CREATE INDEX IF NOT EXISTS idx_vkpi_projects_search_trgm
                ON vkpi_projects USING GIN (
                    (LOWER(COALESCE(project_name, ''))) gin_trgm_ops
                )
            $index$;
            EXECUTE $index$
                CREATE INDEX IF NOT EXISTS idx_vkpi_events_search_trgm
                ON vkpi_events USING GIN (
                    (LOWER(COALESCE(title, ''))) gin_trgm_ops
                )
            $index$;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_trgm indexes unavailable; global search keeps FTS and LIKE fallback';
        END;
    END IF;
END
$migration$;
