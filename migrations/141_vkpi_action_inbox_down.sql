-- 回滚 141(W1 Action Inbox 地基)。仅文档/手动用,不自动应用。
-- 先删子表(ledger 引用 inbox),再删父表。additive 表,删除不影响任何业务域。
DROP TABLE IF EXISTS vkpi_action_execution_ledger;
DROP TABLE IF EXISTS vkpi_action_inbox;
