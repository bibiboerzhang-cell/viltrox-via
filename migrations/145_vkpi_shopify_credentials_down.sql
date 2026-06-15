-- 回滚 145(T1 Shopify creds-ready 管子)。仅文档/手动用,不自动应用。
-- additive 表,删除不影响任何业务域(评分/rule_v0 物理隔离)。
DROP TABLE IF EXISTS vkpi_event_discount_codes;
DROP TABLE IF EXISTS vkpi_shopify_credentials;
