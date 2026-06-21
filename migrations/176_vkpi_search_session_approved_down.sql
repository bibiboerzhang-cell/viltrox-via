-- 176_down — 回滚:移除 approved_kol_ids 列(approval 审计在 result_summary_json 里,随之留存无害)。
BEGIN;
ALTER TABLE vkpi_kol_search_sessions
  DROP COLUMN IF EXISTS approved_kol_ids;
COMMIT;
