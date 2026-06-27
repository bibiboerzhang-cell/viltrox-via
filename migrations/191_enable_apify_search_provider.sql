-- 191_enable_apify_search_provider.sql — 启用 apify_search 发现源(自持,复用现有 Apify)。
-- 适配器自身 env 门控(VKPI_APIFY_SEARCH_ACTOR 设了才真跑),未设则 not_configured,零意外计费。
-- additive、幂等。注释零 ASCII 问号。
BEGIN;
UPDATE vkpi_discovery_providers SET enabled = TRUE, updated_at = NOW() WHERE name = 'apify_search';
COMMIT;
