-- 306 down: 回滚 persona 词效回填列。载荷可由 per_sku_term_performance 随时重建,
-- 回滚只丢本迁移自己的证据,persona 正文各列一字不动。
ALTER TABLE vkpi_product_persona
  DROP COLUMN IF EXISTS term_performance_json;

DELETE FROM schema_migrations
 WHERE version_key = '306_vkpi_product_persona_term_performance.sql';
