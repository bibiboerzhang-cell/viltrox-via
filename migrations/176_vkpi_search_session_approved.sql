-- 176_vkpi_search_session_approved.sql — R1:找人引擎闭合第一刀。
-- 给 KOL 搜索会话加「批准锁定候选」字段:运营在 smart-search 结果里勾选要推进合作的 KOL,
-- POST /kol-search-sessions/{id}/approve 把这批 kol_pool_id 锁进 approved_kol_ids(R2 据此自动建项目草案)。
-- additive、幂等、零触 viltrox_fit_score / rule_v0:只新增一列,不动既有列与评分域。
-- approve 的「谁/何时/几个」审计落 result_summary_json.approval(沿用既有 jsonb 合并路径,不再加列)。
BEGIN;
ALTER TABLE vkpi_kol_search_sessions
  ADD COLUMN IF NOT EXISTS approved_kol_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN vkpi_kol_search_sessions.approved_kol_ids IS
  '运营人审锁定的候选 kol_pool_id 数组(R1);R2 建项目草案的选人来源。只读信号,绝不参与 V6 Fit 评分。';
COMMIT;
