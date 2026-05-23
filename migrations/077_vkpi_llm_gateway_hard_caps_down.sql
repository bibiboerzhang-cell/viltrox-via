DELETE FROM vkpi_provider_budget_caps
WHERE scope IN ('single_call', 'cron:p4_evidence_summary');
