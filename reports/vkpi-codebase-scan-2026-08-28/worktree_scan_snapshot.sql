-- Runnable, reviewed snapshot projection for the current-worktree static scan.
-- The scanner methodology and exclusions are documented in scan-methodology.md.

SELECT *
FROM (VALUES
  ('main_engineering', 4699, 1142341, 44775674, 46778865, 4342254),
  ('executable_test_style_schema', 4572, 1092211, 42906904, 44899213, 3746417),
  ('all_workspace_with_ignored_auxiliary', 4714, 1148592, 44939564, 46951611, 4358542)
) AS scan_scope(scope, files, physical_lines, utf8_characters, bytes, token_proxy);

SELECT *
FROM (VALUES
  ('backend_product', 1536, 444271, 0.4068),
  ('tests', 1030, 271414, 0.2485),
  ('frontend_product', 928, 210366, 0.1926),
  ('operations_scripts', 513, 138123, 0.1265),
  ('sql_migrations', 518, 18775, 0.0172),
  ('other_tools', 47, 9262, 0.0085)
) AS code_area(area, files, physical_lines, share_of_source_lines);

SELECT *
FROM (VALUES
  ('deterministic_reproducible', 18),
  ('heuristic_rules', 11),
  ('concurrency_scheduling_control', 8),
  ('model_llm_trainable', 7)
) AS algorithm_family(category, family_count);
