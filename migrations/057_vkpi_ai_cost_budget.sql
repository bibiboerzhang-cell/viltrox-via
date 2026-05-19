CREATE TABLE IF NOT EXISTS vkpi_ai_cost_ledger (
  id BIGSERIAL PRIMARY KEY,
  cron_task TEXT,
  ai_provider TEXT,
  model_name TEXT,
  cost_usd NUMERIC(10,4),
  tokens_in INT,
  tokens_out INT,
  kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
  staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  task_item_id BIGINT REFERENCES vkpi_async_task_items(id) ON DELETE SET NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_ai_cost_ledger_time
  ON vkpi_ai_cost_ledger (occurred_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_ai_cost_ledger_cron_time
  ON vkpi_ai_cost_ledger (cron_task, occurred_at);

CREATE TABLE IF NOT EXISTS vkpi_provider_budget_caps (
  scope TEXT PRIMARY KEY,
  cap_usd NUMERIC(10,2),
  current_spend NUMERIC(10,4) DEFAULT 0,
  warning_at NUMERIC(3,2) DEFAULT 0.80,
  hard_stop_at NUMERIC(3,2) DEFAULT 1.00,
  reset_at TIMESTAMPTZ,
  fallback_action TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
