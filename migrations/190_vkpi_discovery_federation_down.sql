-- 190 down — 移除联邦发现 + 富集表。
BEGIN;
DROP TABLE IF EXISTS vkpi_kol_enrichment;
DROP TABLE IF EXISTS vkpi_discovery_providers;
COMMIT;
