-- 301: 会话项来源列。搜索结果必须一眼看出「哪些人是自有库里捞的、哪些是本次现场
-- 从平台上新找到的」,而此前 vkpi_kol_search_session_items 根本没有来源列——读端只能
-- 靠 payload 里零散的 origin_lane / source 猜,占比最大的 recall_candidate
-- (线上 1401 条)一条都没有标记。
--
-- 取值与 backend/app/domains/kol/search_sessions_item_origin.py 的 ITEM_ORIGIN_VALUES
-- 字面同步(迁移 293 的教训:字面量与 CHECK 漂移会让整条通道 CheckViolation)。
--   local_pool   自有 KOL 库召回(recall_candidate / existing_kol)
--   online_new   本次搜索现场从平台发现(online_qualified_candidate / new_creator)
--   operator_url 操作员贴 URL 建档(url_profile / url_video)
--   unknown      看过证据仍判不出,诚实标未知(绝不猜)
-- 允许 NULL:迁移只加列,存量行由 scripts/ops/backfill_item_origin.py 人工回填;
-- NULL 在汇总里独立报成「尚未标注」,不会被伪装成 unknown。
ALTER TABLE vkpi_kol_search_session_items
  ADD COLUMN IF NOT EXISTS origin TEXT NULL;

ALTER TABLE vkpi_kol_search_session_items
  DROP CONSTRAINT IF EXISTS chk_vkpi_kol_search_session_items_origin;
ALTER TABLE vkpi_kol_search_session_items
  ADD CONSTRAINT chk_vkpi_kol_search_session_items_origin
  CHECK (origin IS NULL OR origin IN ('local_pool', 'online_new', 'operator_url', 'unknown'));

-- 会话汇总按 (session_id, origin) 做 GROUP BY,单会话取数走这条索引。
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_session_items_session_origin
  ON vkpi_kol_search_session_items(session_id, origin);

-- 全库按来源统计(运营口径:近 N 天现场新发现了多少人)走这条。
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_session_items_origin_created
  ON vkpi_kol_search_session_items(origin, created_at DESC)
  WHERE origin IS NOT NULL;
