DELETE FROM vkpi_provider_budget_caps
WHERE scope = 'cron:market_provider_smoke'
  AND metadata_json LIKE '%082_vkpi_market_provider_smoke_budget%';
