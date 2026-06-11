-- 106: budget scopes for project-level retrospective aggregation (P5 批3, 闸A 批准 cron=$5 / single=$1)
-- First batch is manual validation only (1-3 projects, KOL>=3); full batch runs need a separate approval.

INSERT INTO vkpi_provider_budget_caps
  (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
  ('single_call_project_retrospective', 1.00, 0, 0.80, 1.00, NULL, 'block_project_retrospective',
   '{"seeded_by":"106_vkpi_project_retrospective_budget","tier":"hard_stop","provider":"llm_gateway","target_types":["project"]}'),
  ('cron:vkpi_project_retrospective', 5.00, 0, 0.80, 1.00, NULL, 'fallback_to_template',
   '{"seeded_by":"106_vkpi_project_retrospective_budget","tier":"cron","provider":"llm_gateway","target_types":["project"]}')
ON CONFLICT (scope) DO NOTHING;
