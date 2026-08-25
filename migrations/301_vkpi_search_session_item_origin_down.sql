-- 301 down: 撤掉来源列。只删本迁移新增的对象,payload_json 里的 origin/origin_reason
-- 自描述字段刻意保留——回滚代码后历史会话仍能读到来源,重放上行时也不需要再回填一次。
DROP INDEX IF EXISTS idx_vkpi_kol_search_session_items_origin_created;
DROP INDEX IF EXISTS idx_vkpi_kol_search_session_items_session_origin;
ALTER TABLE vkpi_kol_search_session_items
  DROP CONSTRAINT IF EXISTS chk_vkpi_kol_search_session_items_origin;
ALTER TABLE vkpi_kol_search_session_items
  DROP COLUMN IF EXISTS origin;
