-- 124(P1 性能索引:两份审计均标记的热读路径缺失复合索引)。纯加速、additive only、幂等。
-- 红线:零触 viltrox_fit_score / rule_v0 / KOL Pool 评分;无表/列变更,仅 CREATE INDEX IF NOT EXISTS。
-- 回滚见 124_perf_indexes_down.sql(逐条 DROP INDEX IF EXISTS)。
--
-- 建库前已核对 information_schema + 现存 pg_indexes,按「列存在 + 非冗余」逐条决策:
--
--  [跳过] apify_jobs(status, run_after, created_at):列 run_after 不存在(poller 真列为 next_retry_at),
--         且 idx_apify_jobs_status_next_retry(status, next_retry_at, created_at) WHERE status='queued'
--         已精确覆盖 poller 热路径(apify_jobs_worker.py:3339-3343)。重复,跳过。
--  [跳过] vkpi_kol_search_session_items(session_id):idx_vkpi_kol_search_session_items_session_rank
--         (session_id, rank, id) 的最左前缀已覆盖 session_id 等值/范围,复合冗余。跳过。
--  [跳过] vkpi_project_deliverables(project_id, kol_pool_id, published_at):列 kol_pool_id、
--         published_at 均不存在(该表只有 project_id/delivered_at/status/...)。列缺失,跳过。
--
--  [建] vkpi_kol_search_sessions(status, updated_at DESC):现存仅 (status, created_at DESC),
--       审计热路径按 updated_at 排序,不同列,新增。
--  [建] vkpi_analysis_cache(target_type, target_id, derive_method, status):现存 UNIQUE
--       (target_type, target_id, derive_method) 不含 status;本索引把 status 作为覆盖列,
--       服务「按 status 过滤的最新 derive_method」读路径,非冗余。
--  [建] vkpi_kol_video_evidence(kol_pool_id, content_url):现存 (kol_pool_id) 与 UNIQUE(content_url)
--       分立,无此复合;新增服务「某 kol 下按 content_url 命中」热查。
--  [建] vkpi_shipments(project_id, delivered_at, status):现存仅 (project_id, created_at DESC),
--       不同列,新增服务按 delivered_at/status 的物流读路径。
--  [建] vkpi_kol_pool(country, platform, avg_views):无匹配索引,新增服务地区/平台筛选 + avg_views 排序。

CREATE INDEX IF NOT EXISTS idx_perf_kol_search_sessions_status_updated
  ON vkpi_kol_search_sessions (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_perf_analysis_cache_target_method_status
  ON vkpi_analysis_cache (target_type, target_id, derive_method, status);

CREATE INDEX IF NOT EXISTS idx_perf_kol_video_evidence_pool_url
  ON vkpi_kol_video_evidence (kol_pool_id, content_url);

CREATE INDEX IF NOT EXISTS idx_perf_shipments_project_delivered_status
  ON vkpi_shipments (project_id, delivered_at, status);

CREATE INDEX IF NOT EXISTS idx_perf_kol_pool_country_platform_views
  ON vkpi_kol_pool (country, platform, avg_views);

COMMENT ON INDEX idx_perf_kol_search_sessions_status_updated IS
  'P1 perf: KOL 搜索会话列表读路径(status + updated_at DESC)。回滚:DROP INDEX IF EXISTS idx_perf_kol_search_sessions_status_updated;';
COMMENT ON INDEX idx_perf_analysis_cache_target_method_status IS
  'P1 perf: analysis_cache 按 status 过滤的目标/方法读路径(覆盖列 status)。回滚:DROP INDEX IF EXISTS idx_perf_analysis_cache_target_method_status;';
COMMENT ON INDEX idx_perf_kol_video_evidence_pool_url IS
  'P1 perf: 某 kol 下按 content_url 命中证据。回滚:DROP INDEX IF EXISTS idx_perf_kol_video_evidence_pool_url;';
COMMENT ON INDEX idx_perf_shipments_project_delivered_status IS
  'P1 perf: 项目物流按 delivered_at/status 读路径。回滚:DROP INDEX IF EXISTS idx_perf_shipments_project_delivered_status;';
COMMENT ON INDEX idx_perf_kol_pool_country_platform_views IS
  'P1 perf: KOL Pool 地区/平台筛选 + avg_views 排序;零触评分列。回滚:DROP INDEX IF EXISTS idx_perf_kol_pool_country_platform_views;';
