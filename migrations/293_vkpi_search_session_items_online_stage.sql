-- 293: 严格 30+30 在线通道写 item_type='online_qualified_candidate' / stage='qualified',
-- 迁移 103 的 CHECK 不含这两个值 -> 每次联网搜索 CheckViolation -> requeue -> 空结果(2026-08-23 prod 会话 1125-1129 全 0)。
-- 扩展两个约束(保留既有值),与 backend/app/domains/kol/search_sessions_online.py 字面同步。
ALTER TABLE vkpi_kol_search_session_items DROP CONSTRAINT IF EXISTS chk_vkpi_kol_search_session_items_type;
ALTER TABLE vkpi_kol_search_session_items ADD CONSTRAINT chk_vkpi_kol_search_session_items_type
  CHECK (item_type IN ('url_video', 'url_profile', 'recall_candidate', 'existing_kol', 'new_creator', 'unknown', 'online_qualified_candidate'));
ALTER TABLE vkpi_kol_search_session_items DROP CONSTRAINT IF EXISTS chk_vkpi_kol_search_session_items_stage;
ALTER TABLE vkpi_kol_search_session_items ADD CONSTRAINT chk_vkpi_kol_search_session_items_stage
  CHECK (stage IN ('identified', 'profile', 'evidence', 'analysis', 'summary', 'qualified'));
