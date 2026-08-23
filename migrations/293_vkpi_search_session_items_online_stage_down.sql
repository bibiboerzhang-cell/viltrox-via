-- 293 down: 回到 103 的取值集(先清掉新值的行,否则约束加不回去)
DELETE FROM vkpi_kol_search_session_items WHERE item_type = 'online_qualified_candidate' OR stage = 'qualified';
ALTER TABLE vkpi_kol_search_session_items DROP CONSTRAINT IF EXISTS chk_vkpi_kol_search_session_items_type;
ALTER TABLE vkpi_kol_search_session_items ADD CONSTRAINT chk_vkpi_kol_search_session_items_type
  CHECK (item_type IN ('url_video', 'url_profile', 'recall_candidate', 'existing_kol', 'new_creator', 'unknown'));
ALTER TABLE vkpi_kol_search_session_items DROP CONSTRAINT IF EXISTS chk_vkpi_kol_search_session_items_stage;
ALTER TABLE vkpi_kol_search_session_items ADD CONSTRAINT chk_vkpi_kol_search_session_items_stage
  CHECK (stage IN ('identified', 'profile', 'evidence', 'analysis', 'summary'));
