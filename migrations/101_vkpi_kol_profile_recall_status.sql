CREATE TABLE IF NOT EXISTS vkpi_kol_profile_recall_status (
  kol_pool_id BIGINT PRIMARY KEY REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('recallable', 'suspect', 'pending_data', 'empty')),
  status_reason TEXT NOT NULL,
  status_method TEXT NOT NULL DEFAULT 'kol_recall_status_v1',
  scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_profile_recall_status_status
  ON vkpi_kol_profile_recall_status(status);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_profile_recall_status_scanned_at
  ON vkpi_kol_profile_recall_status(scanned_at DESC);
