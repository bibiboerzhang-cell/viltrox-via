-- Only remove the row if it was seeded by this migration (do not delete a
-- single_call row that a different seeder owns).
DELETE FROM vkpi_provider_budget_caps
WHERE scope = 'single_call'
  AND metadata_json::text LIKE '%134_vkpi_single_call_budget_reseed%';
