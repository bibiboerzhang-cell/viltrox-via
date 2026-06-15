-- 回滚 153:报告深度分析。
DELETE FROM vkpi_provider_budget_caps WHERE scope = 'dashboard:report_analysis';
DROP INDEX IF EXISTS idx_vkpi_report_analysis_hash;
DROP TABLE IF EXISTS vkpi_report_analysis;
