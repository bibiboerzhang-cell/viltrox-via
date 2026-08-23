-- 回滚 292:删除两条 agent 预算 scope 种子(仅删本迁移种的行,运维手改过的行不动)。

DELETE FROM vkpi_provider_budget_caps
WHERE scope IN ('agent_skill', 'agent_alert_explain')
  AND strpos(metadata_json, 'migration_292') > 0;

DELETE FROM schema_migrations
WHERE version_key='292_vkpi_agent_llm_budget_scopes.sql';
