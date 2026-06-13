-- 118(P0-1 邮箱二期:读端联系展开审计 hook 的台账列 + 索引)。
-- 仅为 view_kol_contact 二次确认审计提供查询索引;真值展开走 vkpi_sensitive_access_logs(audit/service.py),
-- 此处补一张轻量物化视图友好的 contact reveal 计数列,便于合规问询『谁展开过哪个 KOL 的真值联系』。
-- 红线:不新增 fit/contact 业务列到 vkpi_kol_pool;只加审计可观测性。down 见 118_vkpi_kol_pool_contact_audit_down.sql
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_reveal_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_last_revealed_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_last_revealed_by_staff_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_contact_reveal ON vkpi_kol_pool(contact_last_revealed_at DESC) WHERE contact_reveal_count > 0;
COMMENT ON COLUMN vkpi_kol_pool.contact_reveal_count IS 'P0-1 二期:真值联系被 view_kol_contact 展开次数(合规审计可观测);非业务/排序字段';