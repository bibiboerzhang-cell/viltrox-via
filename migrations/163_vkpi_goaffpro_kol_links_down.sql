-- 回滚 163(GOAFFPRO D2:KOL↔affiliate 映射 + 已同步销售落账)。仅文档/手动用,不自动应用。
-- additive 表,删除不影响任何业务域(评分/rule_v0 物理隔离;creds 表 162 与现有自建 Links 不触碰)。
DROP TABLE IF EXISTS vkpi_goaffpro_sales;
DROP TABLE IF EXISTS vkpi_goaffpro_kol_links;
