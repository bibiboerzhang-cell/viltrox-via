-- 回滚 146(T4-keypool 多账号 API key 池)。仅文档/手动用,不自动应用。
-- additive 存储表,无下游引用(rotation worker 未接线、无其他域 FK 指向它)。
-- 删除不影响任何业务域;落库的仅为 Fernet 密文/占位,删除即彻底清除。
DROP TABLE IF EXISTS vkpi_api_key_pool;
