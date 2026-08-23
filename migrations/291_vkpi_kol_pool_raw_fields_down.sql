-- 回滚 291:KOL 池 raw 字段提列(纯派生列,可由回填脚本重建)。
-- 不动迁移 208 的 is_verified / is_tt_seller / is_commerce_user。

ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS raw_fields_extractor_version;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS raw_fields_extracted_at;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS tagged_brands_json;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS topic_details_json;

DELETE FROM schema_migrations
WHERE version_key='291_vkpi_kol_pool_raw_fields.sql';
