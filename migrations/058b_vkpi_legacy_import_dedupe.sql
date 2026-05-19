-- V-KPI P2B legacy import dedupe guard.
-- Prevent active duplicate staging batches for the same source workbook hash.

CREATE UNIQUE INDEX IF NOT EXISTS uniq_legacy_batch_active_file_hash
  ON vkpi_legacy_import_batches (source_file_sha256)
  WHERE status IN ('staging', 'staged', 'committing', 'committed');
