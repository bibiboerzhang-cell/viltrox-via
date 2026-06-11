DELETE FROM vkpi_provider_budget_caps
WHERE scope IN ('single_call_project_retrospective', 'cron:vkpi_project_retrospective');
