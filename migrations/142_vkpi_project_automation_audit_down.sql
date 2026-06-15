-- 回滚 142(W2 Projects 自动化审计)。仅文档/手动用,不自动应用。
-- additive 审计表,删除不影响任何业务域(无 FK、无下游引用)。
DROP TABLE IF EXISTS vkpi_project_automation_audit;
