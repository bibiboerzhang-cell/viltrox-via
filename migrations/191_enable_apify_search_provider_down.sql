-- 191 down — 禁用 apify_search 发现源。
BEGIN;
UPDATE vkpi_discovery_providers SET enabled = FALSE, updated_at = NOW() WHERE name = 'apify_search';
COMMIT;
