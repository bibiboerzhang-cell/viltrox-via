-- 124 回滚:仅 DROP 本迁移新增的 P1 性能索引,无表/列变更需逆转。幂等。
DROP INDEX IF EXISTS idx_perf_kol_search_sessions_status_updated;
DROP INDEX IF EXISTS idx_perf_analysis_cache_target_method_status;
DROP INDEX IF EXISTS idx_perf_kol_video_evidence_pool_url;
DROP INDEX IF EXISTS idx_perf_shipments_project_delivered_status;
DROP INDEX IF EXISTS idx_perf_kol_pool_country_platform_views;
