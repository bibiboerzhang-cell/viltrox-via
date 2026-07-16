-- Roll back Marketing Advisor personal conversations and memory.
-- Export user-owned history before applying this destructive rollback.
BEGIN;

DROP TABLE IF EXISTS vkpi_advisor_memory_events;
DROP TABLE IF EXISTS vkpi_advisor_action_drafts;
DROP TABLE IF EXISTS vkpi_advisor_memory_facts;
DROP TABLE IF EXISTS vkpi_advisor_memory_candidates;
DROP TABLE IF EXISTS vkpi_advisor_memory_settings;
DROP TABLE IF EXISTS vkpi_advisor_messages;
DROP TABLE IF EXISTS vkpi_advisor_threads;

DELETE FROM schema_migrations
WHERE version_key = '250_vkpi_marketing_advisor_memory.sql';

COMMIT;
