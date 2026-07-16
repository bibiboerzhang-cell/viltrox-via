DROP INDEX IF EXISTS idx_advisor_feedback_events_owner;
DROP TABLE IF EXISTS vkpi_advisor_message_feedback_events;

DROP INDEX IF EXISTS idx_advisor_feedback_thread;
DROP INDEX IF EXISTS uq_advisor_feedback_request;
DROP TABLE IF EXISTS vkpi_advisor_message_feedback;

DELETE FROM schema_migrations
WHERE version_key = '268_vkpi_advisor_trusted_feedback.sql';
