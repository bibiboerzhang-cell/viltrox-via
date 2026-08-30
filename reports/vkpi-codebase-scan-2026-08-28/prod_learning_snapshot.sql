-- Read-only count queries for the 20260828T173053Z production-sync snapshot.
-- Restore into an isolated review database before running; never run mutations.

SELECT 'vkpi_kol_recommendations' AS relation, COUNT(*) AS row_count FROM public.vkpi_kol_recommendations
UNION ALL SELECT 'vkpi_recommendation_outcomes', COUNT(*) FROM public.vkpi_recommendation_outcomes
UNION ALL SELECT 'vkpi_recommendation_feedback', COUNT(*) FROM public.vkpi_recommendation_feedback
UNION ALL SELECT 'vkpi_recommendation_feature_snapshot', COUNT(*) FROM public.vkpi_recommendation_feature_snapshot
UNION ALL SELECT 'vkpi_recommendation_rerank_model', COUNT(*) FROM public.vkpi_recommendation_rerank_model
UNION ALL SELECT 'vkpi_training_exports', COUNT(*) FROM public.vkpi_training_exports
UNION ALL SELECT 'vkpi_gtm_outcomes', COUNT(*) FROM public.vkpi_gtm_outcomes
UNION ALL SELECT 'vkpi_eval_runs', COUNT(*) FROM public.vkpi_eval_runs
UNION ALL SELECT 'vkpi_eval_results', COUNT(*) FROM public.vkpi_eval_results
UNION ALL SELECT 'vkpi_scoring_experiments', COUNT(*) FROM public.vkpi_scoring_experiments
UNION ALL SELECT 'vkpi_bandit_arms', COUNT(*) FROM public.vkpi_bandit_arms
UNION ALL SELECT 'vkpi_skill_runs', COUNT(*) FROM public.vkpi_skill_runs;
