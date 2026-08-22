-- 回滚 287:镜头出镜派生表与扫描账本(纯派生,可由回填脚本重建)。

DROP TABLE IF EXISTS vkpi_kol_lens_evidence_scan;
DROP TABLE IF EXISTS vkpi_kol_lens_evidence;

DELETE FROM schema_migrations
WHERE version_key='287_vkpi_kol_lens_evidence.sql';
