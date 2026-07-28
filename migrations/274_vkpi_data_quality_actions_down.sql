-- 274 down: remove only the migration receipt.
--
-- The data-quality action ledger can predate migration 274 because older
-- application versions created it at runtime. Preserve the table and every
-- business action so a code rollback cannot destroy or fabricate history.
DELETE FROM schema_migrations
WHERE version_key = '274_vkpi_data_quality_actions.sql';
