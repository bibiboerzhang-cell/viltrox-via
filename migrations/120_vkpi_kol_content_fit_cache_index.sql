-- 120(地基B 内容契合深析:content_fit_v1 读路径的查询索引)。
-- 注:119 号已被并行 workflow(w7fress05)的 119_vkpi_product_persona.sql 占用,本迁移顺延 120。
-- 内容契合结果复用 vkpi_analysis_cache 既有表,独立命名空间:
--   target_type='kol' + derive_method='content_fit_v1'(与 viltrox_fit_score 无任何关系;
--   与 outreach_draft 的 target_type='kol_pool'、video 分析的 target_type='video' 互不重叠)。
-- 既有 UNIQUE(target_type,target_id,derive_method) 已保证 upsert 幂等;本迁移仅为
--   「读最新 content_fit_v1」的 get_content_fit() 加一条 partial 索引,纯加速,不改表结构、
--   不加业务列、不动 status CHECK(仍只 ready/stale;insufficient_evidence 不入库)。
-- 红线:additive only;无 down 破坏性变更需求(DROP INDEX 即可回滚,见注释)。
CREATE INDEX IF NOT EXISTS idx_vkpi_analysis_cache_kol_content_fit
  ON vkpi_analysis_cache (target_id, updated_at DESC)
  WHERE target_type = 'kol' AND derive_method = 'content_fit_v1';

COMMENT ON INDEX idx_vkpi_analysis_cache_kol_content_fit IS
  '地基B 内容契合深析(content_fit_v1)读路径索引;独立 kol 命名空间,零触 viltrox_fit_score。回滚:DROP INDEX IF EXISTS idx_vkpi_analysis_cache_kol_content_fit;';
