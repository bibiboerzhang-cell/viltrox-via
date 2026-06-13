-- 117(合规硬门禁:DSAR 工单 + 删除审计)。改号:原稿 113b → 116。
-- 一个 KOL 散落 ≥8 处:vkpi_kol_pool / vkpi_kol_pool_contacts / vkpi_kol_video_evidence /
--   vkpi_kol_profile_deep / vkpi_analysis_cache / vkpi_kol_profile_index_entries(+Qdrant point) /
--   vkpi_kol_llm_deep_analysis_results / R2 资产。其中 profile_deep 无 FK(070)、analysis_cache 用
--   target_type/target_id 字符串键无 FK(096)、Qdrant/R2 为外部系统 —— 不随行删级联,需应用层显式删(见 dsar_erasure.py)。
-- 本表是工单 + 删除收据(可证明删了哪些表多少行 / 外部 point/asset id),供合规问询。
-- down 见 116_vkpi_dsar_requests_down.sql
CREATE TABLE IF NOT EXISTS vkpi_dsar_requests (
    id BIGSERIAL PRIMARY KEY,
    request_type TEXT NOT NULL DEFAULT 'erasure',   -- erasure | access | rectification
    subject_kol_pool_id BIGINT,                     -- 不设 FK:删主体后工单仍需存在
    subject_handle_snapshot TEXT DEFAULT '',
    subject_platform_snapshot TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',          -- pending | approved | executing | done | rejected
    requested_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    approved_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    jurisdiction TEXT DEFAULT '',                    -- 辖区(需 Jianbo 拍板默认值)
    erasure_receipt_json TEXT NOT NULL DEFAULT '{}', -- {table: rows_deleted, qdrant_points:[...], r2_keys:[...]}
    note TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ,
    CONSTRAINT chk_vkpi_dsar_status CHECK (status IN ('pending','approved','executing','done','rejected')),
    CONSTRAINT chk_vkpi_dsar_type CHECK (request_type IN ('erasure','access','rectification'))
);
CREATE INDEX IF NOT EXISTS idx_vkpi_dsar_status ON vkpi_dsar_requests(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_dsar_subject ON vkpi_dsar_requests(subject_kol_pool_id);
COMMENT ON TABLE vkpi_dsar_requests IS 'DSAR 工单 + 主体级联删除收据(覆盖无 FK/外部系统的 4 类删除缺口)';