-- =====================================================================
-- 010_party_layer_rollback.sql
-- Rollback for Phase 1 party layer migration
--
-- 用法: 手动 psql 进去执行,不走 migration runner
--   psql $DATABASE_URL -f 010_party_layer_rollback.sql
--
-- 警告: 会丢弃所有 party / identity_links / consent / events 数据
-- =====================================================================

DROP VIEW IF EXISTS v_party_activity_14d;

DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS consent_records CASCADE;
DROP TABLE IF EXISTS identity_links CASCADE;
DROP TABLE IF EXISTS parties CASCADE;

DELETE FROM schema_migrations WHERE version_key = '010_party_layer.sql';
