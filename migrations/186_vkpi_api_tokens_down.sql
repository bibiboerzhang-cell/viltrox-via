-- 回滚 186(I1 API Token Broker 调度位)。仅文档/手动用,不自动应用。
-- additive 存储表,无下游引用(无其他域 FK 指向它、调度器 job 可选未必接线)。
-- 删除不影响任何业务域;表内仅 label 引用名与配额记账,无密钥明文,删除即彻底清除。
DROP TABLE IF EXISTS vkpi_api_tokens;
