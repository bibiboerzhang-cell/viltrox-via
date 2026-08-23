DELETE FROM scheduler_tasks WHERE task_key IN ('vkpi_pool_raw_fields_backfill', 'vkpi_tracking_auto_enroll', 'vkpi_lens_evidence_backfill');
