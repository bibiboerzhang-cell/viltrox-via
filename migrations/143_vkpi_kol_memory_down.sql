-- 回滚 143(W3 KOL 长期记忆地基)。仅文档/手动用,不自动应用。
-- additive 表,删除不影响任何业务域(评分/rule_v0 物理隔离)。
DROP TABLE IF EXISTS vkpi_kol_lifecycle_events;
DROP TABLE IF EXISTS vkpi_kol_memory_snapshots;
