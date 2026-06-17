-- 回滚 162(D1 GOAFFPRO 接入骨架 creds 管子)。仅文档/手动用,不自动应用。
-- additive 表,删除不影响任何业务域(评分/rule_v0 物理隔离;现有自建 Links 本刀未触碰)。
DROP TABLE IF EXISTS vkpi_goaffpro_credentials;
