-- 116(合规硬门禁:PII 离境台账)。改号:原稿 113a → 115。
-- 要求:导出谁/何时/导了谁/发往何处。与既有 vkpi_export_logs(聚合导出,028)互补:
-- 本表逐主体粒度记录「哪个 KOL 的 PII 被导/发往何辖区/何接收方」,支撑跨境合规问询。
-- down 见 115_vkpi_pii_export_ledger_down.sql
CREATE TABLE IF NOT EXISTS vkpi_pii_export_ledger (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,   -- 谁导(导出操作人)
    subject_kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,  -- 导了谁(SET NULL:DSAR 删主体后台账仍留痕)
    subject_handle_snapshot TEXT DEFAULT '',                   -- 删主体后仍可读的快照
    pii_fields_json TEXT NOT NULL DEFAULT '[]',                -- 导了哪些 PII 字段:['email','business_email','links']
    export_channel TEXT NOT NULL,                              -- csv | xlsx | pdf | api | outreach_send
    destination_region TEXT DEFAULT '',                       -- 发往何处(辖区,如 CN/EU/US;留空=待辖区裁决)
    destination_recipient TEXT DEFAULT '',                     -- 接收方(邮箱域/系统/合作方)
    legal_basis TEXT DEFAULT '',
    export_log_id BIGINT REFERENCES vkpi_export_logs(id) ON DELETE SET NULL,  -- 关联聚合导出审计(028,id BIGSERIAL)
    ip TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_pii_ledger_subject ON vkpi_pii_export_ledger(subject_kol_pool_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_pii_ledger_staff ON vkpi_pii_export_ledger(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_pii_ledger_region ON vkpi_pii_export_ledger(destination_region, created_at DESC);
COMMENT ON TABLE vkpi_pii_export_ledger IS 'PII 离境/导出逐主体台账:谁/何时/导了谁/发往何处/何接收方';