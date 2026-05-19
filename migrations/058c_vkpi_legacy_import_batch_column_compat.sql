-- V-KPI P2B legacy import schema compatibility patch.
-- Early local runs of 058 used batch_id on staging/audit tables. The
-- executable P2B schema standardizes on import_batch_id.

DO $$
DECLARE
  target_table TEXT;
BEGIN
  FOREACH target_table IN ARRAY ARRAY[
    'vkpi_legacy_kol_profiles_staging',
    'vkpi_legacy_cooperations_staging',
    'vkpi_legacy_official_content_staging',
    'vkpi_legacy_product_costs_staging',
    'vkpi_legacy_risk_watchlist_staging',
    'vkpi_legacy_voc_alerts_staging',
    'vkpi_legacy_import_review_queue',
    'vkpi_legacy_import_logs',
    'vkpi_legacy_import_committed_refs'
  ]
  LOOP
    IF EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = target_table
        AND column_name = 'batch_id'
    ) AND NOT EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = target_table
        AND column_name = 'import_batch_id'
    ) THEN
      EXECUTE format('ALTER TABLE %I RENAME COLUMN batch_id TO import_batch_id', target_table);
    END IF;
  END LOOP;
END $$;
